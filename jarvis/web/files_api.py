"""Project file browser for the web inspector: list, search, read, edit.

UI-free, like ``git_api``. ``POST /api/files {op, …}`` goes through
:func:`post`, always for the project on screen (``os.getcwd()``); the page
names paths *relative to that folder*, never absolute ones.

Safety
  * Every path is resolved and must stay inside the project folder — ``..``,
    absolute paths and symlinks that point away are refused.
  * ``.git`` is never listed, read or changed.
  * Saves are atomic (temp file + rename, mode kept) and carry the ``etag``
    the page read: if Jarvis (or anything else) changed the file since, the
    save is refused with ``code: "conflict"`` instead of overwriting it.
  * Delete moves to the Trash, it never unlinks.
  * Text only: files over :data:`MAX_EDIT_BYTES`, binaries and non-UTF-8 files
    are shown as such, never opened for editing (so nothing gets mangled).
"""
from __future__ import annotations

import base64
import mimetypes
import os
import shutil
import sys
import tempfile
import threading
import time
from pathlib import Path
from typing import Any

from . import git_api

FILES_API_VERSION = 1

MAX_EDIT_BYTES = 1_000_000       # larger text files are shown as "too large", not opened
MAX_IMAGE_BYTES = 4_000_000      # images are sent inline (data URL) up to this size
MAX_ENTRIES = 1500               # entries listed per folder (the rest is counted)
SEARCH_MAX_FILES = 40_000        # files the name search indexes
SEARCH_RESULTS = 60
INDEX_TTL = 10.0                 # seconds a file index / git marks answer is reused
MARKS_TTL = 2.0
MAX_NAME_BYTES = 255

HIDDEN_NAMES = {".git", ".DS_Store"}
# Folders the fallback walk (no git) skips: huge, generated, never what you search for.
NOISE_DIRS = {".git", "node_modules", "__pycache__", ".venv", "venv", ".mypy_cache", ".pytest_cache",
              ".ruff_cache", ".tox", ".next", ".cache", "dist", "build", "target", ".gradle", ".idea"}
IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp", ".ico", ".avif"}
BOM = b"\xef\xbb\xbf"

MUTATING = {"write", "create", "rename", "delete"}

_lock = threading.Lock()                       # one mutation at a time
_cache: dict[tuple[str, str], tuple[float, Any]] = {}


