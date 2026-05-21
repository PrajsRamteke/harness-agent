"""File tools: read_file, write_file, edit_file."""
import pathlib
import threading
from collections import OrderedDict
from contextlib import contextmanager

from ..constants import CWD, MAX_FILE_READ, MAX_FILE_SIZE_BYTES, MAX_FILE_CHUNK_BYTES
from .. import state
from .dirs import SKIP_DIRS
from ..path_resolve import project_scope_error, robust_resolve

# Extensions that are almost never useful to read as text.
_BINARY_EXTS = {
    ".png", ".jpg", ".jpeg", ".gif", ".bmp", ".tiff", ".tif", ".webp", ".heic",
    ".ico", ".icns", ".pdf", ".zip", ".tar", ".gz", ".tgz", ".bz2", ".xz",
    ".7z", ".rar", ".jar", ".war", ".class", ".exe", ".dll", ".so", ".dylib",
    ".o", ".a", ".obj", ".bin", ".dat", ".db", ".sqlite", ".sqlite3",
    ".pyc", ".pyo", ".pyd", ".whl", ".egg",
    ".mp3", ".mp4", ".mov", ".avi", ".mkv", ".wav", ".flac", ".ogg",
    ".ttf", ".otf", ".woff", ".woff2", ".eot",
    ".lock",  # yarn.lock / package-lock.json noise — usually not useful
}

# Known text/code extensions — skip the extra 4KB binary sniff on read.
_TEXT_EXTS = {
    ".py", ".pyi", ".js", ".jsx", ".ts", ".tsx", ".mjs", ".cjs",
    ".json", ".yaml", ".yml", ".toml", ".ini", ".cfg", ".conf",
    ".md", ".mdx", ".rst", ".txt", ".log", ".csv", ".tsv",
    ".html", ".htm", ".xml", ".css", ".scss", ".sass", ".less",
    ".sh", ".bash", ".zsh", ".fish", ".ps1", ".bat", ".cmd",
    ".sql", ".go", ".rs", ".java", ".kt", ".swift", ".rb", ".php",
    ".c", ".h", ".cpp", ".hpp", ".cc", ".cs", ".vue", ".svelte",
    ".dockerfile", ".env", ".gitignore", ".editorconfig",
}

_READ_CACHE_MAX = 96
_read_cache: "OrderedDict[str, tuple[float, int, str]]" = OrderedDict()
_path_locks: dict[str, threading.Lock] = {}
_path_locks_guard = threading.Lock()
_BACKUP_MAX_BYTES = 1_000_000


@contextmanager
def _path_lock(p: pathlib.Path):
    """Serialize read/write/edit on the same resolved path; different paths run in parallel."""
    key = str(p.resolve())
    with _path_locks_guard:
        lk = _path_locks.setdefault(key, threading.Lock())
    with lk:
        yield


def _cache_get(p: pathlib.Path) -> str | None:
    key = str(p.resolve())
    try:
        st = p.stat()
    except OSError:
        return None
    hit = _read_cache.get(key)
    if hit and hit[0] == st.st_mtime and hit[1] == st.st_size:
        _read_cache.move_to_end(key)
        return hit[2]
    return None


def _cache_put(p: pathlib.Path, content: str) -> None:
    key = str(p.resolve())
    try:
        st = p.stat()
    except OSError:
        return
    _read_cache[key] = (st.st_mtime, st.st_size, content)
    _read_cache.move_to_end(key)
    while len(_read_cache) > _READ_CACHE_MAX:
        _read_cache.popitem(last=False)


def _cache_invalidate(p: pathlib.Path) -> None:
    _read_cache.pop(str(p.resolve()), None)


def _save_backup(p: pathlib.Path):
    if not p.exists():
        return
    try:
        if p.stat().st_size > _BACKUP_MAX_BYTES:
            return
        state.backups.append((str(p), p.read_text(errors="ignore")))
    except OSError:
        pass


