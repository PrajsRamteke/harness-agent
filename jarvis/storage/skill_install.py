"""Install skills from a link — no git needed for the common cases.

Understood sources:

* GitHub: ``https://github.com/o/r``, ``…/tree/<ref>/<folder>``, ``…/blob/<ref>/<folder>/SKILL.md``,
  ``o/r``, ``o/r/folder``, ``o/r@skill-name``, ``raw.githubusercontent.com/…/SKILL.md``
* ``https://skills.sh/<owner>/<repo>/<skill>`` and pasted ``npx skills add …`` lines
* a direct link to a ``SKILL.md``, or to a ``.zip`` / ``.skill`` / ``.tar.gz`` archive
* any other git URL (cloned shallowly) and local folders

A skill is a folder with a ``SKILL.md``. ``inspect_source`` lists what a link
holds; ``install_skills`` copies the chosen ones into the **project**
(``.harness/skills``) or **global** (``~/.harness/skills``) tree.

Everything downloaded is treated as untrusted: archives are unpacked with path,
link, size and count checks, and nothing is ever executed.
"""
from __future__ import annotations

import io
import json
import os
import pathlib
import re
import shutil
import subprocess
import tarfile
import tempfile
import threading
import time
import urllib.parse
import zipfile
from typing import Any

from .. import state
from ..constants import HARNESS_SKILLS_DIR, PROJECT_SKILLS_DIRNAME
from . import skills as sk

SCOPES = ("project", "global")
PROVENANCE_FILE = ".jarvis-source.json"

_MAX_DOWNLOAD = 100 * 1024 * 1024
_MAX_FILE = 8 * 1024 * 1024
_MAX_TOTAL = 150 * 1024 * 1024
_MAX_FILES = 5000
_SKIP_DIRS = {".git", "node_modules", "__pycache__", ".venv", ".idea", ".vscode"}
_CACHE_TTL = 300.0
_NAME_RE = re.compile(r"^[a-z0-9]+(-[a-z0-9]+)*$")


class SkillSourceError(ValueError):
    """The link can't be read as a skill source. The message says why."""


# ── source parsing ────────────────────────────────────────────────────────


def _clean(text: str) -> tuple[str, str]:
    """Strip an ``npx skills add …`` wrapper. Returns ``(source, wanted_skill)``."""
    raw = (text or "").strip().strip("`").strip()
    wanted = ""
    if re.match(r"^(npx|bunx|pnpm dlx|pnpx)\s+", raw) or raw.startswith(("skills add", "claude skills add")):
        toks = raw.split()
        rest: list[str] = []
        i = 0
        while i < len(toks):
            t = toks[i]
            if t in ("--skill", "-s") and i + 1 < len(toks):
                wanted = toks[i + 1]
                i += 2
                continue
            if t.startswith("--skill="):
                wanted = t.split("=", 1)[1]
            elif not t.startswith("-"):
                rest.append(t)
            i += 1
        for j, t in enumerate(rest):
            if t == "add" and j + 1 < len(rest):
                return rest[j + 1], wanted
        return (rest[-1] if rest else ""), wanted
    return raw, wanted