class FilesError(Exception):
    """Shown to the user as is; ``code`` lets the page react (conflict, exists, …)."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


# ─── Paths ────────────────────────────────────────────────────────────────

def _inside(root: Path, path: Path) -> bool:
    return path == root or path.is_relative_to(root)


def _locate(root: Path, rel: Any) -> tuple[Path, Path, str]:
    """``(entry, real, clean)`` for a project-relative path.

    ``entry`` is the path itself (a symlink stays a symlink: rename / delete
    act on the link), ``real`` what it resolves to (read / write follow it),
    ``clean`` the normalised ``a/b/c`` form. Raises ``FilesError`` for
    anything that isn't inside the project.
    """
    text = str(rel or "").replace("\\", "/")
    if "\x00" in text:
        raise FilesError("bad_path", "That isn't a valid path.")
    parts = [p for p in text.split("/") if p not in ("", ".")]
    if ".." in parts:
        raise FilesError("outside", "That path is outside the project.")
    if ".git" in parts:
        raise FilesError("protected", "The .git folder is off limits here — use git for that.")
    entry = root.joinpath(*parts)
    try:
        real = entry.resolve(strict=False)
        anchor = entry.parent.resolve(strict=False) / entry.name if parts else root
    except (OSError, RuntimeError):
        raise FilesError("bad_path", "That isn't a valid path.")
    if not _inside(root, anchor) or not _inside(root, real):
        raise FilesError("outside", "That path is outside the project.")
    return entry, real, "/".join(parts)


def _root(cwd: str | None = None) -> Path:
    return Path(cwd or os.getcwd()).resolve()


def _etag(st: os.stat_result) -> str:
    return f"{st.st_mtime_ns:x}-{st.st_size:x}"


def _valid_name(name: str) -> str:
    name = name.strip()
    if not name or name in (".", ".."):
        raise FilesError("bad_name", "Give it a name.")
    if "/" in name or "\\" in name or "\x00" in name:
        raise FilesError("bad_name", "A name can't contain slashes.")
    if len(name.encode("utf-8")) > MAX_NAME_BYTES:
        raise FilesError("bad_name", "That name is too long.")
    return name


def _cached(kind: str, root: Path, ttl: float, build: Any) -> Any:
    key = (kind, str(root))
    hit = _cache.get(key)
    now = time.monotonic()
    if hit and now - hit[0] < ttl:
        return hit[1]
    value = build()
    _cache[key] = (now, value)
    return value


def _forget(root: Path) -> None:
    for kind in ("marks", "index"):
        _cache.pop((kind, str(root)), None)


def forget(cwd: str | None = None) -> None:
    """Drop the cached git markers / name index (git_api calls this after staging, a discard, a commit)."""
    _forget(_root(cwd))


# ─── Git markers (M / A / U …) ────────────────────────────────────────────

def _mark(xy: str) -> str:
    if xy == "??":
        return "U"
    if "U" in xy or xy in ("AA", "DD"):
        return "!"
    if "D" in xy:
        return "D"
    if "R" in xy or "C" in xy:
        return "R"
    if "A" in xy:
        return "A"
    return "M"


def _read_marks(root: Path) -> dict[str, Any]:
    """``{repo, prefix, marks}``: git's view of the folder, paths relative to it.

    ``prefix`` is the folder's place inside the repository (``""`` at its top):
    the page needs it to ask git for a file's diff, which names paths from the
    repository root.
    """
    cwd = str(root)
    code, top, _ = git_api._git(cwd, "rev-parse", "--show-toplevel")
    if code != 0 or not top.strip():
        return {"repo": False, "prefix": "", "marks": {}}
    try:
        rel = root.relative_to(Path(top.strip()).resolve()).as_posix()
    except ValueError:
        return {"repo": False, "prefix": "", "marks": {}}
    prefix = "" if rel == "." else f"{rel}/"
    code, out, _ = git_api._git(cwd, "status", "--porcelain=v1", "-z")
    marks: dict[str, str] = {}
    if code == 0:
        parts, i = out.split("\0"), 0
        while i < len(parts):
            rec = parts[i]
            i += 1
            if len(rec) < 4:
                continue
            xy, path = rec[:2], rec[3:]
            if "R" in xy or "C" in xy:
                i += 1  # the old name follows as a field of its own
            path = path.rstrip("/")
            if prefix:
                if not path.startswith(prefix):
                    continue
                path = path[len(prefix):]
            if path:
                marks[path] = _mark(xy)
    return {"repo": True, "prefix": prefix, "marks": marks}


def _git_state(root: Path) -> dict[str, Any]:
    return _cached("marks", root, MARKS_TTL, lambda: _read_marks(root))


def _ignored(root: Path, paths: list[str]) -> set[str]:
    """Which of these (dirs end in ``/``) are git-ignored — dimmed in the tree."""
    if not paths:
        return set()
    # No -z: git only allows it with --stdin. A name needing quotes (a quote, a
    # backslash, a newline) comes back quoted and simply isn't dimmed.
    code, out, _ = git_api._git(str(root), "-c", "core.quotepath=off", "check-ignore", "--", *paths)
    if code not in (0, 1):
        return set()
    return {p for p in out.splitlines() if p}


# ─── List ─────────────────────────────────────────────────────────────────

def list_dir(root: Path, rel: str = "") -> dict[str, Any]:
    """One folder's entries (folders first), with git markers; the tree loads lazily."""
    _, real, clean = _locate(root, rel)
    if not real.exists():
        raise FilesError("not_found", f"{clean or 'The project folder'} doesn't exist any more.")
    if not real.is_dir():
        raise FilesError("not_a_folder", f"{clean} is a file, not a folder.")
    try:
        scanned = [e for e in os.scandir(real) if e.name not in HIDDEN_NAMES]
    except PermissionError:
        raise FilesError("no_access", "Jarvis isn't allowed to open this folder.")
    except OSError as exc:
        raise FilesError("unreadable", f"Couldn't read this folder: {exc.strerror or exc}.")
    state = _git_state(root)
    marks: dict[str, str] = state["marks"]
    dirty_dirs = {"/".join(p.split("/")[:i]) for p in marks for i in range(1, p.count("/") + 1)}
    base = f"{clean}/" if clean else ""
    rows: list[dict[str, Any]] = []
    for e in scanned:
        try:
            is_dir = e.is_dir(follow_symlinks=True)
        except OSError:
            is_dir = False
        link = e.is_symlink()
        try:
            st = e.stat(follow_symlinks=True)
        except OSError:
            st = None  # a dangling link
        path = f"{base}{e.name}"
        git = ""
        if is_dir:
            git = "dirty" if path in dirty_dirs or marks.get(path) else ""
        else:
            git = marks.get(path, "")
        rows.append({"name": e.name, "path": path, "dir": is_dir, "link": link,
                     "broken": st is None, "size": 0 if st is None or is_dir else st.st_size,
                     "mtime": 0 if st is None else st.st_mtime, "git": git, "ignored": False})
    rows.sort(key=lambda r: (not r["dir"], r["name"].lower(), r["name"]))
    total = len(rows)
    rows = rows[:MAX_ENTRIES]
    if state["repo"]:
        hits = _ignored(root, [r["path"] + ("/" if r["dir"] else "") for r in rows])
        for r in rows:
            r["ignored"] = (r["path"] + ("/" if r["dir"] else "")) in hits
    return {"ok": True, "path": clean, "entries": rows, "total": total,
            "truncated": max(0, total - MAX_ENTRIES), "repo": state["repo"], "prefix": state["prefix"]}


