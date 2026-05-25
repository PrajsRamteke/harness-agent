"""Web remote UI — bottom URL strip + top-right QR overlay."""
from __future__ import annotations

from rich.box import ROUNDED
from rich.markup import escape as _rich_escape
from rich.panel import Panel
from rich.text import Text
from textual.containers import Horizontal
from textual.widgets import Static

from qrcode.constants import ERROR_CORRECT_L

from ..web.qr_ascii import qr_ascii, qr_dimensions
from . import theme as ui


class WebRemoteQR(Static):
    """Tiny scannable QR — pinned top-right when --web is active.

    Wraps the QR in a Rich ``Panel`` so the frame picks up the active
    theme's accent color, and pins the widget's geometry to the exact
    rendered size (QR + 1-char border) so Textual cannot stretch the
    half-block characters into full-width bars.
    """

    DEFAULT_CSS = f"""
    WebRemoteQR {{
        layer: overlay;
        dock: right;
        offset: 0 0;
        width: auto;
        height: auto;
        padding: 0;
        margin: 0 1 0 0;
        background: {ui.BG_1};
        color: #ffffff;
        overflow: hidden;
        text-wrap: nowrap;
        text-style: none;
    }}
    WebRemoteQR.hidden {{
        display: none;
    }}
    """

    def __init__(self, url: str = "", **kwargs) -> None:
        super().__init__(
            "",
            markup=False,
            shrink=True,
            expand=False,
            **kwargs,
        )
        self._url = url

    def set_url(self, url: str) -> None:
        self._url = (url or "").strip()
        if not self._url:
            self.hide()
            return
        art = qr_ascii(self._url.upper(), ecc=ERROR_CORRECT_L)
        if not art:
            self.hide()
            return
        cols, rows = qr_dimensions(art)
        # Panel adds 1 char of border on every side → total = (cols+2, rows+2).
        panel = Panel(
            Text(art, no_wrap=True, overflow="crop", end=""),
            box=ROUNDED,
            border_style=ui.ACCENT,
            padding=(0, 0),
            expand=False,
        )
        total_w = cols + 2
        total_h = rows + 2
        self.styles.width = total_w
        self.styles.min_width = total_w
        self.styles.max_width = total_w
        self.styles.height = total_h
        self.styles.min_height = total_h
        self.styles.max_height = total_h
        self.remove_class("hidden")
        self.update(panel)

    def hide(self) -> None:
        self._url = ""
        self.add_class("hidden")
        try:
            self.update("")
        except Exception:
            pass

    def on_mount(self) -> None:
        if self._url:
            self.set_url(self._url)
        else:
            self.hide()


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
        try:
            self.query_one("#web_open", Static).update("")
        except Exception:
            pass

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
            f"[{ui.FG_DIM}]· scan QR top-right · click · ⌃⇧U copy[/]"
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