def parse_source(text: str) -> dict[str, Any]:
    """``{"kind": github|file|archive|git|local, …, "label": str, "skill": wanted-name-or-""}``."""
    raw, wanted = _clean(text)
    if not raw:
        raise SkillSourceError("Paste a GitHub link, a SKILL.md link, an archive link or a folder path.")

    m = re.match(r"^https?://skills\.sh/([^/\s]+)/([^/\s]+)(?:/([^/\s?#]+))?", raw)
    if m:
        return {"kind": "github", "owner": m[1], "repo": m[2], "ref": "HEAD", "path": "", "skill": m[3] or wanted,
                "label": f"{m[1]}/{m[2]}"}

    if re.match(r"^https?://", raw):
        u = urllib.parse.urlparse(raw)
        host = (u.hostname or "").lower()
        parts = [urllib.parse.unquote(p) for p in u.path.split("/") if p]
        if host == "raw.githubusercontent.com" and len(parts) >= 4:
            path = "/".join(parts[3:])
            folder = path.rsplit("/", 1)[0] if path.lower().endswith("skill.md") and "/" in path else ""
            return {"kind": "github", "owner": parts[0], "repo": parts[1], "ref": parts[2], "path": folder,
                    "skill": wanted, "label": f"{parts[0]}/{parts[1]}"}
        if host in ("github.com", "www.github.com"):
            if len(parts) < 2:
                raise SkillSourceError("That GitHub link needs an owner and a repository: github.com/owner/repo.")
            owner, repo = parts[0], re.sub(r"\.git$", "", parts[1])
            ref, path = "HEAD", ""
            if len(parts) >= 4 and parts[2] in ("tree", "blob"):
                ref = parts[3]
                path = "/".join(parts[4:])
                if parts[2] == "blob" and path.lower().endswith("skill.md"):
                    path = path.rsplit("/", 1)[0] if "/" in path else ""
            return {"kind": "github", "owner": owner, "repo": repo, "ref": ref, "path": path.strip("/"),
                    "skill": wanted, "label": f"{owner}/{repo}" + (f"/{path.strip('/')}" if path.strip("/") else "")}
        low = u.path.lower()
        if low.endswith((".zip", ".skill", ".tar.gz", ".tgz")):
            return {"kind": "archive", "url": raw, "skill": wanted, "label": raw}
        if low.endswith(".md"):
            return {"kind": "file", "url": raw, "skill": wanted, "label": raw}
        if low.endswith(".git") or host in ("gitlab.com", "bitbucket.org", "codeberg.org"):
            return {"kind": "git", "url": raw, "skill": wanted, "label": raw}
        raise SkillSourceError(
            "I can read GitHub links, links to a SKILL.md, .zip/.tar.gz archives and git repositories — "
            "that link is none of those."
        )

    if raw.startswith(("git@", "ssh://", "git://")):
        return {"kind": "git", "url": raw, "skill": wanted, "label": raw}

    expanded = pathlib.Path(raw).expanduser()
    if raw.startswith(("~", "/", "./", "../")) or expanded.exists():
        if not expanded.exists():
            raise SkillSourceError(f"No such folder: {expanded}")
        return {"kind": "local", "path": str(expanded.resolve()), "skill": wanted, "label": str(expanded)}

    # owner/repo, owner/repo/folder, owner/repo@skill
    m = re.match(r"^([A-Za-z0-9][\w.-]*)/([A-Za-z0-9][\w.-]*)(?:@([\w.-]+))?(?:/(.+))?$", raw)
    if m:
        return {"kind": "github", "owner": m[1], "repo": m[2], "ref": "HEAD", "path": (m[4] or "").strip("/"),
                "skill": m[3] or wanted, "label": f"{m[1]}/{m[2]}" + (f"/{m[4]}" if m[4] else "")}
    raise SkillSourceError(
        "Not sure what that is. Give me a GitHub link (or owner/repo), a link to a SKILL.md, an archive, or a folder path."
    )


# ── fetching ──────────────────────────────────────────────────────────────

_cache: dict[str, tuple[float, pathlib.Path]] = {}
_cache_lock = threading.Lock()


def _prune_cache() -> None:
    now = time.monotonic()
    with _cache_lock:
        for key in [k for k, (t, _p) in _cache.items() if now - t > _CACHE_TTL]:
            shutil.rmtree(_cache.pop(key)[1], ignore_errors=True)