def _in_skip_dir(p: pathlib.Path) -> str | None:
    """Return the offending skip-dir name if p is inside one, else None."""
    for part in p.parts:
        if part in SKIP_DIRS:
            return part
    return None


def _looks_binary(p: pathlib.Path) -> bool:
    """Cheap binary sniff: read up to 4KB and look for NUL bytes or very
    low printable-ratio."""
    try:
        with p.open("rb") as fh:
            chunk = fh.read(MAX_FILE_CHUNK_BYTES)
    except Exception:
        return False
    if not chunk:
        return False
    if b"\x00" in chunk:
        return True
    # count printable bytes (tab, newline, carriage-return, 0x20-0x7e + utf-8 high bits)
    printable = sum(1 for b in chunk if b in (9, 10, 13) or 32 <= b <= 126 or b >= 128)
    return (printable / len(chunk)) < 0.85


def _read_lines_slice(p: pathlib.Path, offset: int, limit: int) -> str:
    """Read only the requested line range — avoids loading huge files whole."""
    end = offset + limit if limit else None
    out: list[str] = []
    with p.open("r", encoding="utf-8", errors="ignore") as fh:
        for i, line in enumerate(fh):
            if i < offset:
                continue
            if end is not None and i >= end:
                break
            out.append(f"{i + 1}\t{line.rstrip(chr(10) + chr(13))}")
    return "\n".join(out)


def read_file(path: str, offset: int = 0, limit: int = 0, force: bool = False) -> str:
    p = robust_resolve(path)
    if not p.exists():
        return f"ERROR: {path} not found"
    if p.is_dir():
        return f"ERROR: {path} is a directory"

    with _path_lock(p):
        try:
            st = p.stat()
        except OSError:
            st = None

        if not force:
            scope_err = project_scope_error(p, "read_file")
            if scope_err:
                return scope_err

            skip = _in_skip_dir(p)
            if skip:
                return (
                    f"ERROR: refused to read '{path}' — inside '{skip}/' "
                    f"(node_modules, build artifacts, caches are blocked). "
                    f"Pass force=true only if the user explicitly asked for this file."
                )

            if p.suffix.lower() in _BINARY_EXTS:
                return (
                    f"ERROR: refused to read '{path}' — binary/non-text extension "
                    f"'{p.suffix}'. Use `read_document` for PDF, images, CSV, "
                    f"Excel, JSON, etc., or pass force=true if the user explicitly asked."
                )

            if st and st.st_size > MAX_FILE_SIZE_BYTES:
                if not (offset or limit):
                    return (
                        f"ERROR: '{path}' is {st.st_size} bytes "
                        f"(>{MAX_FILE_SIZE_BYTES:,} bytes). "
                        f"Use offset/limit to page through it, or pass force=true."
                    )

            if p.suffix.lower() not in _TEXT_EXTS and _looks_binary(p):
                return (
                    f"ERROR: '{path}' appears to be a binary file. "
                    f"Pass force=true only if you are sure it is text."
                )

        if offset or limit:
            return _read_lines_slice(p, offset, limit)

        cached = _cache_get(p)
        if cached is not None:
            return cached[:MAX_FILE_READ]

        txt = p.read_text(errors="ignore")
        _cache_put(p, txt)
        return txt[:MAX_FILE_READ]


def write_file(path: str, content: str, allow_outside_project: bool = False) -> str:
    p = robust_resolve(path)
    if not allow_outside_project:
        scope_err = project_scope_error(p, "write_file", "allow_outside_project=true")
        if scope_err:
            return scope_err
    with _path_lock(p):
        _save_backup(p)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content)
        _cache_invalidate(p)
    return f"WROTE {p} ({len(content)} bytes)"


def _apply_text_edit(
    txt: str,
    old_str: str,
    new_str: str,
    replace_all: bool = False,
) -> tuple[str | None, str | None, int]:
    """Return (new_text, error_message, match_count)."""
    n = txt.count(old_str)
    if n == 0:
        return None, "old_str not found", 0
    if n > 1 and not replace_all:
        return (
            None,
            f"old_str matches {n} times; pass replace_all=true or add more context",
            n,
        )
    new_txt = txt.replace(old_str, new_str) if replace_all else txt.replace(old_str, new_str, 1)
    replacements = n if replace_all else 1
    return new_txt, None, replacements