# ─── Search ───────────────────────────────────────────────────────────────

def _walk_files(root: Path) -> list[str]:
    out: list[str] = []
    for base, dirs, names in os.walk(root):
        dirs[:] = sorted(d for d in dirs if d not in NOISE_DIRS)
        rel = Path(base).relative_to(root).as_posix()
        for n in sorted(names):
            if n in HIDDEN_NAMES:
                continue
            out.append(n if rel == "." else f"{rel}/{n}")
            if len(out) >= SEARCH_MAX_FILES:
                return out
    return out


def _build_index(root: Path) -> tuple[list[str], bool]:
    code, out, _ = git_api._git(str(root), "ls-files", "-z", "--cached", "--others", "--exclude-standard")
    if code == 0:
        files = sorted({p for p in out.split("\0") if p and not p.startswith(".git/")})
    else:
        files = _walk_files(root)
    return files[:SEARCH_MAX_FILES], len(files) > SEARCH_MAX_FILES


def _subsequence(needle: str, text: str) -> int | None:
    """Score for ``needle``'s letters appearing in order in ``text`` (None: no)."""
    at, first, last = 0, -1, -1
    for ch in needle:
        at = text.find(ch, at)
        if at < 0:
            return None
        if first < 0:
            first = at
        last = at
        at += 1
    return 300 - (last - first + 1 - len(needle)) * 6 - first


def _score(tokens: list[str], path: str) -> int | None:
    low = path.lower()
    name = low.rsplit("/", 1)[-1]
    total = 0
    for t in tokens:
        if name == t:
            s: int | None = 2000
        elif name.startswith(t):
            s = 1500 - len(name)
        elif t in name:
            s = 1100 - len(name)
        elif t in low:
            s = 700 - len(low)
        else:
            by_name = _subsequence(t, name)
            s = by_name + 200 if by_name is not None else _subsequence(t, low)
        if s is None:
            return None
        total += s
    return total