def _http_get(url: str, *, limit: int = _MAX_DOWNLOAD) -> bytes:
    import httpx

    headers = {"User-Agent": "jarvis-skill-install"}
    token = os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN")
    if token and urllib.parse.urlparse(url).hostname in ("codeload.github.com", "raw.githubusercontent.com", "github.com"):
        headers["Authorization"] = f"Bearer {token}"
    buf = io.BytesIO()
    try:
        with httpx.stream("GET", url, headers=headers, timeout=30, follow_redirects=True) as r:
            if r.status_code == 404:
                raise SkillSourceError("Not found (404). Check the link — or, for a private repository, set GITHUB_TOKEN.")
            if r.status_code in (401, 403):
                raise SkillSourceError(f"Access refused ({r.status_code}). For a private repository set GITHUB_TOKEN.")
            r.raise_for_status()
            for chunk in r.iter_bytes():
                buf.write(chunk)
                if buf.tell() > limit:
                    raise SkillSourceError("That download is too large to be a skill (over 100 MB).")
    except httpx.HTTPStatusError as exc:
        raise SkillSourceError(f"The server answered {exc.response.status_code}.") from exc
    except httpx.HTTPError as exc:
        raise SkillSourceError(f"Couldn't download it ({exc.__class__.__name__}). Check the link and your connection.") from exc
    return buf.getvalue()


class _Budget:
    def __init__(self) -> None:
        self.files = 0
        self.total = 0

    def take(self, size: int) -> bool:
        if size > _MAX_FILE:
            return False
        self.files += 1
        self.total += size
        if self.files > _MAX_FILES or self.total > _MAX_TOTAL:
            raise SkillSourceError("That archive holds too many files to be a skill.")
        return True


def _safe_join(root: pathlib.Path, rel: str) -> pathlib.Path | None:
    rel = rel.replace("\\", "/").lstrip("/")
    if not rel or any(p in ("..", "") for p in rel.split("/")) or re.match(r"^[A-Za-z]:", rel):
        return None
    target = (root / rel).resolve()
    try:
        target.relative_to(root.resolve())
    except ValueError:
        return None
    if any(p in _SKIP_DIRS for p in rel.split("/")):
        return None
    return target


def _unpack_tar(data: bytes, dest: pathlib.Path, subpath: str) -> None:
    budget = _Budget()
    prefix = subpath.strip("/")
    try:
        tf = tarfile.open(fileobj=io.BytesIO(data), mode="r:*")
    except tarfile.TarError as exc:
        raise SkillSourceError(f"That isn't a readable archive ({exc}).") from exc
    with tf:
        for member in tf:
            if not member.isfile():
                continue  # directories, links, devices: never
            parts = member.name.replace("\\", "/").split("/", 1)
            rel = parts[1] if len(parts) == 2 else ""
            if not rel or (prefix and not (rel == prefix or rel.startswith(prefix + "/"))):
                continue
            target = _safe_join(dest, rel)
            if target is None or not budget.take(member.size):
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            src = tf.extractfile(member)
            if src is not None:
                with src, open(target, "wb") as out:
                    shutil.copyfileobj(src, out)
                if member.mode & 0o111:
                    os.chmod(target, 0o755)


def _unpack_zip(data: bytes, dest: pathlib.Path) -> None:
    budget = _Budget()
    try:
        zf = zipfile.ZipFile(io.BytesIO(data))
    except zipfile.BadZipFile as exc:
        raise SkillSourceError("That isn't a readable zip archive.") from exc
    with zf:
        names = [i for i in zf.infolist() if not i.is_dir()]
        # A single top folder is just the archive's wrapper — drop it.
        tops = {i.filename.replace("\\", "/").split("/", 1)[0] for i in names if "/" in i.filename}
        strip = len(tops) == 1 and all("/" in i.filename for i in names)
        for info in names:
            if (info.external_attr >> 28) == 0xA:  # symlink
                continue
            rel = info.filename.replace("\\", "/")
            if strip:
                rel = rel.split("/", 1)[1]
            target = _safe_join(dest, rel)
            if target is None or not budget.take(info.file_size):
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            with zf.open(info) as src, open(target, "wb") as out:
                shutil.copyfileobj(src, out)


