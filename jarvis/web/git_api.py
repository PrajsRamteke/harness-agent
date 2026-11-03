"""Git card in the web inspector: open in an editor, switch branch, stash,
commit, push, create a PR, and a model-written commit / PR message.

UI-free. Every git / gh / open call goes through :func:`_run`, so tests swap
in a fake and nothing real is committed or pushed. Runs on the HTTP handler
thread (subprocesses and network), never on the TUI thread; mutations hold
``_lock`` so two clicks can't interleave. Always works in this Jarvis's
folder (``os.getcwd()``) — the page names an operation, never a path.
"""
from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Any

# Bumped when the page needs something new from this module. A page newer
# than its server (Python only reloads when Jarvis restarts) checks it before
# relying on it — e.g. a PR From/Into, which older servers ignored.
GIT_API_VERSION = 3

GIT_TIMEOUT = 20.0
NET_TIMEOUT = 180.0
DIFF_MAX_CHARS = 12_000      # what the model sees of the diff (less = a faster answer)
GEN_CACHE_TTL = 600.0        # same changes, same question → the answer again, at once
UNTRACKED_MAX_FILES = 300    # untracked files whose lines count towards +N
UNTRACKED_MAX_BYTES = 2_000_000
MESSAGE_TIMEOUT = 90.0

_lock = threading.Lock()


class GitError(Exception):
    """Shown to the user as is."""


def _env() -> dict[str, str]:
    env = dict(os.environ)
    # Never wait on a credential / editor prompt nobody can answer.
    env.update(GIT_TERMINAL_PROMPT="0", GIT_EDITOR="true", GIT_OPTIONAL_LOCKS="0", GH_PROMPT_DISABLED="1")
    return env


def _run(args: list[str], cwd: str, timeout: float = GIT_TIMEOUT) -> tuple[int, str, str]:
    """(exit code, stdout, stderr). Tests replace this."""
    try:
        p = subprocess.run(
            args, cwd=cwd, capture_output=True, text=True, timeout=timeout,
            stdin=subprocess.DEVNULL, env=_env(),
        )
    except FileNotFoundError:
        return 127, "", f"{args[0]} is not installed"
    except subprocess.TimeoutExpired:
        return 124, "", f"{' '.join(args[:3])} took longer than {int(timeout)}s"
    return p.returncode, p.stdout, p.stderr


def _git(cwd: str, *args: str, timeout: float = GIT_TIMEOUT) -> tuple[int, str, str]:
    return _run(["git", *args], cwd, timeout)


def _ok(cwd: str, *args: str, timeout: float = GIT_TIMEOUT) -> str:
    code, out, err = _git(cwd, *args, timeout=timeout)
    if code != 0:
        raise GitError(_first_error(err or out) or f"git {args[0]} failed")
    return out


def _first_error(text: str) -> str:
    lines = [ln.strip() for ln in (text or "").splitlines() if ln.strip()]
    for ln in lines:  # git's own "error:" / "fatal:" line says it best
        if ln.lower().startswith(("error:", "fatal:", "! [rejected]", "hint: updates were rejected")):
            return re.sub(r"^(error|fatal):\s*", "", ln, flags=re.I)[:300]
    return (lines[-1] if lines else "")[:300]


# ─── Editors / "Open in" ──────────────────────────────────────────────────

# id, label, macOS app names to look for, CLI on PATH (other systems)
EDITORS: list[tuple[str, str, tuple[str, ...], str]] = [
    ("cursor", "Cursor", ("Cursor",), "cursor"),
    ("vscode", "VS Code", ("Visual Studio Code",), "code"),
    ("antigravity", "Antigravity", ("Antigravity",), "antigravity"),
    ("windsurf", "Windsurf", ("Windsurf",), "windsurf"),
    ("zed", "Zed", ("Zed",), "zed"),
    ("idea", "IntelliJ IDEA", ("IntelliJ IDEA", "IntelliJ IDEA CE"), "idea"),
    ("pycharm", "PyCharm", ("PyCharm", "PyCharm CE"), "pycharm"),
    ("terminal", "Terminal", ("Terminal",), ""),
    ("finder", "Finder", (), ""),
]


def _mac_app(names: tuple[str, ...]) -> str | None:
    for name in names:
        for root in ("/Applications", str(Path.home() / "Applications"),
                     "/System/Applications", "/System/Applications/Utilities"):
            if Path(root, f"{name}.app").exists():
                return name
    return None


def editors() -> list[dict[str, str]]:
    out = []
    for eid, label, apps, cli in EDITORS:
        if sys.platform == "darwin":
            if eid == "finder" or _mac_app(apps):
                out.append({"id": eid, "label": label})
        elif eid == "finder":
            if shutil.which("xdg-open"):
                out.append({"id": eid, "label": "Files"})
        elif cli and shutil.which(cli):
            out.append({"id": eid, "label": label})
    return out


def open_in(cwd: str, editor_id: str, target: str = "") -> dict[str, Any]:
    """Open the project folder — or ``target``, one of its files (already checked
    to be inside it) — in an editor / Finder (a file is revealed there)."""
    spec = next((e for e in EDITORS if e[0] == editor_id), None)
    if spec is None:
        return {"ok": False, "error": "Unknown app"}
    eid, label, apps, cli = spec
    what = target or cwd
    if sys.platform == "darwin":
        if eid == "finder":
            args = ["open", "-R", what] if target else ["open", what]
        else:
            app = _mac_app(apps)
            if not app:
                return {"ok": False, "error": f"{label} isn't installed on this computer"}
            args = ["open", "-a", app, what]
    elif eid == "finder":
        args = ["xdg-open", str(Path(what).parent) if target else what]
    elif cli and shutil.which(cli):
        args = [cli, what]
    else:
        return {"ok": False, "error": f"{label} isn't installed on this computer"}
    code, out, err = _run(args, cwd, timeout=15)
    if code != 0:
        return {"ok": False, "error": _first_error(err or out) or f"Couldn't open {label}"}
    return {"ok": True, "editor": eid, "label": label}


# ─── Status ───────────────────────────────────────────────────────────────

_BRANCH_LINE = re.compile(r"^## (?P<head>.+?)(?:\.\.\.(?P<up>\S+))?(?: \[(?P<track>[^\]]+)\])?$")


