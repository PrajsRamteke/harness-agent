"""``/web`` dialog — QR code + link to open this session in a browser.

Opened by ``/web`` / ``/web qr`` (starting the web remote first if Jarvis
was launched without ``--web``) and by "QR + link" in the web strip.
Everything is one click or one key: copy, open, pin/unpin the corner QR,
stop the remote.
"""
from __future__ import annotations

from collections.abc import Callable

from rich.console import Group
from rich.markup import escape as _rich_escape
from rich.text import Text
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import CenterMiddle, Vertical
from textual.widgets import Static

from ..web.qr_ascii import qr_ascii
from . import theme as ui
from .footer import FooterBar
from .modal_chrome import TUI_MODAL_CHROME_CSS, TuiModalScreen, hint_line
from .mouse_toggle import disable_mouse, enable_mouse
from .web_bar import qr_target


class WebConnectScreen(TuiModalScreen[str | None]):
    """Dismisses with "stop" when the user stops the remote, else None."""

    DEFAULT_CSS = (
        TUI_MODAL_CHROME_CSS
        + """
    WebConnectScreen #modal {
        width: 84;
        max-width: 96%;
        height: auto;
        max-height: 95%;
    }
    WebConnectScreen #web_qr {
        width: auto;
        height: auto;
        padding: 0 1;
        margin: 1 0 0 0;
        background: #000000;
        color: #ffffff;
        text-wrap: nowrap;
    }
    WebConnectScreen #web_qr_wrap {
        width: 100%;
        height: auto;
        align-horizontal: center;
    }
    WebConnectScreen #web_links {
        height: auto;
        margin: 1 0 0 0;
    }
    WebConnectScreen #web_status {
        height: 1;
        margin: 1 0 0 0;
    }
    WebConnectScreen #web_actions {
        height: 1;
        width: 100%;
        margin: 1 0 0 0;
    }
    """
    )

    BINDINGS = [
        Binding("escape", "close", "Close", show=False),
        Binding("c", "pick('copy')", "Copy link", show=False),
        Binding("o", "pick('open')", "Open", show=False),
        Binding("h", "pick('toggle_qr')", "Corner QR", show=False),
        Binding("s", "pick('stop')", "Stop", show=False),
    ]

    def __init__(
        self,
        url: str,
        other_urls: list[str] | None = None,
        *,
        corner_qr: bool = True,
        clients: Callable[[], int] | None = None,
    ) -> None:
        super().__init__()
        self._url = url
        self._others = [u for u in (other_urls or []) if u and u != url]
        self._corner_qr = corner_qr
        self._clients = clients or (lambda: 0)

    def compose(self) -> ComposeResult:
        with CenterMiddle():
            with Vertical(id="modal"):
                yield Static(f"[{ui.ACCENT}]🌐[/]  Open this session in a browser", id="modal_title")
                with Vertical(id="web_qr_wrap"):
                    yield Static(self._qr_text(), id="web_qr", markup=False)
                yield Static(self._links(), id="web_links")
                yield Static("", id="web_status")
                yield FooterBar(id="web_actions", classes="-left")
                yield Static(
                    hint_line(("c", "copy"), ("o", "open"), ("h", "corner QR"), ("s", "stop"), ("esc", "close")),
                    id="modal_hint",
                )

    def _qr_text(self) -> Text:
        art = qr_ascii(qr_target(self._url)) or "(QR unavailable — install the qrcode package)"
        return Text(art, no_wrap=True, overflow="crop", end="")

    def _links(self) -> Group:
        lines = [
            Text.from_markup(
                f"[{ui.FG_MUTE}]Scan with your phone camera (same Wi-Fi), or open:[/]"
            ),
            Text.from_markup(f"[bold {ui.ACCENT_2}]{_rich_escape(self._url)}[/]"),
        ]
        for other in self._others[:2]:
            lines.append(Text.from_markup(f"[{ui.FG_DIM}]{_rich_escape(other)}[/]"))
        return Group(*lines)

    def _paint_actions(self) -> None:
        corner = "unpin corner QR" if self._corner_qr else "pin QR to corner"
        self.query_one("#web_actions", FooterBar).set_segments([
            (f"[bold {ui.ACCENT}]copy link[/]", "screen.pick('copy')"),
            (f"[{ui.FG}]open in browser[/]", "screen.pick('open')"),
            (f"[{ui.FG}]{corner}[/]", "screen.pick('toggle_qr')"),
            (f"[{ui.ERR}]stop web remote[/]", "screen.pick('stop')"),
        ])

    def _paint_status(self) -> None:
        try:
            n = int(self._clients())
        except Exception:
            n = 0
        if n:
            label = "browser" if n == 1 else "browsers"
            text = f"[{ui.OK}]●[/] [{ui.FG_MUTE}]{n} {label} connected — they mirror this terminal live[/]"
        else:
            text = f"[{ui.FG_DIM}]○ no browser connected yet[/]"
        self.query_one("#web_status", Static).update(Text.from_markup(text))

    def on_mount(self) -> None:
        enable_mouse()
        self._paint_actions()
        self._paint_status()
        self.set_interval(1.0, self._paint_status)

    def on_unmount(self) -> None:
        disable_mouse()

    def action_pick(self, what: str) -> None:
        """Copy / open / corner-QR act in place; only "stop" closes the dialog."""
        app = self.app
        if what == "copy":
            ok = bool(getattr(app, "_copy_web_url", lambda **_: False)(show_status=False))
            self.notify("Link copied" if ok else "Copy failed — select the link instead",
                        severity="information" if ok else "warning", timeout=2.5)
        elif what == "open":
            app.action_open_web_url()
        elif what == "toggle_qr":
            app.action_toggle_web_qr()
            self._corner_qr = bool(app._web_qr_wanted())
            self._paint_actions()
        elif what == "stop":
            self.dismiss("stop")

    def action_close(self) -> None:
        self.dismiss(None)
