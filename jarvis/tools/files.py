"""File tools: read_file, write_file, edit_file."""
import pathlib

from ..constants import CWD, MAX_FILE_READ, MAX_FILE_SIZE_BYTES, MAX_FILE_CHUNK_BYTES
from .. import state
from .dirs import SKIP_DIRS
from ..path_resolve import robust_resolve

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


def _save_backup(p: pathlib.Path):
    if p.exists():
        state.backups.append((str(p), p.read_text(errors="ignore")))


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


def read_file(path: str, offset: int = 0, limit: int = 0, force: bool = False) -> str:
    p = robust_resolve(path)
    if not p.exists(): return f"ERROR: {path} not found"
    if p.is_dir(): return f"ERROR: {path} is a directory"

    if not force:
        skip = _in_skip_dir(p)
        if skip:
            return (f"ERROR: refused to read '{path}' — inside '{skip}/' "
                    f"(node_modules, build artifacts, caches are blocked). "
                    f"Pass force=true only if the user explicitly asked for this file.")

        if p.suffix.lower() in _BINARY_EXTS:
            return (f"ERROR: refused to read '{path}' — binary/non-text extension "
                    f"'{p.suffix}'. Use `read_document` for PDF, images, CSV, "
                    f"Excel, JSON, etc., or pass force=true if the user explicitly asked.")

        try:
            if p.stat().st_size > MAX_FILE_SIZE_BYTES:
                return (f"ERROR: '{path}' is {p.stat().st_size} bytes "
                        f"(>{MAX_FILE_SIZE_BYTES:,} bytes). "
                        f"Use offset/limit to page through it, or pass force=true.")
        except OSError:
            pass

        if _looks_binary(p):
            return (f"ERROR: '{path}' appears to be a binary file. "
                    f"Pass force=true only if you are sure it is text.")

    txt = p.read_text(errors="ignore")
    if offset or limit:
        lines = txt.splitlines()
        end = offset + limit if limit else len(lines)
        return "\n".join(f"{i+1}\t{l}" for i, l in enumerate(lines[offset:end], start=offset))
    return txt[:MAX_FILE_READ]


def write_file(path: str, content: str) -> str:
    p = robust_resolve(path)
    _save_backup(p)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(content)
    return f"WROTE {p} ({len(content)} bytes)"


def edit_file(path: str, old_str: str, new_str: str, replace_all: bool = False) -> str:
    p = robust_resolve(path)
    if not p.exists(): return f"ERROR: {path} not found"
    txt = p.read_text(errors="ignore")
    n = txt.count(old_str)
    if n == 0: return "ERROR: old_str not found"
    if n > 1 and not replace_all:
        return f"ERROR: old_str matches {n} times; pass replace_all=true or add more context"
    _save_backup(p)
    new_txt = txt.replace(old_str, new_str) if replace_all else txt.replace(old_str, new_str, 1)
    p.write_text(new_txt)
    return f"EDITED {p} ({n} replacement{'s' if n>1 else ''})"