def _clone(url: str, dest: pathlib.Path) -> None:
    if shutil.which("git") is None:
        raise SkillSourceError("git isn't installed, so I can't fetch that repository. Use a GitHub link instead.")
    env = dict(os.environ, GIT_TERMINAL_PROMPT="0")
    try:
        r = subprocess.run(
            ["git", "clone", "--depth", "1", "--quiet", url, str(dest)],
            capture_output=True, text=True, timeout=120, env=env,
        )
    except subprocess.TimeoutExpired as exc:
        raise SkillSourceError("Cloning took too long (over 2 minutes).") from exc
    if r.returncode != 0:
        raise SkillSourceError("git couldn't clone it: " + (r.stderr.strip().splitlines() or ["unknown error"])[-1])
    shutil.rmtree(dest / ".git", ignore_errors=True)


def _fetch_github(src: dict[str, Any], dest: pathlib.Path) -> None:
    owner, repo, ref, path = src["owner"], src["repo"], src["ref"], src.get("path", "")
    data = _http_get(f"https://codeload.github.com/{owner}/{repo}/tar.gz/{ref}")
    _unpack_tar(data, dest, path if ref != "HEAD" or path else "")


def fetch(src: dict[str, Any]) -> pathlib.Path:
    """A folder holding the source's files (cached for a few minutes so inspect → install downloads once)."""
    _prune_cache()
    key = json.dumps({k: v for k, v in src.items() if k not in ("skill", "label")}, sort_keys=True)
    if src["kind"] == "local":
        return pathlib.Path(src["path"])
    with _cache_lock:
        hit = _cache.get(key)
    if hit and hit[1].exists():
        return hit[1]
    dest = pathlib.Path(tempfile.mkdtemp(prefix="jarvis-skill-"))
    try:
        kind = src["kind"]
        if kind == "github":
            _fetch_github(src, dest)
        elif kind == "archive":
            data = _http_get(src["url"])
            if src["url"].lower().split("?")[0].endswith((".tar.gz", ".tgz")):
                _unpack_tar(data, dest, "")
            else:
                _unpack_zip(data, dest)
        elif kind == "file":
            data = _http_get(src["url"], limit=2 * 1024 * 1024)
            text = data.decode("utf-8", errors="replace")
            fm = sk._parse_frontmatter(text)
            folder = dest / (_norm_name(fm.get("name", "")) or "skill")
            folder.mkdir(parents=True, exist_ok=True)
            (folder / "SKILL.md").write_text(text, encoding="utf-8")
        elif kind == "git":
            _clone(src["url"], dest / "repo")
        else:
            raise SkillSourceError(f"Unknown source kind {kind!r}.")
    except BaseException:
        shutil.rmtree(dest, ignore_errors=True)
        raise
    with _cache_lock:
        _cache[key] = (time.monotonic(), dest)
    return dest


# ── discovery ─────────────────────────────────────────────────────────────


def _norm_name(raw: str) -> str:
    name = re.sub(r"[^a-z0-9]+", "-", (raw or "").strip().lower()).strip("-")
    return name[:64].strip("-")


def _find_skill_dirs(root: pathlib.Path, max_depth: int = 6) -> list[pathlib.Path]:
    out: list[pathlib.Path] = []

    def walk(d: pathlib.Path, depth: int) -> None:
        if depth > max_depth:
            return
        try:
            entries = sorted(d.iterdir())
        except OSError:
            return
        if any(e.is_file() and e.name.lower() == "skill.md" for e in entries):
            out.append(d)
            return  # a skill's own subfolders are its resources, not more skills
        for e in entries:
            if e.is_dir() and not e.is_symlink() and e.name not in _SKIP_DIRS:
                walk(e, depth + 1)

    walk(root, 0)
    return out


