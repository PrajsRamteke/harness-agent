"""Format tool output for the transcript vs full scrollable viewer."""
from __future__ import annotations

from rich.text import Text

from ..constants import TOOL_UI_PREVIEW_CHARS, TOOL_UI_PREVIEW_LINES, TOOL_UI_VIEWER_MAX_CHARS


def format_tool_output_preview(out_str: str) -> tuple[Text, bool]:
    """Build transcript preview text. Returns ``(body, was_truncated)``."""
    raw = (out_str or "").strip()
    if not raw:
        return Text(""), False

    lines = raw.splitlines()
    truncated = len(lines) > TOOL_UI_PREVIEW_LINES or len(raw) > TOOL_UI_PREVIEW_CHARS
    if not truncated:
        return Text(raw), False

    preview = "\n".join(lines[:TOOL_UI_PREVIEW_LINES])
    if len(preview) > TOOL_UI_PREVIEW_CHARS:
        preview = preview[:TOOL_UI_PREVIEW_CHARS]

    body = Text(preview)
    body.append(
        f"\n\n… {len(raw):,} chars · {len(lines)} lines · "
        f"Ctrl+F for scrollable full output",
        style="dim",
    )
    return body, True


def viewer_text(content: str) -> str:
    """Cap content shown in the scrollable tool-output modal."""
    raw = content or ""
    if len(raw) <= TOOL_UI_VIEWER_MAX_CHARS:
        return raw
    return (
        raw[:TOOL_UI_VIEWER_MAX_CHARS]
        + f"\n\n… truncated for viewer ({len(raw):,} chars total)"
    )