def search(root: Path, query: str, limit: int = SEARCH_RESULTS) -> dict[str, Any]:
    """Files whose *path* matches (every word, in order of letters) — fuzzy, best first."""
    tokens = [t for t in str(query or "").lower().split() if t]
    if not tokens:
        return {"ok": True, "query": "", "results": [], "truncated": False}
    files, truncated = _cached("index", root, INDEX_TTL, lambda: _build_index(root))
    scored = []
    for path in files:
        s = _score(tokens, path)
        if s is not None:
            scored.append((-s, len(path), path))
    scored.sort()
    results: list[dict[str, Any]] = []
    marks = _git_state(root)["marks"]
    for _, _, path in scored:
        if not (root / path).is_file():  # tracked but deleted from disk
            continue
        name = path.rsplit("/", 1)[-1]
        results.append({"path": path, "name": name, "dir": path[:-len(name)].rstrip("/"), "git": marks.get(path, "")})
        if len(results) >= max(1, min(int(limit or SEARCH_RESULTS), 200)):
            break
    return {"ok": True, "query": " ".join(tokens), "results": results, "truncated": truncated}


# ─── Read ─────────────────────────────────────────────────────────────────

def _eol(text: str) -> tuple[str, bool]:
    crlf = text.count("\r\n")
    lf = text.count("\n") - crlf
    return ("crlf" if crlf > lf else "lf"), bool(crlf and lf)


def read_file(root: Path, rel: str) -> dict[str, Any]:
    """A file ready for the editor: text (``kind: "text"``), an inline image, or
    a reason it can't be edited (``binary`` / ``large``)."""
    _, real, clean = _locate(root, rel)
    if not real.exists():
        raise FilesError("not_found", f"{clean} doesn't exist any more.")
    if not real.is_file():
        raise FilesError("not_a_file", f"{clean} is a folder, not a file.")
    st = real.stat()
    ext = real.suffix.lower()
    state = _git_state(root)
    base = {"ok": True, "path": clean, "name": real.name, "ext": ext.lstrip("."), "size": st.st_size,
            "mtime": st.st_mtime, "etag": _etag(st), "writable": os.access(real, os.W_OK),
            "repo": state["repo"], "prefix": state["prefix"], "git": state["marks"].get(clean, "")}
    if ext in IMAGE_EXTS:
        if st.st_size > MAX_IMAGE_BYTES:
            return {**base, "kind": "large", "limit": MAX_IMAGE_BYTES}
        mime = mimetypes.guess_type(real.name)[0] or "application/octet-stream"
        data = base64.b64encode(real.read_bytes()).decode("ascii")
        return {**base, "kind": "image", "data_url": f"data:{mime};base64,{data}"}
    if st.st_size > MAX_EDIT_BYTES:
        return {**base, "kind": "large", "limit": MAX_EDIT_BYTES}
    raw = real.read_bytes()
    if b"\0" in raw[:8192]:
        return {**base, "kind": "binary"}
    bom = raw.startswith(BOM)
    try:
        text = raw[len(BOM):].decode("utf-8") if bom else raw.decode("utf-8")
    except UnicodeDecodeError:
        return {**base, "kind": "binary", "reason": "not UTF-8 text"}
    eol, mixed = _eol(text)
    if "\r" in text:
        text = text.replace("\r\n", "\n")
    return {**base, "kind": "text", "content": text, "eol": eol, "mixed_eol": mixed, "bom": bom,
            "lines": text.count("\n") + (0 if text.endswith("\n") or not text else 1)}