def _parse_branch_line(line: str) -> dict[str, Any]:
    m = _BRANCH_LINE.match(line.strip())
    info: dict[str, Any] = {"branch": "", "upstream": "", "ahead": 0, "behind": 0, "gone": False}
    if not m:
        return info
    head = m.group("head")
    if head.startswith("No commits yet on "):
        head = head[len("No commits yet on "):]
    if head.startswith("HEAD (no branch)"):
        head = ""
    info["branch"] = head
    info["upstream"] = m.group("up") or ""
    for part in (m.group("track") or "").split(","):
        part = part.strip()
        if part.startswith("ahead "):
            info["ahead"] = int(part[6:] or 0)
        elif part.startswith("behind "):
            info["behind"] = int(part[7:] or 0)
        elif part == "gone":
            info["gone"] = True
    return info


def _count_lines(path: Path) -> int:
    try:
        if path.stat().st_size > UNTRACKED_MAX_BYTES:
            return 0
        data = path.read_bytes()
    except OSError:
        return 0
    if b"\0" in data[:8000]:
        return 0  # binary
    return data.count(b"\n") + (1 if data and not data.endswith(b"\n") else 0)


def status(cwd: str) -> dict[str, Any]:
    """Everything the card shows. ``repo: False`` outside a git repository."""
    try:
        from .fs_api import display
        shown = display(cwd)
    except Exception:
        shown = cwd
    base: dict[str, Any] = {"ok": True, "api": GIT_API_VERSION, "repo": False, "cwd": cwd, "display": shown,
                            "editors": editors(), "gh": bool(shutil.which("gh"))}
    code, top, _ = _git(cwd, "rev-parse", "--show-toplevel")
    if code != 0:
        return base
    code, out, err = _git(cwd, "status", "--porcelain=v1", "-b", "--untracked-files=all")
    if code != 0:
        return {**base, "repo": True, "error": _first_error(err)}
    lines = out.splitlines()
    info = _parse_branch_line(lines[0] if lines and lines[0].startswith("## ") else "")
    files = [ln for ln in lines if not ln.startswith("## ")]
    untracked = [ln[3:] for ln in files if ln.startswith("??")]
    staged = sum(1 for ln in files if ln[:1] not in (" ", "?"))
    detached = not info["branch"]
    if detached:
        _, sha, _ = _git(cwd, "rev-parse", "--short", "HEAD")
        info["branch"] = sha.strip()
    added = removed = 0
    has_head = _git(cwd, "rev-parse", "--verify", "-q", "HEAD")[0] == 0
    numstat = _git(cwd, "diff", "--numstat", "HEAD")[1] if has_head else (
        _git(cwd, "diff", "--numstat")[1] + _git(cwd, "diff", "--numstat", "--cached")[1])
    for ln in numstat.splitlines():
        parts = ln.split("\t")
        if len(parts) >= 2 and parts[0].isdigit() and parts[1].isdigit():
            added += int(parts[0])
            removed += int(parts[1])
    root = Path(top.strip() or cwd)
    for rel in untracked[:UNTRACKED_MAX_FILES]:
        added += _count_lines(root / rel.strip('"'))
    remotes = [r for r in _git(cwd, "remote")[1].split() if r]
    return {
        **base,
        "repo": True,
        "root": str(root),
        "branch": info["branch"],
        "detached": detached,
        "upstream": info["upstream"],
        "ahead": info["ahead"],
        "behind": info["behind"],
        "upstream_gone": info["gone"],
        "files": len(files),
        "staged": staged,
        "untracked": len(untracked),
        "added": added,
        "removed": removed,
        "has_head": has_head,
        "remotes": remotes,
    }


def branches(cwd: str) -> dict[str, Any]:
    """Local branches (newest first) + remote ones not checked out yet."""
    code, out, err = _git(cwd, "for-each-ref", "--sort=-committerdate",
                          "--format=%(refname:short)\t%(HEAD)", "refs/heads")
    if code != 0:
        return {"ok": False, "error": _first_error(err) or "Not a git repository"}
    local, current = [], ""
    for ln in out.splitlines():
        name, _, head = ln.partition("\t")
        if not name:
            continue
        local.append(name)
        if head.strip() == "*":
            current = name
    remote, remote_short = [], []
    _, rout, _ = _git(cwd, "for-each-ref", "--sort=-committerdate", "--format=%(refname:short)", "refs/remotes")
    for name in rout.splitlines():
        name = name.strip()
        if not name or name.endswith("/HEAD") or "/" not in name:
            continue
        short = name.split("/", 1)[1]
        if short not in remote_short:
            remote_short.append(short)
        if short not in local and len(remote) < 50:
            remote.append(name)
    # What a PR can merge into: the remote's branches (a PR's base must exist
    # there), else the local ones; the repository's default branch first.
    base = _default_base(cwd).split("/", 1)[-1] if _default_base(cwd) else ""
    bases = list(dict.fromkeys(([base] if base else []) + (remote_short or local)))
    return {"ok": True, "current": current, "local": local, "remote": remote,
            "bases": bases[:80], "base": base or (bases[0] if bases else "")}


# ─── Mutations ────────────────────────────────────────────────────────────

def _valid_branch(cwd: str, name: str) -> str:
    name = (name or "").strip()
    if not name or name.startswith("-"):
        raise GitError("Give the branch a name")
    code, out, _ = _git(cwd, "check-ref-format", "--branch", name)
    if code != 0:
        raise GitError(f"“{name}” isn't a valid branch name")
    return out.strip() or name


def checkout(cwd: str, branch: str, create: bool = False) -> dict[str, Any]:
    with _lock:
        try:
            name = _valid_branch(cwd, branch)
            if create:
                _ok(cwd, "switch", "-c", name)
            elif _git(cwd, "show-ref", "--verify", "-q", f"refs/heads/{name}")[0] == 0:
                _ok(cwd, "switch", name)
            elif "/" in name and _git(cwd, "show-ref", "--verify", "-q", f"refs/remotes/{name}")[0] == 0:
                _ok(cwd, "switch", "--track", name)  # origin/x → a local x that tracks it
            else:
                raise GitError(f"No branch called “{name}”")
        except GitError as exc:
            return {"ok": False, "error": str(exc)}
    return {"ok": True, "branch": status(cwd).get("branch", name)}


def stash(cwd: str, message: str = "") -> dict[str, Any]:
    msg = (message or "").strip() or f"Jarvis web · {time.strftime('%Y-%m-%d %H:%M')}"
    with _lock:
        try:
            out = _ok(cwd, "stash", "push", "--include-untracked", "-m", msg)
        except GitError as exc:
            return {"ok": False, "error": str(exc)}
    if "No local changes" in out:
        return {"ok": False, "error": "Nothing to stash"}
    return {"ok": True, "message": msg}


