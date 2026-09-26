"""``/web`` dialog — QR code + link to open this session in a browser.

Two modes, switched at the top of the dialog (or with ``l`` / ``a``):

* **Local network** — the LAN link; phone on the same Wi-Fi.
* **Anywhere** — a public HTTPS link through a tunnel (Cloudflare quick
  tunnel or ngrok, ``jarvis/web/tunnel.py``). Its QR carries the token: a
  tunnel visitor is never handed one.

Opened by ``/web`` (starting the remote first if Jarvis was launched without
``--web``), ``/web anywhere``, and "QR + link" in the web strip. Everything is
one click or one key.
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
from ..web.tunnel import INSTALL_HINTS, available_providers
from . import theme as ui
from .footer import FooterBar
from .modal_chrome import TUI_MODAL_CHROME_CSS, TuiModalScreen, hint_line
from .mouse_toggle import disable_mouse, enable_mouse
from .web_bar import qr_target

_PROVIDER_NAMES = {"cloudflare": "Cloudflare", "ngrok": "ngrok"}


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
    WebConnectScreen #web_mode {
        height: 1;
        width: 100%;
        margin: 1 0 0 0;
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
    WebConnectScreen #web_qr.-message {
        background: transparent;
        color: $jv-fg;
        padding: 1 2;
        text-wrap: wrap;
        width: 100%;
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
        height: auto;
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
        Binding("l", "mode('local')", "Local network", show=False),
        Binding("a", "mode('anywhere')", "Anywhere", show=False),
        Binding("c", "pick('copy')", "Copy link", show=False),
        Binding("o", "pick('open')", "Open", show=False),
        Binding("h", "pick('toggle_qr')", "Corner QR", show=False),
        Binding("i", "pick('install')", "Install cloudflared", show=False),
        Binding("s", "pick('stop')", "Stop", show=False),
    ]

    def __init__(
        self,
        url: str,
        other_urls: list[str] | None = None,
        *,
        corner_qr: bool = True,
        clients: Callable[[], int] | None = None,
        mode: str = "local",
    ) -> None:
        super().__init__()
        self._url = url
        self._others = [u for u in (other_urls or []) if u and u != url]
        self._corner_qr = corner_qr
        self._clients = clients or (lambda: 0)
        self._mode = mode if mode in ("local", "anywhere") else "local"
        self._painted: tuple | None = None

    def compose(self) -> ComposeResult:
        with CenterMiddle():
            with Vertical(id="modal"):
                yield Static(f"[{ui.ACCENT}]🌐[/]  Open this session in a browser", id="modal_title")
                yield FooterBar(id="web_mode", classes="-left")
                with Vertical(id="web_qr_wrap"):
                    yield Static("", id="web_qr", markup=False)
                yield Static("", id="web_links")
                yield Static("", id="web_status")
                yield FooterBar(id="web_actions", classes="-left")
                yield Static("", id="modal_hint")

    # ─── state ─────────────────────────────────────────────────────────
    def _tunnel(self):
        return getattr(self.app, "_web_tunnel", None)

    def _public_link(self) -> str:
        return str(getattr(self.app, "_web_public_link", "") or "")

    def _anywhere_state(self) -> str:
        """none | missing | starting | live | error"""
        tunnel = self._tunnel()
        if tunnel is None:
            return "none" if available_providers() else "missing"
        if tunnel.status == "live" and self._public_link():
            return "live"
        return tunnel.status if tunnel.status in ("starting", "error") else "none"

    def _current_link(self) -> str:
        if self._mode == "anywhere":
            return self._public_link()
        return self._url

    # ─── painting ──────────────────────────────────────────────────────
    def _paint(self, *, force: bool = False) -> None:
        tunnel = self._tunnel()
        key = (self._mode, self._anywhere_state(), self._public_link(),
               getattr(tunnel, "error", ""), self._corner_qr)
        if key == self._painted and not force:
            return
        self._painted = key
        self._paint_mode()
        if self._mode == "local":
            self._paint_local()
        else:
            self._paint_anywhere()
        self._paint_actions()
        self._paint_hint()

    def _paint_mode(self) -> None:
        def seg(label: str, on: bool, action: str) -> tuple[str, str]:
            dot = f"[bold {ui.ACCENT}]●[/]" if on else f"[{ui.FG_DIM}]○[/]"
            style = f"bold {ui.FG}" if on else ui.FG_MUTE
            return (f"{dot} [{style}]{label}[/]", action)

        self.query_one("#web_mode", FooterBar).set_segments([
            seg("Local network", self._mode == "local", "screen.mode('local')"),
            seg("Anywhere", self._mode == "anywhere", "screen.mode('anywhere')"),
        ])

    def _show_qr(self, link: str, *, with_token: bool) -> None:
        qr = self.query_one("#web_qr", Static)
        qr.remove_class("-message")
        art = qr_ascii(link if with_token else qr_target(link))
        qr.update(Text(art or "(QR unavailable — install the qrcode package)",
                       no_wrap=True, overflow="crop", end=""))

    def _show_message(self, markup: str) -> None:
        qr = self.query_one("#web_qr", Static)
        qr.add_class("-message")
        qr.update(Text.from_markup(markup))

    def _paint_local(self) -> None:
        self._show_qr(self._url, with_token=False)
        lines = [
            Text.from_markup(f"[{ui.FG_MUTE}]Scan with your phone camera (same Wi-Fi), or open:[/]"),
            Text.from_markup(f"[bold {ui.ACCENT_2}]{_rich_escape(self._url)}[/]"),
        ]
        for other in self._others[:2]:
            lines.append(Text.from_markup(f"[{ui.FG_DIM}]{_rich_escape(other)}[/]"))
        self.query_one("#web_links", Static).update(Group(*lines))
        self._paint_status()

    def _paint_anywhere(self) -> None:
        st = self._anywhere_state()
        links = self.query_one("#web_links", Static)
        tunnel = self._tunnel()
        name = _PROVIDER_NAMES.get(getattr(tunnel, "provider", ""), "tunnel")
        if st == "live":
            link = self._public_link()
            self._show_qr(link, with_token=True)
            lines = [
                Text.from_markup(f"[{ui.FG_MUTE}]Scan from any network (mobile data works), or open:[/]"),
                Text.from_markup(f"[bold {ui.ACCENT_2}]{_rich_escape(link)}[/]"),
                Text.from_markup(
                    f"[{ui.WARN}]⚠ Anyone with this link can control Jarvis on this computer — "
                    f"don't share it.[/] [{ui.FG_DIM}]via {name}[/]"
                ),
            ]
            if getattr(tunnel, "provider", "") == "ngrok":
                lines.append(Text.from_markup(
                    f"[{ui.FG_DIM}]ngrok's free plan shows a one-time \"Visit site\" page — tap it once.[/]"
                ))
            links.update(Group(*lines))
        elif st == "starting":
            self._show_message(
                f"[{ui.ACCENT}]◌[/] [bold]Opening a public link with {name}…[/]\n"
                f"[{ui.FG_DIM}]Usually a few seconds. The QR appears here when it's ready.[/]"
            )
            links.update("")
        elif st == "error":
            err = _rich_escape(getattr(tunnel, "error", "") or "the tunnel stopped")
            self._show_message(
                f"[{ui.ERR}]✕[/] [bold]The Anywhere link didn't start[/]\n"
                f"[{ui.FG_MUTE}]{err}[/]\n\n[{ui.FG_DIM}]Press a to try again, or l for the local network.[/]"
            )
            links.update("")
        elif st == "missing":
            self._show_message(
                f"[bold]Anywhere needs a tunnel app[/] [{ui.FG_DIM}](one time)[/]\n\n"
                f"[{ui.FG}]Cloudflare — free, no account:[/]  [bold {ui.ACCENT_2}]{INSTALL_HINTS['cloudflare']}[/]\n"
                f"[{ui.FG_DIM}]or ngrok (free account):  {INSTALL_HINTS['ngrok']}[/]\n\n"
                f"[{ui.FG_MUTE}]Press[/] [bold]i[/] [{ui.FG_MUTE}]to install cloudflared now, then[/] [bold]a[/]."
            )
            links.update("")
        else:  # none: installed but not started (tunnel was closed)
            self._show_message(
                f"[bold]Use Jarvis from any network[/]\n"
                f"[{ui.FG_DIM}]Opens a private HTTPS link through a tunnel. Press[/] [bold]a[/] "
                f"[{ui.FG_DIM}]to start it.[/]"
            )
            links.update("")
        self._paint_status()

    def _paint_actions(self) -> None:
        segs: list[tuple[str, str]] = []
        if self._current_link():
            segs.append((f"[bold {ui.ACCENT}]copy link[/]", "screen.pick('copy')"))
            segs.append((f"[{ui.FG}]open in browser[/]", "screen.pick('open')"))
        if self._mode == "local":
            corner = "unpin corner QR" if self._corner_qr else "pin QR to corner"
            segs.append((f"[{ui.FG}]{corner}[/]", "screen.pick('toggle_qr')"))
        elif self._anywhere_state() == "missing":
            segs.append((f"[bold {ui.ACCENT}]install cloudflared[/]", "screen.pick('install')"))
        elif self._tunnel() is not None:
            segs.append((f"[{ui.FG}]turn off Anywhere[/]", "screen.mode('local_off')"))
        segs.append((f"[{ui.ERR}]stop web remote[/]", "screen.pick('stop')"))
        self.query_one("#web_actions", FooterBar).set_segments(segs)

    def _paint_hint(self) -> None:
        pairs = [("l", "local"), ("a", "anywhere")]
        if self._current_link():
            pairs += [("c", "copy"), ("o", "open")]
        if self._mode == "local":
            pairs.append(("h", "corner QR"))
        elif self._anywhere_state() == "missing":
            pairs.append(("i", "install"))
        pairs += [("s", "stop"), ("esc", "close")]
        self.query_one("#modal_hint", Static).update(hint_line(*pairs))

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

    # ─── lifecycle ─────────────────────────────────────────────────────
    def on_mount(self) -> None:
        enable_mouse()
        self._paint(force=True)
        # Repaint when the tunnel goes live / fails, and keep the count fresh.
        self.set_interval(0.5, self._tick)

    def _tick(self) -> None:
        self._paint()
        self._paint_status()

    def on_unmount(self) -> None:
        disable_mouse()

    # ─── actions ───────────────────────────────────────────────────────
    def action_mode(self, mode: str) -> None:
        app = self.app
        if mode == "local_off":
            app._stop_tunnel()
            mode = "local"
        elif mode == "anywhere" and self._anywhere_state() in ("none", "error"):
            if self._anywhere_state() == "error":
                app._stop_tunnel(quiet=True)
            app._start_tunnel()
        self._mode = mode
        self._paint(force=True)

    def action_pick(self, what: str) -> None:
        """Copy / open / corner-QR / install act in place; only "stop" closes."""
        app = self.app
        link = self._current_link()
        if what == "copy" and link:
            ok = bool(app._copy_to_system_clipboard(link))
            self.notify("Link copied" if ok else "Copy failed — select the link instead",
                        severity="information" if ok else "warning", timeout=2.5)
        elif what == "open" and link:
            import threading
            import webbrowser

            threading.Thread(target=webbrowser.open, args=(link,), daemon=True).start()
        elif what == "toggle_qr" and self._mode == "local":
            app.action_toggle_web_qr()
            self._corner_qr = bool(app._web_qr_wanted())
            self._paint(force=True)
        elif what == "install" and self._anywhere_state() == "missing":
            app._install_cloudflared()
        elif what == "stop":
            self.dismiss("stop")

    def action_close(self) -> None:
        self.dismiss(None)