def base_file(root: Path, rel: str) -> dict[str, Any]:
    """What git has for this file (``HEAD``), for the editor's change gutter.

    ``base`` is the text (line endings normalised, BOM dropped); ``""`` for a
    new file (everything counts as added); ``None`` when there is nothing to
    compare with — not a repository, an ignored file, or a binary / huge one.
    """
    _, real, clean = _locate(root, rel)
    if not real.is_file():
        raise FilesError("not_found", f"{clean} doesn't exist any more.")
    state = _git_state(root)
    if not state["repo"]:
        return {"ok": True, "path": clean, "repo": False, "tracked": False, "base": None}
    try:
        top = git_api._root(str(root))
    except git_api.GitError:
        return {"ok": True, "path": clean, "repo": False, "tracked": False, "base": None}
    full = f"{state['prefix']}{clean}"
    if git_api._git(top, "cat-file", "-e", f"HEAD:{full}")[0] != 0:
        # Not in HEAD: a new file (untracked / staged) is all "added"; an ignored one has no base.
        new = state["marks"].get(clean) in ("U", "A")
        return {"ok": True, "path": clean, "repo": True, "tracked": False, "base": "" if new else None}
    text = git_api._blob(top, "HEAD", full)
    if text is None:
        return {"ok": True, "path": clean, "repo": True, "tracked": True, "base": None}
    text = text[1:] if text.startswith("\ufeff") else text
    return {"ok": True, "path": clean, "repo": True, "tracked": True, "base": text.replace("\r\n", "\n")}


def stat_file(root: Path, rel: str) -> dict[str, Any]:
    """Cheap change check for the open file (the page polls it)."""
    _, real, clean = _locate(root, rel)
    try:
        st = real.stat()
    except OSError:
        return {"ok": True, "path": clean, "exists": False}
    return {"ok": True, "path": clean, "exists": True, "etag": _etag(st), "size": st.st_size}


# ─── Write ────────────────────────────────────────────────────────────────

def _atomic_write(target: Path, data: bytes) -> None:
    fd, tmp = tempfile.mkstemp(prefix=".jarvis-save-", dir=target.parent)
    try:
        with os.fdopen(fd, "wb") as fh:
            fh.write(data)
            fh.flush()
            os.fsync(fh.fileno())
        if target.exists():
            shutil.copymode(target, tmp)
        os.replace(tmp, target)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def write_file(root: Path, rel: str, content: Any, *, etag: str = "", eol: str = "lf", bom: bool = False,
               force: bool = False) -> dict[str, Any]:
    if not isinstance(content, str):
        raise FilesError("bad_content", "Nothing to save.")
    _, real, clean = _locate(root, rel)
    if not real.is_file():
        raise FilesError("not_found", f"{clean} doesn't exist any more — it was moved or deleted.")
    if not os.access(real, os.W_OK):
        raise FilesError("read_only", f"{clean} is read-only.")
    if not force and etag and _etag(real.stat()) != etag:
        raise FilesError("conflict", "This file changed on disk since you opened it.")
    text = content.replace("\r\n", "\n")
    if eol == "crlf":
        text = text.replace("\n", "\r\n")
    data = (BOM if bom else b"") + text.encode("utf-8")
    if len(data) > MAX_EDIT_BYTES:
        raise FilesError("too_large", "That is too large to save from here.")
    _atomic_write(real, data)
    st = real.stat()
    return {"ok": True, "path": clean, "etag": _etag(st), "size": st.st_size, "mtime": st.st_mtime}


def create(root: Path, rel: str, kind: str = "file") -> dict[str, Any]:
    entry, _, clean = _locate(root, rel)
    if not clean:
        raise FilesError("bad_name", "Give it a name.")
    _valid_name(entry.name)
    if os.path.lexists(entry):
        raise FilesError("exists", f"{clean} already exists.")
    try:
        entry.parent.mkdir(parents=True, exist_ok=True)
        if kind == "dir":
            entry.mkdir()
        else:
            with open(entry, "x", encoding="utf-8"):
                pass
    except FileExistsError:
        raise FilesError("exists", f"{clean} already exists.")
    return {"ok": True, "path": clean, "dir": kind == "dir"}


