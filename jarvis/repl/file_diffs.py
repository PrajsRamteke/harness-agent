"""Inline, real-time unified diffs for file create/edit tools.

When ``write_file`` / ``edit_file`` / ``multi_edit`` change a file, the tool
handlers call :func:`emit_file_diff` so the user sees exactly what changed,
rendered as a color-coded diff panel in the transcript the moment the write
lands. The shared ``console`` is thread-safe in the TUI (writes are marshalled
to the main thread) and prints directly in the legacy Rich REPL, so the same
call works in both front-ends.
"""
from __future__ import annotations

import difflib
import pathlib

from ..console import console, Panel
from ..constants import CWD
from .. import state

try:
    from ..tui import theme as _ui
except Exception:  # pragma: no cover — headless / import-order fallback
    from types import SimpleNamespace

    _ui = SimpleNamespace(
        OK="#3fb950", ERR="#f85149", FG_DIM="#6b7684",
        ACCENT="#58a6ff", WARN="#e3b341", SEP="#1f2630",
    )

# Keep diff panels readable: cap total rendered lines and per-line width so a
# 3,000-line new file or a minified bundle can't flood the transcript.
_MAX_DIFF_LINES = 200
_MAX_LINE_LEN = 500


def _short_path(path: str) -> str:
    """Project-relative path when possible, else the absolute path."""
    try:
        return str(pathlib.Path(path).resolve().relative_to(CWD))
    except Exception:
        return path


def _clip(line: str) -> str:
    line = line.rstrip("\n")
    if len(line) > _MAX_LINE_LEN:
        return line[:_MAX_LINE_LEN] + " …"
    return line


def _build_diff_body(before: str, after: str) -> tuple[object | None, int, int]:
    """Return (renderable, added, removed). renderable is None when identical."""
    from rich.text import Text

    before_lines = before.splitlines()
    after_lines = after.splitlines()
    diff = list(
        difflib.unified_diff(before_lines, after_lines, lineterm="", n=3)
    )
    if not diff:
        return None, 0, 0

    added = sum(1 for d in diff if d.startswith("+") and not d.startswith("+++"))
    removed = sum(1 for d in diff if d.startswith("-") and not d.startswith("---"))

    body = Text()
    shown = 0
    for ln in diff:
        # Drop the synthetic ---/+++ file headers; we render our own title.
        if ln.startswith("---") or ln.startswith("+++"):
            continue
        if shown >= _MAX_DIFF_LINES:
            body.append(
                f"  … diff truncated ({len(diff) - shown} more lines)\n",
                style=_ui.FG_DIM,
            )
            break
        text = _clip(ln)
        if ln.startswith("@@"):
            style = _ui.ACCENT
        elif ln.startswith("+"):
            style = _ui.OK
        elif ln.startswith("-"):
            style = _ui.ERR
        else:
            style = _ui.FG_DIM
        body.append(text + "\n", style=style)
        shown += 1

    return body, added, removed


def emit_file_diff(path: str, before: str, after: str, *, action: str = "write") -> None:
    """Render a live unified diff panel for a file write/edit. Never raises."""
    try:
        if not getattr(state, "show_file_diffs", True):
            return
        if before == after:
            return  # no-op write — nothing to show

        body, added, removed = _build_diff_body(before, after)
        if body is None:
            return

        rel = _short_path(path)
        verb = {"create": "new file", "write": "rewrote", "edit": "edited"}.get(
            action, action
        )
        title = f"✎ {verb} · {rel}  [+{added} -{removed}]"
        console.print(
            Panel(
                body,
                title=title,
                title_align="left",
                border_style=_ui.ACCENT,
                padding=(0, 1),
            )
        )
    except Exception:
        # Diff display is best-effort decoration — never break a file write.
        pass