def _push_locked(cwd: str) -> dict[str, Any]:
    st = status(cwd)
    if not st.get("repo"):
        raise GitError("Not a git repository")
    if st.get("detached"):
        raise GitError("You're not on a branch — switch to one first")
    if st.get("upstream") and not st.get("upstream_gone"):
        _ok(cwd, "push", timeout=NET_TIMEOUT)
        return {"ok": True, "published": False}
    remotes = st.get("remotes") or []
    if not remotes:
        raise GitError("This repository has no remote to push to")
    remote = "origin" if "origin" in remotes else remotes[0]
    _ok(cwd, "push", "-u", remote, "HEAD", timeout=NET_TIMEOUT)
    return {"ok": True, "published": True, "remote": remote}


def push(cwd: str) -> dict[str, Any]:
    with _lock:
        try:
            return _push_locked(cwd)
        except GitError as exc:
            return {"ok": False, "error": str(exc)}


def commit(cwd: str, message: str, *, then_push: bool = False, staged_only: bool = False) -> dict[str, Any]:
    """Commit everything (``git add --all`` first), or with ``staged_only`` just
    what is staged (the Files drawer's Source Control tab)."""
    message = (message or "").strip()
    if not message:
        return {"ok": False, "error": "Write a commit message first (or Generate one)"}
    with _lock:
        try:
            if not staged_only:
                _ok(cwd, "add", "--all")
            if _git(cwd, "diff", "--cached", "--quiet")[0] == 0:
                raise GitError("Nothing staged — stage files with + first" if staged_only else "Nothing to commit")
            _ok(cwd, "commit", "-m", message)
            sha = _git(cwd, "rev-parse", "--short", "HEAD")[1].strip()
            result: dict[str, Any] = {"ok": True, "sha": sha}
            if then_push:
                try:
                    result.update(_push_locked(cwd), ok=True, pushed=True)
                except GitError as exc:  # committed, but the push failed
                    result.update(pushed=False, push_error=str(exc))
            return result
        except GitError as exc:
            return {"ok": False, "error": str(exc)}


def _pull_error(text: str) -> str:
    low = (text or "").lower()
    if "not possible to fast-forward" in low or "diverging branches" in low or "divergent branches" in low:
        return ("Your branch and the remote both have new commits, so it can't just move forward — "
                "merge or rebase in a terminal")
    if "would be overwritten" in low:
        names = [ln.strip() for ln in text.splitlines() if ln.startswith(("\t", "    ")) and ln.strip()]
        which = f" ({', '.join(names[:3])}{'…' if len(names) > 3 else ''})" if names else ""
        return f"Pulling would overwrite your uncommitted changes{which} — commit or stash them first"
    if "no tracking information" in low or "no such ref was fetched" in low:
        return "This branch doesn't track a remote branch — nothing to pull from"
    return _first_error(text) or "git pull failed"


def pull(cwd: str) -> dict[str, Any]:
    """Fast-forward the branch to its upstream (``git pull --ff-only``): never
    a merge commit or a rebase made behind your back."""
    with _lock:
        st = status(cwd)
        if not st.get("repo"):
            return {"ok": False, "error": "Not a git repository"}
        if st.get("detached"):
            return {"ok": False, "error": "You're not on a branch — switch to one first"}
        if not st.get("upstream") or st.get("upstream_gone"):
            return {"ok": False, "error": "This branch doesn't track a remote branch — nothing to pull from"}
        before = _git(cwd, "rev-parse", "HEAD")[1].strip()
        code, out, err = _git(cwd, "pull", "--no-rebase", "--ff-only", timeout=NET_TIMEOUT)
        if code != 0:
            return {"ok": False, "error": _pull_error(f"{err}\n{out}")}
        after = _git(cwd, "rev-parse", "HEAD")[1].strip()
        if not before or before == after:
            return {"ok": True, "commits": 0, "upstream": st["upstream"]}
        n = _git(cwd, "rev-list", "--count", f"{before}..{after}")[1].strip()
        stat = _git(cwd, "diff", "--shortstat", before, after)[1].strip()
        return {"ok": True, "commits": int(n) if n.isdigit() else 0, "summary": stat, "upstream": st["upstream"]}


# ─── Changed files + one file's diff (the Changes dialog) ─────────────────

EMPTY_TREE = "4b825dc642cb6eb9a060e54bf8d69288fbee4904"  # git's empty tree: "before" in a repo with no commit
FILES_MAX = 500               # files listed per side
COMMITS_MAX = 50              # incoming commits listed
DIFF_FILE_MAX_BYTES = 1_500_000
_STATUS = {"A": "added", "D": "deleted", "R": "renamed", "C": "copied", "M": "modified",
           "T": "modified", "U": "conflict"}


def _root(cwd: str) -> str:
    code, top, _ = _git(cwd, "rev-parse", "--show-toplevel")
    if code != 0 or not top.strip():
        raise GitError("Not a git repository")
    return top.strip()


def _name_status(out: str) -> list[dict[str, Any]]:
    """``git diff --name-status -z`` → [{path, old_path, status}]."""
    parts, rows, i = out.split("\0"), [], 0
    while i < len(parts):
        code = parts[i]
        if not code:
            i += 1
            continue
        kind = code[0]
        if kind in "RC" and i + 2 < len(parts):
            rows.append({"path": parts[i + 2], "old_path": parts[i + 1], "status": _STATUS[kind]})
            i += 3
        elif i + 1 < len(parts):
            rows.append({"path": parts[i + 1], "old_path": "", "status": _STATUS.get(kind, "modified")})
            i += 2
        else:
            break
    return rows


def _numstat(out: str) -> dict[str, tuple[int, int, bool]]:
    """``git diff --numstat -z`` → {path: (added, removed, binary)}."""
    parts, res, i = out.split("\0"), {}, 0
    while i < len(parts):
        rec = parts[i]
        if not rec:
            i += 1
            continue
        a, _, rest = rec.partition("\t")
        r, _, path = rest.partition("\t")
        if path == "" and i + 2 < len(parts):  # a rename: the paths follow as their own fields
            path = parts[i + 2]
            i += 3
        else:
            i += 1
        res[path] = (int(a) if a.isdigit() else 0, int(r) if r.isdigit() else 0, a == "-")
    return res


def _diff_files(root: str, *revs: str) -> list[dict[str, Any]]:
    names = _name_status(_ok(root, "diff", "-M", "--name-status", "-z", *revs))
    counts = _numstat(_ok(root, "diff", "-M", "--numstat", "-z", *revs))
    for row in names:
        added, removed, binary = counts.get(row["path"], (0, 0, False))
        row.update(added=added, removed=removed, binary=binary)
    return names[:FILES_MAX]


