"""Shorten paths and shell commands for display (never for execution).

``/Users/me/proj/src/a.py`` → ``src/a.py`` inside the working directory,
``/Users/me/other`` → ``~/other`` elsewhere, and a redundant leading
``cd <cwd> &&`` is dropped from shell commands.
"""
from __future__ import annotations

import pathlib
import re

_CD_PREFIX = re.compile(r"""^\s*cd\s+("[^"]+"|'[^']+'|\S+)\s*(?:&&|;)\s*(.+)$""", re.S)


def shorten_paths(text: str) -> str:
    """Rewrite absolute paths under cwd as relative and under home as ``~``."""
    if not text or "/" not in text:
        return text or ""
    try:
        cwd = str(pathlib.Path.cwd())
        home = str(pathlib.Path.home())
    except Exception:
        return text
    if cwd and cwd != "/":
        text = re.sub(rf"(?<![\w.~/-]){re.escape(cwd)}/", "", text)
        text = re.sub(rf"(?<![\w.~/-]){re.escape(cwd)}(?![\w./-])", ".", text)
    if home and home != "/":
        text = re.sub(rf"(?<![\w.~/-]){re.escape(home)}(?=/|\b)", "~", text)
    return text


def _is_cwd(target: str) -> bool:
    target = target.strip("\"'")
    if target in (".", "./"):
        return True
    try:
        return pathlib.Path(target).expanduser().resolve() == pathlib.Path.cwd().resolve()
    except Exception:
        return False


def shorten_command(cmd: str) -> str:
    """Display form of a shell command: no ``cd <cwd> &&``, short paths."""
    text = " ".join((cmd or "").split())
    for _ in range(3):  # "cd x && cd y && …"
        m = _CD_PREFIX.match(text)
        if not m or not _is_cwd(m.group(1)):
            break
        text = m.group(2).strip()
    return shorten_paths(text)
