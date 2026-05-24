"""Bottom strip showing the web remote URL — click to open or copy."""
from __future__ import annotations

from rich.markup import escape as _rich_escape
from rich.text import Text
from textual.containers import Horizontal
from textual.widgets import Static

from . import theme as ui


class WebRemoteBar(Horizontal):
    """Persistent footer row with the remote URL (click link to open)."""

    def __init__(self, url: str = "", **kwargs) -> None:
        super().__init__(**kwargs)
        self._url = url

    def compose(self):
        yield Static("", id="web_open", markup=True)

    def set_url(self, url: str) -> None:
        self._url = url
        self.remove_class("hidden")
        self._refresh()

    def hide_bar(self) -> None:
        self.add_class("hidden")
        self._url = ""

    def _refresh(self) -> None:
        if not self._url:
            self.hide_bar()
            return
        esc = _rich_escape(self._url)
        short = esc
        if len(short) > 72:
            short = short[:69] + "…"
        open_line = (
            f"🌐 [{ui.FG_DIM}]remote[/]  "
            f"[link={esc}]{short}[/link]  "
            f"[{ui.FG_DIM}]· click to open · ⌃⇧U copy[/]"
        )
        try:
            self.query_one("#web_open", Static).update(Text.from_markup(open_line))
        except Exception:
            pass

    def on_mount(self) -> None:
        if self._url:
            self._refresh()
        else:
            self.hide_bar()