def _int(v: Any) -> int:
    try:
        return int(v or 0)
    except (TypeError, ValueError):
        return 0


def changed_files(cwd: str) -> dict[str, Any]:
    """What differs, file by file: uncommitted work (``local``, vs HEAD, new
    files included) and what a pull would bring (``incoming``, upstream vs the
    merge base, with its commits)."""
    try:
        st = status(cwd)
        if not st.get("repo"):
            raise GitError("Not a git repository")
        root = _root(cwd)
        local = _diff_files(root, "HEAD" if st.get("has_head") else EMPTY_TREE)
        listed = {r["path"] for r in local}
        others = _git(root, "ls-files", "--others", "--exclude-standard", "-z")[1]
        for rel in [p for p in others.split("\0") if p][:FILES_MAX]:
            if rel in listed:
                continue
            path = Path(root, rel)
            lines = _count_lines(path)
            binary = lines == 0 and path.is_file() and path.stat().st_size > 0
            local.append({"path": rel, "old_path": "", "status": "untracked", "added": lines,
                          "removed": 0, "binary": binary})
        incoming = None
        up = st.get("upstream") or ""
        if up and not st.get("upstream_gone") and st.get("has_head") and _int(st.get("behind")):
            commits = []
            log = _git(root, "log", f"-n{COMMITS_MAX}", "--format=%h%x1f%s%x1f%an%x1f%cr", f"HEAD..{up}")[1]
            for ln in log.splitlines():
                sha, subject, author, when = (ln.split("\x1f") + ["", "", ""])[:4]
                commits.append({"sha": sha, "subject": subject, "author": author, "when": when})
            incoming = {"upstream": up, "behind": _int(st.get("behind")), "commits": commits,
                        "files": _diff_files(root, f"HEAD...{up}")}
        return {"ok": True, "branch": st.get("branch", ""), "upstream": up,
                "behind": _int(st.get("behind")), "local": local[:FILES_MAX], "incoming": incoming}
    except GitError as exc:
        return {"ok": False, "error": str(exc)}


def _blob(root: str, rev: str, path: str) -> str | None:
    """A file's text at ``rev``; None when it's binary or too big to show."""
    code, size, _ = _git(root, "cat-file", "-s", f"{rev}:{path}")
    if code != 0:
        return ""
    if not size.strip().isdigit() or int(size) > DIFF_FILE_MAX_BYTES:
        return None
    try:
        code, out, _ = _git(root, "show", f"{rev}:{path}")
    except (UnicodeDecodeError, ValueError):
        return None
    if code != 0:
        return ""
    return None if "\0" in out[:8000] else out


def _disk(root: str, path: str) -> str | None:
    full = Path(root, path)
    try:
        if not full.resolve().is_relative_to(Path(root).resolve()):
            return ""
        if not full.is_file():
            return ""
        if full.stat().st_size > DIFF_FILE_MAX_BYTES:
            return None
        data = full.read_bytes()
    except OSError:
        return ""
    if b"\0" in data[:8000]:
        return None
    return data.decode("utf-8", errors="replace")


def file_diff(cwd: str, path: str, side: str = "local") -> dict[str, Any]:
    """One listed file's diff as numbered hunks (file_changes.build_hunks, the
    same rows the Changes tab draws). Only paths ``changed_files`` lists."""
    from ..file_changes import build_hunks

    side = "incoming" if side == "incoming" else "local"
    listing = changed_files(cwd)
    if not listing.get("ok"):
        return listing
    rows = (listing.get("incoming") or {}).get("files", []) if side == "incoming" else listing.get("local", [])
    row = next((r for r in rows if r["path"] == path), None)
    if row is None:
        return {"ok": False, "code": "gone", "error": "That file has no changes any more"}
    out: dict[str, Any] = {"ok": True, "side": side, **row, "hunks": [], "hidden": 0}
    if row.get("binary"):
        return {**out, "binary": True}
    try:
        root = _root(cwd)
        old_path = row.get("old_path") or path
        status_ = row["status"]
        if side == "incoming":
            up = listing["upstream"]
            base = _ok(root, "merge-base", "HEAD", up).strip()
            before = "" if status_ == "added" else _blob(root, base, old_path)
            after = "" if status_ == "deleted" else _blob(root, up, path)
        else:
            has_head = _git(root, "rev-parse", "--verify", "-q", "HEAD")[0] == 0
            before = "" if status_ in ("added", "untracked") or not has_head else _blob(root, "HEAD", old_path)
            after = "" if status_ == "deleted" else _disk(root, path)
    except GitError as exc:
        return {"ok": False, "error": str(exc)}
    if before is None or after is None:
        return {**out, "binary": True, "too_large": True}
    hunks, hidden = build_hunks(before, after)
    return {**out, "hunks": hunks, "hidden": hidden}


# ─── Source control: staged / unstaged, stage, unstage, discard ──────────
# The Files drawer's Source Control tab (files.js), like VS Code's: what is
# staged for the next commit, what isn't yet, and merge conflicts. Paths are
# named from the repository root (the page adds / strips ``prefix``).

_CONFLICT_XY = {"DD", "AU", "UD", "UA", "DU", "AA", "UU"}


def _porcelain(root: str) -> list[tuple[str, str, str]]:
    """``git status --porcelain=v1 -z`` → [(xy, path, old_path)]."""
    out = _ok(root, "status", "--porcelain=v1", "-z", "--untracked-files=all")
    parts, rows, i = out.split("\0"), [], 0
    while i < len(parts):
        rec = parts[i]
        i += 1
        if len(rec) < 4:
            continue
        xy, path, old = rec[:2], rec[3:], ""
        if ("R" in xy or "C" in xy) and i < len(parts):  # -z: "XY new\0old\0"
            old = parts[i]
            i += 1
        rows.append((xy, path, old))
    return rows


def _untracked_row(root: str, path: str) -> dict[str, Any]:
    full = Path(root, path)
    lines = _count_lines(full)
    try:
        binary = lines == 0 and full.is_file() and full.stat().st_size > 0
    except OSError:
        binary = False
    return {"path": path, "old_path": "", "status": "untracked", "added": lines, "removed": 0, "binary": binary}


