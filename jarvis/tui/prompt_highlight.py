"""@file mention highlighting for the composer TextArea."""
from __future__ import annotations

import re
from collections import defaultdict
from typing import DefaultDict, List, Tuple

from rich.style import Style
from textual._text_area_theme import TextAreaTheme

from ..prompt_refs import active_file_ref_at_cursor

Highlight = Tuple[int, int | None, str]
HighlightMap = dict[int, List[Highlight]]

PROMPT_THEME_NAME = "jarvis_prompt"
_FILE_REF_LINE_RE = re.compile(r'@(?:"([^"]+)"|([^\s@]+))')


def build_prompt_text_area_theme() -> TextAreaTheme:
    """Theme-aware TextArea theme for @file chips in the composer."""
    from . import theme as ui

    return TextAreaTheme(
        name=PROMPT_THEME_NAME,
        base_style=Style(color=ui.FG, bgcolor=ui.BG_2),
        cursor_line_style=Style(bgcolor=ui.BG_2),
        cursor_style=Style(color=ui.BG_0, bgcolor=ui.BORDER_FC),
        syntax_styles={
            "file_ref": Style(color=ui.ACCENT_2, bgcolor=ui.BG_4, bold=True),
            "file_ref_active": Style(color=ui.FG, bgcolor=ui.ACCENT, bold=True),
        },
    )


def _spans_overlap(start: int, end: int, o_start: int, o_end: int) -> bool:
    return start < o_end and o_start < end


def build_file_ref_highlights(
    text: str,
    *,
    cursor_row: int | None = None,
    cursor_col: int | None = None,
) -> HighlightMap:
    """Map line numbers to highlight spans for completed and in-progress @ mentions."""
    highlights: DefaultDict[int, List[Highlight]] = defaultdict(list)
    lines = (text or "").split("\n")

    for row, line in enumerate(lines):
        for match in _FILE_REF_LINE_RE.finditer(line):
            highlights[row].append((match.start(), match.end(), "file_ref"))

    if cursor_row is None or cursor_col is None:
        return dict(highlights)

    active = active_file_ref_at_cursor(text, cursor_row, cursor_col)
    if not active:
        return dict(highlights)

    row, start_col, _query = active
    end_col = max(start_col + 1, cursor_col)
    row_spans = [
        span
        for span in highlights[row]
        if not _spans_overlap(span[0], span[1] or span[0], start_col, end_col)
    ]
    row_spans.append((start_col, end_col, "file_ref_active"))
    row_spans.sort(key=lambda span: span[0])
    highlights[row] = row_spans
    return dict(highlights)