def _skill_md(folder: pathlib.Path) -> pathlib.Path:
    return next(e for e in folder.iterdir() if e.is_file() and e.name.lower() == "skill.md")


def _describe(folder: pathlib.Path, root: pathlib.Path) -> dict[str, Any]:
    text = _skill_md(folder).read_text(encoding="utf-8", errors="replace")
    fm = sk._parse_frontmatter(text)
    raw_name = (fm.get("name") or "").strip()
    desc = (fm.get("description") or "").strip()
    name = _norm_name(raw_name or folder.name)
    problems = []
    if not raw_name:
        problems.append("no name in the SKILL.md header — using the folder name")
    elif raw_name.lower() != name:
        problems.append(f"name “{raw_name}” isn't lowercase-hyphen — will be saved as “{name}”")
    if not desc:
        problems.append("no description")
    elif len(desc) > 1024:
        problems.append("description is over 1024 characters — will be shortened")
    size = sum(f.stat().st_size for f in folder.rglob("*") if f.is_file())
    files = sum(1 for f in folder.rglob("*") if f.is_file())
    rel = "" if folder == root else folder.relative_to(root).as_posix()
    return {
        "name": name,
        "description": desc[:400],
        "folder": rel,
        "files": files,
        "size": size,
        "problems": problems,
        "usable": bool(name and _NAME_RE.match(name) and desc),
    }


def inspect_source(text: str) -> dict[str, Any]:
    """What is in this link? ``{ok, label, skills:[{name, description, …}], wanted}``."""
    try:
        src = parse_source(text)
        root = fetch(src)
        base = root / src["path"] if src.get("path") and src["kind"] == "local" else root
        found = [_describe(f, base) for f in _find_skill_dirs(base)]
    except SkillSourceError as exc:
        return {"ok": False, "error": str(exc)}
    except OSError as exc:
        return {"ok": False, "error": f"Couldn't read it: {exc}"}
    if not found:
        return {"ok": False, "error": "No skills found there — a skill is a folder with a SKILL.md file.",
                "label": src["label"]}
    wanted = _norm_name(src.get("skill") or "")
    if wanted:
        picked = [s for s in found if s["name"] == wanted or s["folder"].rsplit("/", 1)[-1] == wanted]
        if not picked:
            names = ", ".join(s["name"] for s in found[:12])
            return {"ok": False, "error": f"No skill called “{wanted}” there. It has: {names}.", "label": src["label"]}
        found = picked
    installed = {s["name"]: s for s in sk.discover_skills(force=True, include_global=True)}
    for s in found:
        cur = installed.get(s["name"])
        s["installed"] = cur["scope"] if cur else ""
    return {"ok": True, "label": src["label"], "source": src, "skills": found, "wanted": wanted}


# ── install ───────────────────────────────────────────────────────────────


def scope_root(scope: str) -> pathlib.Path:
    if scope == "project":
        return sk._find_project_root() / PROJECT_SKILLS_DIRNAME
    if scope == "global":
        return HARNESS_SKILLS_DIR
    raise ValueError("scope must be 'project' or 'global'")


def ensure_global_visible() -> bool:
    """Turn global skills on if they were hidden. True when this call switched it on."""
    if getattr(state, "global_skills", False):
        return False
    state.global_skills = True
    try:
        state.save_skills_config()
    except Exception:
        pass
    return True


