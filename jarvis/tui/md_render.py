"""Fast, theme-aware markdown → ``rich.Text`` for transcript messages.

Textual's ``Markdown`` widget builds a deep widget tree per message, which
made long sessions slow to mount, scroll and resize. Instead each message
renders its markdown with Rich into plain styled lines at the block's width
and shows them in a single ``Static`` — cheap to lay out, cached per width,
and still selectable/copyable (the result is ``Text``).

Look: left-aligned bold headings in the accent color (no centered/boxed
H1), code blocks on a tinted panel with a palette-matched syntax theme,
subtle inline code, dim bullets and quotes.
"""
from __future__ import annotations

import re
from functools import lru_cache

from rich.console import Console, ConsoleOptions, RenderResult
from rich.markdown import CodeBlock, Heading, Markdown
from rich.style import Style
from rich.syntax import Syntax
from rich.text import Text
from rich.theme import Theme

from . import theme as ui

_CODE_THEMES = {
    "dracula": "dracula",
    "gruvbox": "gruvbox-dark",
    "nord": "nord",
    "monochrome": "bw",
    "tokyonight": "one-dark",
    "catppuccin": "one-dark",
}


def _code_theme() -> str:
    return _CODE_THEMES.get(ui.active_theme(), "one-dark")


def _code_bg() -> str:
    return ui.blend(ui.BG_0, ui.BG_2, 0.9)


class _Heading(Heading):
    def __rich_console__(self, console: Console, options: ConsoleOptions) -> RenderResult:
        text = self.text.copy()
        text.justify = "left"
        yield text


class _CodeBlock(CodeBlock):
    def __rich_console__(self, console: Console, options: ConsoleOptions) -> RenderResult:
        code = str(self.text).rstrip()
        yield Syntax(
            code,
            self.lexer_name,
            theme=_code_theme(),
            word_wrap=True,
            padding=(1, 2),
            background_color=_code_bg(),
        )


class JarvisMarkdown(Markdown):
    elements = {
        **Markdown.elements,
        "heading_open": _Heading,
        "fence": _CodeBlock,
        "code_block": _CodeBlock,
    }


def _rich_theme() -> Theme:
    return Theme({
        "markdown.paragraph": Style(color=ui.FG),
        "markdown.text": Style(color=ui.FG),
        "markdown.code": Style(color=ui.ACCENT_3, bgcolor=ui.BG_3),
        "markdown.code_block": Style(color=ui.FG, bgcolor=_code_bg()),
        "markdown.block_quote": Style(color=ui.FG_MUTE, italic=True),
        "markdown.list": Style(color=ui.FG),
        "markdown.item": Style(color=ui.FG),
        "markdown.item.bullet": Style(color=ui.FG_DIM),
        "markdown.item.number": Style(color=ui.FG_DIM),
        "markdown.hr": Style(color=ui.BORDER),
        "markdown.h1.border": Style(color=ui.BORDER),
        "markdown.h1": Style(color=ui.ACCENT, bold=True),
        "markdown.h2": Style(color=ui.ACCENT, bold=True),
        "markdown.h3": Style(color=ui.FG, bold=True),
        "markdown.h4": Style(color=ui.FG, bold=True, italic=True),
        "markdown.h5": Style(color=ui.FG_MUTE, bold=True),
        "markdown.h6": Style(color=ui.FG_MUTE, italic=True),
        "markdown.link": Style(color=ui.ACCENT_2, underline=True),
        "markdown.link_url": Style(color=ui.FG_DIM),
        "markdown.table.border": Style(color=ui.BORDER),
        "markdown.table.header": Style(color=ui.FG, bold=True),
        "markdown.kbd": Style(color=ui.ACCENT_3, bold=True),
    })


@lru_cache(maxsize=32)
def _console(theme_name: str, width: int) -> Console:
    del theme_name  # cache key only — colors come from ui.* at creation
    return Console(
        width=width,
        color_system="truecolor",
        force_terminal=True,
        legacy_windows=False,
        theme=_rich_theme(),
        file=_NullFile(),
        emoji=False,
        highlight=False,
    )


class _NullFile:
    def write(self, *_a) -> int:
        return 0

    def flush(self) -> None:
        pass

    def isatty(self) -> bool:
        return True


def render_markdown(source: str, width: int) -> Text:
    """Render markdown to styled, pre-wrapped lines as one ``Text``."""
    width = max(10, int(width))
    src = source or ""
    if not src.strip():
        return Text("")
    console = _console(ui.active_theme(), width)
    try:
        lines = console.render_lines(
            JarvisMarkdown(src, code_theme=_code_theme(), hyperlinks=True),
            console.options.update_width(width),
            pad=False,
        )
    except Exception:
        return Text(src)
    # Drop trailing blank lines Rich adds after the last block — but keep
    # "blank" rows that carry a background (a code block's bottom padding).
    def _blank(line) -> bool:
        return all(
            seg.control or (not seg.text.strip() and not (seg.style and seg.style.bgcolor))
            for seg in line
        )

    while lines and _blank(lines[-1]):
        lines.pop()
    out = Text(no_wrap=True, overflow="crop", end="")
    for i, line in enumerate(lines):
        if i:
            out.append("\n")
        for seg in line:
            if seg.control:
                continue
            out.append(seg.text, seg.style)
    return out


_FENCE = re.compile(r"^\s*(```|~~~)")


def split_stable(source: str, min_chars: int = 1200) -> int:
    """Index up to which ``source`` can be frozen during streaming.

    Returns a cut at the last blank line that is outside a code fence and
    leaves at least a short live tail, or 0 when nothing should be frozen
    yet. Everything before the cut renders identically on its own.
    """
    if len(source) < min_chars:
        return 0
    in_fence = False
    cut = 0
    pos = 0
    lines = source.split("\n")
    for idx, line in enumerate(lines[:-1]):  # never cut inside the last line
        pos += len(line) + 1
        if _FENCE.match(line):
            in_fence = not in_fence
            continue
        if in_fence or line.strip():
            continue
        nxt = lines[idx + 1] if idx + 1 < len(lines) else ""
        # Don't split a list/table across the cut — they'd renumber/restyle.
        if re.match(r"^\s*([-*+]|\d+[.)]|\|)\s", nxt):
            continue
        cut = pos
    return cut if cut >= min_chars // 2 else 0