def _scm_rows(root: str) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    """(staged, unstaged, conflicts) — each row {path, old_path, status, added, removed, binary}."""
    rows = _porcelain(root)
    staged_n = _numstat(_git(root, "diff", "--cached", "-M", "--numstat", "-z")[1])
    work_n = _numstat(_git(root, "diff", "--numstat", "-z")[1])

    def row(path: str, old: str, status_: str, counts: dict[str, tuple[int, int, bool]]) -> dict[str, Any]:
        added, removed, binary = counts.get(path, (0, 0, False))
        return {"path": path, "old_path": old, "status": status_, "added": added, "removed": removed, "binary": binary}

    staged: list[dict[str, Any]] = []
    unstaged: list[dict[str, Any]] = []
    conflicts: list[dict[str, Any]] = []
    untracked = 0
    for xy, path, old in rows:
        if xy == "??":
            untracked += 1
            if untracked <= UNTRACKED_MAX_FILES:
                unstaged.append(_untracked_row(root, path))
            else:
                unstaged.append({"path": path, "old_path": "", "status": "untracked", "added": 0, "removed": 0,
                                 "binary": False})
        elif xy in _CONFLICT_XY:
            conflicts.append(row(path, "", "conflict", work_n))
        else:
            x, y = xy[0], xy[1]
            if x not in " ?!":
                staged.append(row(path, old if x in "RC" else "", _STATUS.get(x, "modified"), staged_n))
            if y not in " ?!":
                unstaged.append(row(path, "", _STATUS.get(y, "modified"), work_n))
    key = lambda r: r["path"].lower()  # noqa: E731
    return sorted(staged, key=key), sorted(unstaged, key=key), sorted(conflicts, key=key)


def source_control(cwd: str) -> dict[str, Any]:
    try:
        root = _root(cwd)
        staged, unstaged, conflicts = _scm_rows(root)
    except GitError as exc:
        return {"ok": False, "error": str(exc)}
    on_branch = _git(root, "symbolic-ref", "--short", "-q", "HEAD")[1].strip()
    branch = on_branch or _git(root, "rev-parse", "--short", "HEAD")[1].strip()
    prefix = _git(cwd, "rev-parse", "--show-prefix")[1].strip()
    total = len(staged) + len(unstaged) + len(conflicts)
    # For the commit box's ⌄ menu: Push N commits / Publish branch / nothing to push.
    code, up, _ = _git(root, "rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{u}")
    upstream = up.strip() if code == 0 else ""
    ahead = _int(_git(root, "rev-list", "--count", "@{u}..HEAD")[1].strip()) if upstream else 0
    return {"ok": True, "branch": branch, "prefix": prefix, "upstream": upstream, "ahead": ahead,
            "remotes": bool(_git(root, "remote")[1].split()),
            "detached": not on_branch, "gh": bool(shutil.which("gh")),  # Create PR (GitHub CLI)
            "has_head": _git(root, "rev-parse", "--verify", "-q", "HEAD")[0] == 0,
            "staged": staged[:FILES_MAX], "unstaged": unstaged[:FILES_MAX], "conflicts": conflicts[:FILES_MAX],
            "truncated": max(0, len(staged) - FILES_MAX) + max(0, len(unstaged) - FILES_MAX), "total": total}


def _pathspecs(paths: Any) -> list[str]:
    """Repository-relative paths from the page, checked: never outside the repo, never ``.git``."""
    if not isinstance(paths, list) or not paths:
        raise GitError("Pick a file first")
    out = []
    for p in paths[:FILES_MAX]:
        text = str(p or "").replace("\\", "/")
        parts = [s for s in text.split("/") if s not in ("", ".")]
        if not parts or "\x00" in text or text.startswith("/") or ".." in parts or ".git" in parts:
            raise GitError("That path is outside the repository")
        out.append("/".join(parts))
    return out


def _forget_file_marks(cwd: str) -> None:
    try:
        from . import files_api

        files_api.forget(cwd)
    except Exception:  # the file browser's markers just stay a moment longer
        pass


def stage(cwd: str, paths: Any = None, *, everything: bool = False) -> dict[str, Any]:
    """``git add`` (new, changed and deleted files alike; a conflict counts as resolved)."""
    with _lock:
        try:
            root = _root(cwd)
            if everything:
                _ok(root, "add", "--all")
            else:
                _ok(root, "--literal-pathspecs", "add", "--all", "--", *_pathspecs(paths))
        except GitError as exc:
            return {"ok": False, "error": str(exc)}
        finally:
            _forget_file_marks(cwd)
    return {"ok": True}


def unstage(cwd: str, paths: Any = None, *, everything: bool = False) -> dict[str, Any]:
    """Take files out of the next commit; their text on disk is untouched."""
    with _lock:
        try:
            root = _root(cwd)
            has_head = _git(root, "rev-parse", "--verify", "-q", "HEAD")[0] == 0
            if everything:
                if has_head:
                    _ok(root, "restore", "--staged", "--", ".")
                else:
                    _ok(root, "rm", "--cached", "-r", "-q", "--ignore-unmatch", "--", ".")
            else:
                specs = _pathspecs(paths)
                if has_head:
                    _ok(root, "--literal-pathspecs", "restore", "--staged", "--", *specs)
                else:
                    _ok(root, "--literal-pathspecs", "rm", "--cached", "-r", "-q", "--ignore-unmatch", "--", *specs)
        except GitError as exc:
            return {"ok": False, "error": str(exc)}
        finally:
            _forget_file_marks(cwd)
    return {"ok": True}


def discard(cwd: str, paths: Any = None, *, everything: bool = False) -> dict[str, Any]:
    """Throw away changes that aren't staged. A changed file goes back to its staged
    (else committed) text — its current text is first *copied* to the Trash; a new
    file is *moved* to the Trash. Nothing is lost for good."""
    from . import files_api

    with _lock:
        try:
            root = _root(cwd)
            _, unstaged, _ = _scm_rows(root)
            wanted = None if everything else set(_pathspecs(paths))
            rows = [r for r in unstaged if wanted is None or r["path"] in wanted]
            if not rows:
                raise GitError("Nothing to discard")
            restore, trashed = [], []
            for r in rows:
                full = Path(root, r["path"])
                if r["status"] == "untracked":
                    files_api.delete(Path(root), r["path"])
                    trashed.append(r["path"])
                    continue
                if r["status"] != "deleted" and full.is_file():
                    files_api.trash_copy(full)
                restore.append(r["path"])
            if restore:
                _ok(root, "--literal-pathspecs", "checkout", "-q", "--", *restore)
        except files_api.FilesError as exc:
            return {"ok": False, "error": str(exc)}
        except OSError as exc:
            return {"ok": False, "error": f"Couldn't put a copy in the Trash: {exc.strerror or exc}"}
        except GitError as exc:
            return {"ok": False, "error": str(exc)}
        finally:
            _forget_file_marks(cwd)
    return {"ok": True, "discarded": len(rows), "restored": restore, "trashed": trashed}