def _patch_header(text: str, name: str, desc: str) -> str:
    """Make the SKILL.md header valid: lowercase-hyphen name, description ≤ 1024."""
    m = re.match(r"^---[ \t]*\n(.*?)\n---[ \t]*(\n|$)", text, re.S)
    if not m:
        return f"---\nname: {name}\ndescription: {desc.splitlines()[0][:1000] if desc else name}\n---\n\n{text}"
    lines = m.group(1).split("\n")
    out: list[str] = []
    skipping = False
    seen_name = seen_desc = False
    for ln in lines:
        key = re.match(r"^(\w[\w.-]*)\s*:", ln)
        if key:
            skipping = False
            if key.group(1) == "name":
                out.append(f"name: {name}")
                seen_name = True
                continue
            if key.group(1) == "description" and (len(desc) > 1024):
                out.append("description: " + json.dumps(desc[:1000].rstrip() + "…"))
                seen_desc = True
                skipping = True
                continue
        elif skipping and ln.startswith((" ", "\t")):
            continue
        out.append(ln)
    if not seen_name:
        out.insert(0, f"name: {name}")
    _ = seen_desc
    return "---\n" + "\n".join(out) + "\n---" + text[m.end() - len(m.group(2)):]


def _copy_skill(src_dir: pathlib.Path, dest: pathlib.Path, info: dict[str, Any]) -> None:
    tmp = dest.with_name(dest.name + ".installing")
    shutil.rmtree(tmp, ignore_errors=True)
    tmp.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(
        src_dir, tmp,
        ignore=shutil.ignore_patterns(*_SKIP_DIRS, ".DS_Store", "*.pyc"),
    )
    md = _skill_md(tmp)
    text = md.read_text(encoding="utf-8", errors="replace")
    fm = sk._parse_frontmatter(text)
    raw_name = (fm.get("name") or "").strip()
    desc = (fm.get("description") or "").strip()
    if raw_name != info["name"] or len(desc) > 1024:
        md.write_text(_patch_header(text, info["name"], desc), encoding="utf-8")
    if md.name != "SKILL.md":
        md.rename(tmp / "SKILL.md")
    if dest.exists():
        shutil.rmtree(dest)
    os.replace(tmp, dest)


def _record_source(dest: pathlib.Path, label: str, src: dict[str, Any]) -> None:
    try:
        (dest / PROVENANCE_FILE).write_text(
            json.dumps({"source": label, "spec": {k: v for k, v in src.items() if k != "label"},
                        "installed_at": int(time.time())}, indent=2),
            encoding="utf-8",
        )
    except OSError:
        pass