def rename(root: Path, rel: str, to: str) -> dict[str, Any]:
    src, _, clean = _locate(root, rel)
    dest, _, new_clean = _locate(root, to)
    if not clean or not new_clean:
        raise FilesError("bad_name", "Give it a name.")
    _valid_name(dest.name)
    if not os.path.lexists(src):
        raise FilesError("not_found", f"{clean} doesn't exist any more.")
    if new_clean == clean:
        return {"ok": True, "path": clean, "unchanged": True}
    if new_clean.startswith(f"{clean}/"):
        raise FilesError("bad_name", "A folder can't move into itself.")
    # Case-only renames on a case-insensitive disk "exist" already: same file.
    if os.path.lexists(dest) and not (dest.exists() and src.exists() and os.path.samefile(src, dest)):
        raise FilesError("exists", f"{new_clean} already exists.")
    dest.parent.mkdir(parents=True, exist_ok=True)
    os.rename(src, dest)
    return {"ok": True, "path": new_clean, "from": clean}


def _trash_dir() -> Path:
    home = Path.home()
    path = home / ".Trash" if sys.platform == "darwin" else home / ".local" / "share" / "Trash" / "files"
    path.mkdir(parents=True, exist_ok=True)
    return path


def trash_copy(path: Path, note: str = "discarded") -> Path:
    """Put a *copy* of ``path`` in the Trash (``name (discarded).ext``) — the
    safety net before git overwrites a file's text."""
    trash = _trash_dir()
    dest = trash / f"{path.stem} ({note}){path.suffix}"
    n = 1
    while os.path.lexists(dest):
        n += 1
        dest = trash / f"{path.stem} ({note} {n}){path.suffix}"
    shutil.copy2(str(path), str(dest))
    return dest


def delete(root: Path, rel: str) -> dict[str, Any]:
    """Move to the Trash (recoverable) — never unlink."""
    entry, _, clean = _locate(root, rel)
    if not clean:
        raise FilesError("protected", "The project folder itself can't be deleted from here.")
    if not os.path.lexists(entry):
        raise FilesError("not_found", f"{clean} doesn't exist any more.")
    trash = _trash_dir()
    dest = trash / entry.name
    n = 1
    while os.path.lexists(dest):
        n += 1
        dest = trash / f"{entry.stem} {n}{entry.suffix}" if entry.is_file() else trash / f"{entry.name} {n}"
    shutil.move(str(entry), str(dest))
    return {"ok": True, "path": clean, "trash": str(dest)}


# ─── Entry point for the HTTP handler ─────────────────────────────────────

def resolve_inside(cwd: str, rel: str) -> Path:
    """The absolute path of a project-relative ``rel`` (``FilesError`` if it escapes)."""
    return _locate(_root(cwd), rel)[1]


def post(op: str, data: dict[str, Any], *, cwd: str | None = None) -> dict[str, Any]:
    root = _root(cwd)
    rel = data.get("path", "")
    try:
        if op == "list":
            return list_dir(root, rel)
        if op == "read":
            return read_file(root, rel)
        if op == "stat":
            return stat_file(root, rel)
        if op == "base":
            return base_file(root, rel)
        if op == "search":
            return search(root, str(data.get("query") or ""), int(data.get("limit") or SEARCH_RESULTS))
        if op in MUTATING:
            with _lock:
                try:
                    if op == "write":
                        return write_file(root, rel, data.get("content"), etag=str(data.get("etag") or ""),
                                          eol="crlf" if data.get("eol") == "crlf" else "lf",
                                          bom=bool(data.get("bom")), force=bool(data.get("force")))
                    if op == "create":
                        return create(root, rel, "dir" if data.get("kind") == "dir" else "file")
                    if op == "rename":
                        return rename(root, rel, str(data.get("to") or ""))
                    return delete(root, rel)
                finally:
                    _forget(root)
    except FilesError as exc:
        return {"ok": False, "code": exc.code, "error": str(exc)}
    except OSError as exc:
        return {"ok": False, "code": "io", "error": f"{exc.strerror or exc}"}
    return {"ok": False, "error": f"unknown files op: {op}"}