def scm_diff(cwd: str, path: str, side: str) -> dict[str, Any]:
    """One file's diff for the Source Control tab: ``staged`` = HEAD → the index
    (what the next commit gets), ``unstaged`` = the index → the file on disk."""
    from ..file_changes import build_hunks

    side = "staged" if side == "staged" else "unstaged"
    try:
        root = _root(cwd)
        staged, unstaged, conflicts = _scm_rows(root)
        rows = staged if side == "staged" else unstaged + conflicts
        row = next((r for r in rows if r["path"] == path), None)
        if row is None:
            return {"ok": False, "code": "gone", "error": "That file has no changes here any more"}
        out: dict[str, Any] = {"ok": True, "side": side, **row, "hunks": [], "hidden": 0}
        if row.get("binary"):
            return {**out, "binary": True}
        has_head = _git(root, "rev-parse", "--verify", "-q", "HEAD")[0] == 0
        st = row["status"]
        if side == "staged":
            before = "" if st == "added" or not has_head else _blob(root, "HEAD", row["old_path"] or path)
            after = "" if st == "deleted" else _blob(root, "", path)  # ":path" = the index
        else:
            if st == "untracked":
                before = ""
            elif st == "conflict":  # the index holds the merge stages: compare with HEAD
                before = _blob(root, "HEAD", path) if has_head else ""
            else:
                before = _blob(root, "", path)
            after = "" if st == "deleted" else _disk(root, path)
    except GitError as exc:
        return {"ok": False, "error": str(exc)}
    if before is None or after is None:
        return {**out, "binary": True, "too_large": True}
    hunks, hidden = build_hunks(before, after)
    return {**out, "hunks": hunks, "hidden": hidden}


_URL = re.compile(r"https?://\S+")


def slug_branch(text: str, prefix: str = "") -> str:
    """"feat(web): Add the git card" → "feat/add-the-git-card" (a valid branch name)."""
    text = (text or "").strip()
    m = re.match(r"^(feat|fix|chore|docs|refactor|perf|test|build|ci|style)(?:\([^)]*\))?!?:\s*(.*)$", text, re.I)
    if m:
        prefix, text = m.group(1).lower(), m.group(2)
    elif not prefix and re.match(r"^(fixes|fixed|fix)\b", text, re.I):
        prefix, text = "fix", re.sub(r"^(fixes|fixed|fix)\s*", "", text, flags=re.I)
    slug = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
    slug = "-".join(slug.split("-")[:7])[:48].strip("-")
    if not slug:
        return ""
    return f"{prefix or 'feat'}/{slug}"


def _pr_plan(st: dict[str, Any], head: str, base: str, local: list[str]) -> str:
    """What creating this PR will do, in one line (the form shows it too)."""
    files = int(st.get("files") or 0)
    current = st.get("branch") or ""
    if head == current:
        return "commit_push" if files else "push"
    if head in local:
        return "push_other"
    return "new_branch"


def create_pr(cwd: str, title: str = "", body: str = "", *, base: str = "", head: str = "",
              draft: bool = False) -> dict[str, Any]:
    """Open a PR from ``head`` into ``base``.

    ``head`` empty = the branch you're on. A ``head`` that doesn't exist yet is
    created from your current work (``switch -c``, so uncommitted changes come
    along). Uncommitted changes on the head are committed with the title as the
    message, then the head is pushed and ``gh pr create --base --head`` runs.
    """
    if not shutil.which("gh"):
        return {"ok": False, "error": "Creating a PR needs the GitHub CLI — brew install gh, then gh auth login"}
    with _lock:
        try:
            st = status(cwd)
            if not st.get("repo"):
                raise GitError("Not a git repository")
            remotes = st.get("remotes") or []
            if not remotes:
                raise GitError("This repository has no remote to open a PR on")
            remote = "origin" if "origin" in remotes else remotes[0]
            current = "" if st.get("detached") else (st.get("branch") or "")
            head = (head or "").strip() or current
            base = (base or "").strip() or _default_base(cwd).split("/", 1)[-1]
            if not head:
                raise GitError("Name the branch this PR comes from")
            if not base:
                raise GitError("Pick the branch to merge into")
            if head == base:
                raise GitError(f"A PR merges one branch into another — pick a branch other than “{base}” to open it from")
            head = _valid_branch(cwd, head)
            local = [n for n in _git(cwd, "for-each-ref", "--format=%(refname:short)", "refs/heads")[1].split() if n]
            plan = _pr_plan(st, head, base, local)
            committed = ""
            if plan == "new_branch":
                _ok(cwd, "switch", "-c", head)  # your changes come along
                current = head
            if head == current and int(st.get("files") or 0):
                message = title.strip()
                if not message:
                    raise GitError("Add a title — it's also the commit message for your changes")
                _ok(cwd, "add", "--all")
                if _git(cwd, "diff", "--cached", "--quiet")[0] != 0:
                    _ok(cwd, "commit", "-m", message)
                    committed = _git(cwd, "rev-parse", "--short", "HEAD")[1].strip()
            # gh needs the branch on the remote.
            _ok(cwd, "push", "-u", remote, head if head != current else "HEAD", timeout=NET_TIMEOUT)
            args = ["gh", "pr", "create", "--base", base, "--head", head]
            if title.strip():
                args += ["--title", title.strip(), "--body", body.strip()]
            else:
                args += ["--fill"]
            if draft:
                args.append("--draft")
            code, out, err = _run(args, cwd, timeout=NET_TIMEOUT)
            text = f"{out}\n{err}"
            extra = {"head": head, "base": base, "created_branch": plan == "new_branch", "commit": committed}
            if code != 0:
                m = _URL.search(text)
                if m and "already exists" in text:
                    return {"ok": True, "url": m.group(0), "existing": True, **extra}
                raise GitError(_first_error(err or out) or "gh pr create failed")
            m = _URL.search(text)
            return {"ok": True, "url": m.group(0) if m else "", **extra}
        except GitError as exc:
            return {"ok": False, "error": str(exc)}


# ─── Model-written commit / PR text ───────────────────────────────────────

_COMMIT_SYSTEM = """You write git commit messages for a developer.
- First line: a summary of at most 72 characters, imperative mood. Follow the style of the recent commit subjects you are shown (e.g. "feat(web): …" prefixes) when they have one.
- If the change needs it, a blank line and then a short body: 1–5 lines or "- " bullets on what changed and why.
- Describe only what the diff shows. No preamble, no quotes, no code fences, no sign-off lines."""

