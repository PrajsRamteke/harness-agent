"""Footer strip with clickable, hover-underlined segments.

Textual's ``@click`` markup recolors links with ``link-color``; this widget
keeps each segment's own colors and maps the mouse x-position to the
segment's action instead.
"""
from __future__ import annotations

from rich.text import Text
from textual.widget import Widget

from . import theme as ui

Segment = tuple[str, "str | None"]  # (Rich markup, action name or None)


def _markup(m: str) -> Text:
    try:
        return Text.from_markup(m)
    except Exception:
        return Text(m)


class FooterBar(Widget):
    DEFAULT_CSS = """
    FooterBar {
        height: 1;
        width: auto;
        overflow: hidden;
    }
    FooterBar.-left {
        width: 1fr;
    }
    """

    can_focus = False

    def __init__(self, *, id: str | None = None, classes: str | None = None) -> None:
        super().__init__(id=id, classes=classes)
        self._segments: list[Segment] = []
        self._spans: list[tuple[int, int, str | None]] = []
        self._hover: int | None = None

    def set_segments(self, segments: list[Segment]) -> None:
        if segments == self._segments:
            return
        self._segments = list(segments)
        self.refresh(layout=True)

    def _build(self) -> Text:
        out = Text(no_wrap=True, overflow="ellipsis")
        self._spans = []
        for i, (markup, action) in enumerate(self._segments):
            if i:
                out.append(" · ", style=ui.FG_DIM)
            piece = _markup(markup)
            if action and i == self._hover:
                piece.stylize("underline")
            start = out.cell_len
            out.append_text(piece)
            self._spans.append((start, out.cell_len, action))
        return out

    def get_content_width(self, container, viewport) -> int:
        return self._build().cell_len

    def render(self) -> Text:
        return self._build()

    def _action_at(self, x: int) -> tuple[int | None, str | None]:
        for i, (start, end, action) in enumerate(self._spans):
            if start <= x < end:
                return i, action
        return None, None

    def on_mouse_move(self, event) -> None:
        idx, action = self._action_at(event.x)
        idx = idx if action else None
        if idx != self._hover:
            self._hover = idx
            self.refresh()

    def on_leave(self) -> None:
        if self._hover is not None:
            self._hover = None
            self.refresh()

    async def on_click(self, event) -> None:
        _idx, action = self._action_at(event.x)
        if action:
            event.stop()
            await self.app.run_action(action)