def install_skills(
    text: str,
    *,
    scope: str = "global",
    names: list[str] | None = None,
    install_all: bool = False,
    overwrite: bool = False,
    confirm: Any = None,
) -> dict[str, Any]:
    """Copy skills from ``text`` into ``scope``.

    One skill (or one picked with ``names`` / ``…@skill``) installs straight
    away; a source with several and no choice made returns ``needs_choice`` with
    the list, so the caller can ask which. ``confirm(description)`` is asked
    first when the request came from an agent rather than the user's own hands.
    """
    if scope not in SCOPES:
        return {"ok": False, "error": "scope must be 'project' or 'global'"}
    info = inspect_source(text)
    if not info.get("ok"):
        return {"ok": False, "error": info.get("error", "couldn't read that")}
    src = info["source"]
    skills = info["skills"]
    want = {_norm_name(n) for n in (names or []) if n}
    if want:
        missing = sorted(want - {s["name"] for s in skills})
        skills = [s for s in skills if s["name"] in want]
        if not skills:
            have = ", ".join(s["name"] for s in info["skills"][:12])
            return {"ok": False, "error": f"No skill called {', '.join(missing)} there. It has: {have}."}
    elif len(skills) > 1 and not install_all:
        return {
            "ok": False,
            "needs_choice": True,
            "label": info["label"],
            "skills": skills,
            "error": f"{info['label']} has {len(skills)} skills — say which one(s), or ask for all.",
        }
    usable = [s for s in skills if s["usable"]]
    unusable = [s for s in skills if not s["usable"]]
    if not usable:
        return {"ok": False, "error": "Those skills have no usable name/description in their SKILL.md header.",
                "skipped": [{"name": s["name"], "reason": "; ".join(s["problems"])} for s in unusable]}

    if confirm is not None:
        listing = ", ".join(s["name"] for s in usable)
        if not confirm(f"install skill{'s' if len(usable) != 1 else ''} {listing} from {info['label']} ({scope})"):
            return {"ok": False, "denied": True, "error": "You declined installing it."}

    root = fetch(src)
    base = root / src["path"] if src.get("path") and src["kind"] == "local" else root
    folders = {s["name"]: s["folder"] for s in usable}
    dest_root = scope_root(scope)
    installed: list[dict[str, Any]] = []
    skipped: list[dict[str, Any]] = [{"name": s["name"], "reason": "; ".join(s["problems"]) or "unusable"} for s in unusable]
    for s in usable:
        dest = dest_root / s["name"]
        if dest.exists() and not overwrite:
            skipped.append({"name": s["name"], "reason": f"already installed in {scope} (say overwrite to replace it)"})
            continue
        src_dir = (base / folders[s["name"]]) if folders[s["name"]] else base
        try:
            _copy_skill(src_dir, dest, s)
            _record_source(dest, info["label"], src)
        except (OSError, shutil.Error) as exc:
            skipped.append({"name": s["name"], "reason": f"couldn't copy: {exc}"})
            continue
        installed.append({"name": s["name"], "description": s["description"], "scope": scope,
                          "path": str(dest / "SKILL.md"), "files": s["files"], "problems": s["problems"]})
    scope_note = ""
    if installed and scope == "global" and ensure_global_visible():
        scope_note = "Global skills were off — I turned them on so this skill is visible."
    sk.invalidate_cache()
    try:
        from ..repl.system import invalidate_system_cache

        invalidate_system_cache()
    except Exception:
        pass
    out = {
        "ok": bool(installed),
        "installed": installed,
        "skipped": skipped,
        "scope": scope,
        "label": info["label"],
        "scope_note": scope_note,
    }
    if installed:
        out["message"] = "; ".join(f"{i['name']} → {scope}" for i in installed) + (f" ({scope_note})" if scope_note else "")
    else:
        out["error"] = "; ".join(f"{s['name']}: {s['reason']}" for s in skipped) or "nothing installed"
    return out


# ── remove / move / list ──────────────────────────────────────────────────


def _managed_dirs() -> dict[str, pathlib.Path]:
    return {"project": scope_root("project"), "global": scope_root("global")}


def find_installed(name: str, scope: str | None = None) -> dict[str, Any] | None:
    n = _norm_name(name)
    for rec in sk.discover_skills(force=True, include_global=True):
        if rec["name"] == n and (scope is None or rec["scope"] == scope):
            return rec
    return None


def _managed_path(rec: dict[str, Any]) -> pathlib.Path | None:
    """The skill's folder if it lives in a folder Jarvis manages (never touch other tools' folders)."""
    folder = pathlib.Path(rec["path"]).parent
    for scope_dir in _managed_dirs().values():
        try:
            folder.resolve().relative_to(scope_dir.resolve())
            return folder
        except ValueError:
            continue
    return None


def remove_skill(name: str, scope: str | None = None) -> dict[str, Any]:
    rec = find_installed(name, scope)
    if rec is None:
        return {"ok": False, "error": f"No skill named '{name}'" + (f" in {scope}" if scope else "") + "."}
    folder = _managed_path(rec)
    if folder is None:
        return {"ok": False, "error": f"'{rec['name']}' lives in {rec['source_dir']}, which belongs to another tool — remove it there."}
    shutil.rmtree(folder, ignore_errors=True)
    sk.invalidate_cache()
    _invalidate_prompt()
    return {"ok": True, "name": rec["name"], "scope": rec["scope"], "message": f"Removed skill '{rec['name']}' ({rec['scope']})."}


