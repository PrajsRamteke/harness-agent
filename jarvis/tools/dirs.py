"""Directory listing & glob."""
import os, pathlib
from ..constants import CWD


def list_dir(path: str = ".") -> str:
    p = (CWD / path).resolve() if not os.path.isabs(path) else pathlib.Path(path)
    if not p.exists(): return f"ERROR: {path} not found"
    items = sorted(p.iterdir(), key=lambda x: (not x.is_dir(), x.name.lower()))[:300]
    return "\n".join(f"{'d' if x.is_dir() else 'f'} {x.name}" for x in items)


def glob_files(pattern: str) -> str:
    matches = sorted(pathlib.Path(CWD).glob(pattern))[:200]
    return "\n".join(str(m.relative_to(CWD)) for m in matches) or "no matches"
