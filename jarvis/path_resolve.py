"""Resolve user-supplied paths when the final component differs only by Unicode space characters.

macOS screenshot names often use U+202F (narrow no-break space) before AM/PM. Typed paths
use a normal space (U+0020), so pathlib reports missing files unless we match loosely.
"""
from __future__ import annotations

import os
import pathlib
import unicodedata

from .constants import CWD


def _unicode_space_key(name: str) -> str:
    """Map filename to a form comparable across Zs (space separator) code points."""
    return "".join(" " if unicodedata.category(ch) == "Zs" else ch for ch in name)


def robust_resolve(path: str, cwd: pathlib.Path | None = None) -> pathlib.Path:
    """Like pathlib resolve, but if the path is missing, retry matching the basename in
    its parent directory when names differ only by Unicode space characters."""
    cwd = cwd or CWD
    raw = os.path.expanduser((path or "").strip())
    base = (cwd / raw).resolve() if not os.path.isabs(raw) else pathlib.Path(raw).resolve()
    if base.exists():
        return base
    parent, name = base.parent, base.name
    if not name or not parent.is_dir():
        return base
    key = _unicode_space_key(name)
    try:
        for ent in parent.iterdir():
            if _unicode_space_key(ent.name) == key:
                return ent.resolve()
    except OSError:
        pass
    return base


def is_within(path: pathlib.Path, root: pathlib.Path | None = None) -> bool:
    """Return True when ``path`` is inside ``root`` after resolving both."""
    root = (root or CWD).resolve()
    try:
        path.resolve().relative_to(root)
        return True
    except ValueError:
        return False


def project_scope_error(path: pathlib.Path, tool: str, escape: str = "force=true") -> str | None:
    """Default guard for codebase tools: stay inside the current project."""
    if is_within(path, CWD):
        return None
    return (
        f"ERROR: {tool} refused outside-project path '{path}'. "
        f"This codebase is rooted at '{CWD}'. Use a project-relative path, "
        f"or pass {escape} only when the user explicitly asked for that outside file."
    )