_PR_SYSTEM = """You write GitHub pull request descriptions for a developer.
- Line 1: the PR title, at most 72 characters, no "Title:" label.
- Line 2: "Branch: " and a branch name for this work, like feat/short-topic or fix/short-topic (lowercase, 2–5 words joined by hyphens).
- Then a blank line and a short Markdown body: "## Summary" with 2–5 bullets on what changed and why, then "## Testing" with one or two lines.
- Describe only what the diff and commits show. No preamble and no code fences around the whole answer."""


_BRANCH_SYSTEM = """You name git branches.
Reply with one branch name only, like feat/short-topic or fix/short-topic: lowercase, a type prefix (feat, fix, chore, docs, refactor, perf, test), a slash, then 2–5 words joined by hyphens. No quotes, no explanation."""


def _diff_for_model(cwd: str, *, against: str = "HEAD", limit: int = DIFF_MAX_CHARS) -> str:
    parts = []
    has_head = _git(cwd, "rev-parse", "--verify", "-q", "HEAD")[0] == 0
    stat = _git(cwd, "diff", "--stat", against)[1] if has_head else _git(cwd, "diff", "--stat", "--cached")[1]
    if stat.strip():
        parts.append("Stat:\n" + stat.strip())
    untracked = _git(cwd, "ls-files", "--others", "--exclude-standard")[1].split("\n")
    untracked = [u for u in untracked if u.strip()]
    if untracked:
        parts.append("New files:\n" + "\n".join(untracked[:60]))
    diff = _git(cwd, "diff", against)[1] if has_head else _git(cwd, "diff", "--cached")[1]
    if diff.strip():
        parts.append("Diff:\n" + diff)
    for rel in untracked[:5]:  # a peek at new files' content
        try:
            head = Path(cwd, rel).read_text(errors="replace")[:800]
        except OSError:
            continue
        parts.append(f"New file {rel}:\n{head}")
    text = "\n\n".join(parts)
    if len(text) > limit:
        text = text[:limit] + "\n… (diff cut here)"
    return text


def _staged_diff_for_model(cwd: str, *, limit: int = DIFF_MAX_CHARS) -> str:
    """Like ``_diff_for_model`` but only what is staged (a staged-only commit)."""
    stat = _git(cwd, "diff", "--cached", "--stat")[1].strip()
    diff = _git(cwd, "diff", "--cached")[1]
    text = "\n\n".join(p for p in (f"Stat:\n{stat}" if stat else "", f"Diff:\n{diff}" if diff.strip() else "") if p)
    if len(text) > limit:
        text = text[:limit] + "\n… (diff cut here)"
    return text


def _default_base(cwd: str) -> str:
    code, out, _ = _git(cwd, "symbolic-ref", "--short", "-q", "refs/remotes/origin/HEAD")
    if code == 0 and out.strip():
        return out.strip()
    for cand in ("origin/main", "origin/master", "main", "master"):
        if _git(cwd, "rev-parse", "--verify", "-q", cand)[0] == 0:
            return cand
    return ""


_gen_cache: dict[str, tuple[float, str]] = {}


def _ask_model(system: str, user: str, *, max_tokens: int = 1024) -> str:
    """One side request to the git text model (not part of the conversation).

    Settings → *Commit text model* decides which model answers: Auto (the
    chat's own, the default) or a provider/model picked just for this work.
    Answers are kept for ``GEN_CACHE_TTL`` keyed by model + prompt, so asking
    again for unchanged changes is instant.
    """
    import hashlib

    from .. import side_model, state

    source, side = side_model.preference()
    key = hashlib.sha1(f"{source or state.provider}|{side or state.MODEL}|{max_tokens}|{system}|{user}".encode("utf-8", "replace")).hexdigest()
    hit = _gen_cache.get(key)
    if hit and time.monotonic() - hit[0] < GEN_CACHE_TTL:
        return hit[1]
    text = _ask_model_uncached(system, user, max_tokens=max_tokens)
    if text:
        _gen_cache[key] = (time.monotonic(), text)
        while len(_gen_cache) > 32:
            _gen_cache.pop(next(iter(_gen_cache)))
    return text


def _ask_model_uncached(system: str, user: str, *, max_tokens: int) -> str:
    from .. import side_model
    from ..constants import AUTH_OAUTH, OAUTH_IDENTITY, PROVIDER_OPENCODE, PROVIDER_OPENCODE_ZEN
    from ..prompt_enhance import _blocks, _short_error

    try:
        client, model, provider, auth_mode = side_model.resolve()
    except Exception as exc:  # noqa: BLE001 — a misconfigured pick must say so
        raise GitError(
            f"The commit text model ({side_model.label()}) isn't available — {exc}. "
            "Set it back to Auto in Settings."
        ) from exc
    if client is None:
        raise GitError("No model connected — run /provider")
    sys_prompt: Any = system
    if auth_mode == AUTH_OAUTH:
        sys_prompt = [{"type": "text", "text": OAUTH_IDENTITY}, {"type": "text", "text": system}]
    answer_tool = {
        "name": "write_text", "description": "Return the text you wrote. Call it once.",
        "input_schema": {"type": "object", "properties": {"text": {"type": "string"}}, "required": ["text"]},
    }
    kwargs: dict[str, Any] = dict(model=model, max_tokens=max_tokens, system=sys_prompt,
                                  messages=[{"role": "user", "content": user}])
    if provider in (PROVIDER_OPENCODE, PROVIDER_OPENCODE_ZEN):
        kwargs["thinking"] = {"type": "disabled"}
    if getattr(client, "gate_tools", None):  # gateways that force tools into every request
        kwargs["tools"] = [answer_tool]
    box: dict[str, Any] = {}

    def run() -> None:
        try:
            with client.messages.stream(**kwargs) as stream:
                blocks = _blocks(stream.get_final_message())
            for b in blocks:
                if b.get("type") == "tool_use" and b.get("name") == "write_text":
                    box["text"] = str((b.get("input") or {}).get("text") or "")
                    return
            box["text"] = "".join(b.get("text") or "" for b in blocks if b.get("type") == "text")
        except BaseException as exc:  # noqa: BLE001 — reported below
            box["error"] = exc

    worker = threading.Thread(target=run, name="git-message", daemon=True)
    worker.start()
    worker.join(MESSAGE_TIMEOUT)
    if worker.is_alive():
        raise GitError(f"The model took longer than {int(MESSAGE_TIMEOUT)}s — try again")
    if box.get("error") is not None:
        raise GitError(_short_error(box["error"]))
    return _clean_message(box.get("text", ""))