def edit_file(
    path: str,
    old_str: str,
    new_str: str,
    replace_all: bool = False,
    allow_outside_project: bool = False,
) -> str:
    p = robust_resolve(path)
    if not allow_outside_project:
        scope_err = project_scope_error(p, "edit_file", "allow_outside_project=true")
        if scope_err:
            return scope_err
    with _path_lock(p):
        if not p.exists():
            return f"ERROR: {path} not found"
        txt = p.read_text(errors="ignore")
        new_txt, err, n = _apply_text_edit(txt, old_str, new_str, replace_all)
        if err:
            return f"ERROR: {err}"
        _save_backup(p)
        p.write_text(new_txt)
        _cache_invalidate(p)
    return f"EDITED {p} ({n} replacement{'s' if n > 1 else ''})"


_MULTI_EDIT_MAX = 30


def multi_edit(
    edits: list | None = None,
    allow_outside_project: bool = False,
) -> str:
    """Apply multiple search-replace edits in one call (same rules as edit_file)."""
    if not edits:
        return "ERROR: no edits provided"
    if not isinstance(edits, list):
        return "ERROR: edits must be an array"
    if len(edits) > _MULTI_EDIT_MAX:
        return f"ERROR: max {_MULTI_EDIT_MAX} edits per call (got {len(edits)})"

    lines: list[str] = []
    ok = fail = 0
    i = 0
    while i < len(edits):
        raw = edits[i]
        if not isinstance(raw, dict):
            fail += 1
            lines.append(f"{i + 1}/{len(edits)} ERROR: edit must be an object")
            i += 1
            continue

        path = str(raw.get("path") or "").strip()
        if not path:
            fail += 1
            lines.append(f"{i + 1}/{len(edits)} ERROR: path is required")
            i += 1
            continue

        block: list[dict] = [raw]
        j = i + 1
        while j < len(edits) and isinstance(edits[j], dict) and str(edits[j].get("path") or "").strip() == path:
            block.append(edits[j])
            j += 1

        p = robust_resolve(path)
        if not allow_outside_project:
            scope_err = project_scope_error(p, "multi_edit", "allow_outside_project=true")
            if scope_err:
                for k, _ in enumerate(block):
                    fail += 1
                    lines.append(f"{i + k + 1}/{len(edits)} ERROR {path}: {scope_err}")
                i = j
                continue

        with _path_lock(p):
            if not p.exists():
                for k, _ in enumerate(block):
                    fail += 1
                    lines.append(f"{i + k + 1}/{len(edits)} ERROR {path}: not found")
                i = j
                continue

            txt = p.read_text(errors="ignore")
            backed_up = False
            for k, edit in enumerate(block):
                idx = i + k + 1
                old_str = edit.get("old_str")
                new_str = edit.get("new_str")
                if old_str is None or new_str is None:
                    fail += 1
                    lines.append(f"{idx}/{len(edits)} ERROR {path}: old_str and new_str are required")
                    continue

                new_txt, err, n = _apply_text_edit(
                    txt,
                    str(old_str),
                    str(new_str),
                    bool(edit.get("replace_all")),
                )
                if err:
                    fail += 1
                    lines.append(f"{idx}/{len(edits)} ERROR {path}: {err}")
                    continue

                if not backed_up:
                    _save_backup(p)
                    backed_up = True
                txt = new_txt
                ok += 1
                lines.append(f"{idx}/{len(edits)} EDITED {path} ({n} replacement{'s' if n > 1 else ''})")

            if backed_up:
                p.write_text(txt)
                _cache_invalidate(p)

        i = j

    summary = f"{ok} succeeded, {fail} failed"
    return summary + ("\n" + "\n".join(lines) if lines else "")
