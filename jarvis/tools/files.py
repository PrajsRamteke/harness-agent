"""File tools: read_file, write_file, edit_file."""
import os, pathlib

from ..constants import CWD, MAX_FILE_READ
from .. import state


def _save_backup(p: pathlib.Path):
    if p.exists():
        state.backups.append((str(p), p.read_text(errors="ignore")))


def read_file(path: str, offset: int = 0, limit: int = 0) -> str:
    p = (CWD / path).resolve() if not os.path.isabs(path) else pathlib.Path(path)
    if not p.exists(): return f"ERROR: {path} not found"
    if p.is_dir(): return f"ERROR: {path} is a directory"
    txt = p.read_text(errors="ignore")
    if offset or limit:
        lines = txt.splitlines()
        end = offset + limit if limit else len(lines)
        return "\n".join(f"{i+1}\t{l}" for i, l in enumerate(lines[offset:end], start=offset))
    return txt[:MAX_FILE_READ]


def write_file(path: str, content: str) -> str:
    p = (CWD / path).resolve() if not os.path.isabs(path) else pathlib.Path(path)
    _save_backup(p)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(content)
    return f"WROTE {p} ({len(content)} bytes)"


def edit_file(path: str, old_str: str, new_str: str, replace_all: bool = False) -> str:
    p = (CWD / path).resolve() if not os.path.isabs(path) else pathlib.Path(path)
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