def _clean_message(text: str) -> str:
    text = (text or "").strip()
    text = re.sub(r"^```[a-z]*\n|\n```$", "", text).strip()
    lines = text.splitlines()
    if lines and re.match(r"^(here(?:'s| is)|sure|commit message|title)\b.*:\s*$", lines[0], re.I):
        lines = lines[1:]
    if lines:
        lines[0] = re.sub(r"^(?:title|subject)\s*:\s*", "", lines[0], flags=re.I).strip().strip('"`')
    return "\n".join(lines).strip()


def _branch_line(lines: list[str]) -> tuple[str, list[str]]:
    """Pull a "Branch: feat/x" line out of the model's answer."""
    for i, ln in enumerate(lines[:4]):
        m = re.match(r"^\s*\**branch\**\s*:\s*`?([A-Za-z0-9._/-]+)`?\s*$", ln, re.I)
        if m:
            return m.group(1).lower().strip("/"), lines[:i] + lines[i + 1:]
    return "", lines


def generate(cwd: str, kind: str = "commit", *, title: str = "", staged: bool = False) -> dict[str, Any]:
    try:
        if kind == "pr":
            base = _default_base(cwd)
            log = _git(cwd, "log", f"{base}..HEAD", "--format=- %s", "-30")[1] if base else ""
            committed = _git(cwd, "diff", f"{base}...HEAD")[1][:DIFF_MAX_CHARS // 2] if base else ""
            pending = _diff_for_model(cwd, limit=DIFF_MAX_CHARS - len(committed))
            diff = (committed + "\n" + pending).strip()
            if not (log.strip() or diff):
                raise GitError("No changes on this branch yet")
            user = (f"Branch commits (vs {base or 'the default branch'}):\n{log.strip() or '(none yet)'}\n\n"
                    f"Changes:\n{diff}\n\nWrite the pull request title, branch name and description.")
            lines = _ask_model(_PR_SYSTEM, user, max_tokens=900).splitlines()
            branch, lines = _branch_line(lines)
            title_line = lines[0].strip() if lines else ""
            body = "\n".join(lines[1:]).strip()
            if not branch or not re.fullmatch(r"[a-z0-9]+(?:/[a-z0-9][a-z0-9._-]*)+", branch):
                branch = slug_branch(title_line)
            return {"ok": True, "title": title_line, "body": body, "branch": branch[:60]}
        if kind == "branch":
            if title.strip():  # a title is there: name the branch after it, no model needed
                return {"ok": True, "branch": slug_branch(title)}
            diff = _diff_for_model(cwd, limit=4000)  # a name needs the gist, not the whole diff
            if not diff.strip():
                raise GitError("No changes to name a branch after")
            log = _git(cwd, "log", "-5", "--format=%s")[1].strip()
            text = _ask_model(_BRANCH_SYSTEM, f"Recent commits:\n{log or '(none)'}\n\nChanges:\n{diff}\n\nSuggest the branch name.",
                              max_tokens=60)
            name = text.strip().splitlines()[0].strip().strip("`'\"") if text.strip() else ""
            if "/" in name and re.fullmatch(r"[a-z0-9]+/[a-z0-9][a-z0-9-]*", name):
                return {"ok": True, "branch": name[:60]}
            return {"ok": True, "branch": slug_branch(name)}
        diff = _staged_diff_for_model(cwd) if staged else _diff_for_model(cwd)
        if not diff.strip():
            raise GitError("Nothing staged to describe" if staged else "Nothing to commit")
        recent = _git(cwd, "log", "-8", "--format=%s")[1].strip()
        user = (f"Recent commit subjects in this repository:\n{recent or '(none yet)'}\n\n"
                f"Changes to commit:\n{diff}\n\nWrite the commit message.")
        return {"ok": True, "message": _ask_model(_COMMIT_SYSTEM, user, max_tokens=400)}
    except GitError as exc:
        return {"ok": False, "error": str(exc)}


# ─── Entry point for the HTTP handler ─────────────────────────────────────

MUTATING = {"checkout", "stash", "commit", "push", "pr", "pull", "stage", "unstage", "discard"}


def post(op: str, data: dict[str, Any], *, busy: bool = False) -> dict[str, Any]:
    cwd = os.getcwd()
    if op in ("checkout", "stash", "pull", "discard") and busy:
        return {"ok": False, "code": "busy",
                "error": "Jarvis is working in these files. Wait for it to finish, or stop it first."}
    if op == "open":
        target = ""
        if data.get("path"):  # one file of the project, not an arbitrary path
            from . import files_api

            try:
                target = str(files_api.resolve_inside(cwd, str(data["path"])))
            except files_api.FilesError as exc:
                return {"ok": False, "error": str(exc)}
        return open_in(cwd, str(data.get("editor") or ""), target)
    if op == "branches":
        return branches(cwd)
    if op == "checkout":
        return checkout(cwd, str(data.get("branch") or ""), create=bool(data.get("create")))
    if op == "stash":
        return stash(cwd, str(data.get("message") or ""))
    if op == "commit":
        return commit(cwd, str(data.get("message") or ""), then_push=bool(data.get("push")),
                      staged_only=bool(data.get("staged")))
    if op == "scm":
        return source_control(cwd)
    if op == "stage":
        return stage(cwd, data.get("paths"), everything=bool(data.get("all")))
    if op == "unstage":
        return unstage(cwd, data.get("paths"), everything=bool(data.get("all")))
    if op == "discard":
        return discard(cwd, data.get("paths"), everything=bool(data.get("all")))
    if op == "push":
        return push(cwd)
    if op == "pull":
        return pull(cwd)
    if op == "files":
        return changed_files(cwd)
    if op == "diff" and data.get("side") in ("staged", "unstaged"):
        return scm_diff(cwd, str(data.get("path") or ""), str(data["side"]))
    if op == "diff":
        return file_diff(cwd, str(data.get("path") or ""), str(data.get("side") or "local"))
    if op == "pr":
        head = str(data.get("head") or "").strip()
        if busy and head and head != status(cwd).get("branch"):
            return {"ok": False, "code": "busy",
                    "error": "Jarvis is working in these files. Wait for it to finish before moving them to another branch."}
        return create_pr(cwd, str(data.get("title") or ""), str(data.get("body") or ""),
                         base=str(data.get("base") or ""), head=str(data.get("head") or ""),
                         draft=bool(data.get("draft")))
    if op == "generate":
        kind = str(data.get("kind") or "commit")
        return generate(cwd, kind if kind in ("pr", "branch") else "commit", title=str(data.get("title") or ""),
                        staged=bool(data.get("staged")))
    return {"ok": False, "error": f"unknown git op: {op}"}