def move_skill(name: str, to_scope: str) -> dict[str, Any]:
    if to_scope not in SCOPES:
        return {"ok": False, "error": "scope must be 'project' or 'global'"}
    rec = find_installed(name)
    if rec is None:
        return {"ok": False, "error": f"No skill named '{name}'."}
    if rec["scope"] == to_scope:
        return {"ok": False, "error": f"'{rec['name']}' is already a {to_scope} skill."}
    folder = _managed_path(rec)
    if folder is None:
        return {"ok": False, "error": f"'{rec['name']}' belongs to another tool's folder — can't move it."}
    dest = scope_root(to_scope) / rec["name"]
    if dest.exists():
        return {"ok": False, "error": f"'{rec['name']}' already exists in {to_scope}."}
    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.move(str(folder), str(dest))
    if to_scope == "global":
        ensure_global_visible()
    sk.invalidate_cache()
    _invalidate_prompt()
    return {"ok": True, "name": rec["name"], "scope": to_scope, "message": f"Moved '{rec['name']}' to {to_scope}."}


def update_skill(name: str) -> dict[str, Any]:
    """Re-fetch a skill from where it came from (recorded when it was installed)."""
    rec = find_installed(name)
    if rec is None:
        return {"ok": False, "error": f"No skill named '{name}'."}
    folder = _managed_path(rec)
    prov = (folder / PROVENANCE_FILE) if folder else None
    if prov is None or not prov.is_file():
        return {"ok": False, "error": f"I don't know where '{rec['name']}' came from — reinstall it from its link."}
    try:
        spec = json.loads(prov.read_text(encoding="utf-8"))
        label = spec["source"]
        src = dict(spec["spec"], label=label)
    except (OSError, ValueError, KeyError):
        return {"ok": False, "error": "The saved source is unreadable — reinstall it from its link."}
    src["skill"] = rec["name"]
    key_text = json.dumps(src)
    _ = key_text
    # Re-run the install from the recorded spec.
    with _cache_lock:
        for k in list(_cache):
            shutil.rmtree(_cache.pop(k)[1], ignore_errors=True)
    text = _spec_to_text(src)
    return install_skills(text, scope=rec["scope"], names=[rec["name"]], overwrite=True)


def _spec_to_text(src: dict[str, Any]) -> str:
    kind = src["kind"]
    if kind == "github":
        tail = f"/tree/{src['ref']}/{src['path']}" if src.get("path") else (f"/tree/{src['ref']}" if src["ref"] != "HEAD" else "")
        return f"https://github.com/{src['owner']}/{src['repo']}{tail}"
    if kind in ("archive", "file", "git"):
        return src["url"]
    return src.get("path", "")


def _invalidate_prompt() -> None:
    try:
        from ..repl.system import invalidate_system_cache

        invalidate_system_cache()
    except Exception:
        pass


def describe_installed(query: str = "") -> dict[str, Any]:
    """Every skill the agent can see, with where it lives and whether Jarvis manages it."""
    q = (query or "").strip().lower()
    rows = []
    visible_global = bool(getattr(state, "global_skills", False))
    for rec in sorted(sk.discover_skills(force=True, include_global=True), key=lambda r: (r.get("scope") != "project", r["name"])):
        if q and q not in rec["name"] and q not in rec["description"].lower():
            continue
        folder = _managed_path(rec)
        origin = ""
        if folder is not None:
            try:
                origin = json.loads((folder / PROVENANCE_FILE).read_text(encoding="utf-8")).get("source", "")
            except (OSError, ValueError):
                origin = ""
        rows.append({
            "name": rec["name"],
            "description": rec["description"],
            "scope": rec["scope"],
            "source_dir": rec["source_dir"],
            "managed": folder is not None,
            "origin": origin,
            "active": rec["scope"] == "project" or visible_global,
        })
    return {
        "skills": rows,
        "global_skills": bool(getattr(state, "global_skills", False)),
        "hidden_global_count": sum(1 for r in rows if not r["active"]),
        "project_dir": str(scope_root("project")),
        "global_dir": str(scope_root("global")),
    }
