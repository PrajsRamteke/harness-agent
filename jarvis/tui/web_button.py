"""The composer's ``🌐 web`` button (right edge of the input box).

One click starts the web remote if it isn't running and opens this session in
the computer's browser (the loopback link, token included). While the remote
runs the word turns green. The flow lives in
``mixins/web_remote.py:action_open_web_browser``.
"""
from __future__ import annotations

from rich.text import Text
from textual.widget import Widget

from . import theme as ui

_NARROW = 70  # below this terminal width the button is just its glyph


class WebButton(Widget):
    DEFAULT_CSS = """
    WebButton {
        width: auto;
        height: 1;
        margin: 1 0 1 2;
        background: transparent;
    }
    WebButton.hidden {
        display: none;
    }
    """

    can_focus = False

    def __init__(self, *, id: str | None = None, classes: str | None = None) -> None:
        super().__init__(id=id, classes=classes)
        self.live = False
        self._hover = False
        self._sync_tooltip()

    def set_live(self, live: bool) -> None:
        live = bool(live)
        if live == self.live:
            return
        self.live = live
        self._sync_tooltip()
        self.refresh(layout=True)

    def _sync_tooltip(self) -> None:
        self.tooltip = (
            "Web remote is on — open this session in your browser"
            if self.live
            else "Start the web remote and open this session in your browser"
        )

    def _narrow(self) -> bool:
        return (self.app.size.width or 100) < _NARROW

    def _label(self) -> Text:
        out = Text(no_wrap=True)
        hot = self._hover
        out.append("🌐")
        if not self._narrow():
            color = ui.OK if self.live else (ui.ACCENT if hot else ui.FG_DIM)
            out.append(" web", style=f"{color} underline" if hot else color)
        return out

    def get_content_width(self, container, viewport) -> int:
        return self._label().cell_len

    def render(self) -> Text:
        return self._label()

    def on_enter(self) -> None:
        self._hover = True
        self.refresh()

    def on_leave(self) -> None:
        self._hover = False
        self.refresh()

    async def on_click(self, event) -> None:
        event.stop()
        await self.app.run_action("open_web_browser")
