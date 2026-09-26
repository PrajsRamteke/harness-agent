"""Web remote UI — bottom URL strip + top-right QR overlay."""
from __future__ import annotations

from urllib.parse import urlparse, urlunparse

from rich.box import ROUNDED
from rich.markup import escape as _rich_escape
from rich.panel import Panel
from rich.text import Text
from textual.containers import Horizontal
from textual.widgets import Static

from ..web.qr_ascii import qr_ascii, qr_dimensions
from . import theme as ui
from .footer import FooterBar


def qr_target(url: str) -> str:
    """What the QR encodes: the URL without its query (token).

    Keeps the code small enough to scan off a terminal; the server hands a
    bare ``/`` visit the current token (``handler.do_GET`` redirect).
    """
    parsed = urlparse((url or "").strip())
    return urlunparse(parsed._replace(query=""))


class WebRemoteQR(Static):
    """Tiny scannable QR — pinned top-right when --web is active.

    Click it (or its "✕ hide" label) to hide it; ``/web qr`` brings the QR
    back in a dialog and ``/web show`` re-pins it here.

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
        art = qr_ascii(qr_target(self._url))
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
            subtitle=Text.from_markup(f"[bold {ui.FG}]✕[/] [{ui.FG_MUTE}]hide[/]"),
            subtitle_align="right",
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

    async def on_click(self, event) -> None:
        event.stop()
        await self.app.run_action("hide_web_qr")


class WebRemoteBar(Horizontal):
    """Persistent footer row: the remote URL plus one-click QR controls."""

    def __init__(self, url: str = "", **kwargs) -> None:
        super().__init__(**kwargs)
        self._url = url
        self._qr_shown = True

    def compose(self):
        yield FooterBar(id="web_open", classes="-left")

    def set_url(self, url: str, *, qr_shown: bool | None = None) -> None:
        self._url = url
        if qr_shown is not None:
            self._qr_shown = qr_shown
        self.remove_class("hidden")
        self._refresh()

    def set_qr_shown(self, shown: bool) -> None:
        self._qr_shown = shown
        self._refresh()

    def hide_bar(self) -> None:
        self.add_class("hidden")
        self._url = ""
        try:
            self.query_one("#web_open", FooterBar).set_segments([])
        except Exception:
            pass

    def _refresh(self) -> None:
        if not self._url:
            self.hide_bar()
            return
        esc = _rich_escape(self._url)
        short = esc if len(esc) <= 72 else esc[:69] + "…"
        segments = [
            (f"[{ui.ACCENT}]🌐[/]  [{ui.ACCENT_2}]{short}[/]", "open_web_url"),
            (
                f"[{ui.FG_MUTE}]hide QR[/]" if self._qr_shown else f"[{ui.FG_MUTE}]show QR[/]",
                "toggle_web_qr",
            ),
            (f"[{ui.FG_MUTE}]QR + link[/]", "web_connect"),
            (f"[{ui.FG_DIM}]⌃⇧U copy[/]", "copy_web_url"),
        ]
        try:
            self.query_one("#web_open", FooterBar).set_segments(segments)
        except Exception:
            pass

    def on_mount(self) -> None:
        if self._url:
            self._refresh()
        else:
            self.hide_bar()
