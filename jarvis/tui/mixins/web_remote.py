"""Web-remote bridge wiring for the Jarvis TUI.

Owns everything behind ``jarvis --web``: starting the bridge/mux/server,
the corner QR and the footer's remote item, and routing submits/cancels/settings/actions that
arrive from the browser back into the normal turn loop.

Mixed into ``JarvisTUI``; all ``self.*`` references resolve on the composed
app instance.
"""
from __future__ import annotations

import threading
from contextlib import nullcontext

from rich.markup import escape as _rich_escape

from ..console_shim import TUIConsole
from ..console_swap import _swap_console_everywhere
from ..web_bar import WebRemoteQR
from ..app_commands import (
    _is_bare_model_command,
    _is_provider_hub_command,
    _is_session_picker_command,
    _is_think_picker_command,
    _is_mcp_modal_command,
    _is_agent_picker_command,
    _is_skill_picker_command,
    _is_memory_modal_command,
    _is_pin_modal_command,
    _is_lesson_modal_command,
    _is_settings_modal_command,
    _is_theme_modal_command,
)
from .. import theme as ui
from ... import state


class WebRemoteMixin:
    """Web-remote (browser bridge) behaviour for ``JarvisTUI``."""

    def _start_web_remote(self, tui_console: TUIConsole) -> bool:
        from ...web.bridge import WebBridge
        from ...web.console_mux import WebMuxConsole
        from ...web.server import primary_remote_url, start_web_server

        bridge = WebBridge()
        bridge.set_handlers(
            on_submit=lambda text, files=None: self.call_from_thread(
                lambda: self._handle_web_submit(text, files)
            ),
            on_cancel=lambda: self.call_from_thread(self._handle_web_cancel),
            on_settings=lambda data, done: self.call_from_thread(
                lambda: self._complete_web_settings(data, done)
            ),
            on_action=lambda action, data, done: self.call_from_thread(
                lambda a=action, d=data, cb=done: self._complete_web_action(a, d, cb)
            ),
        )
        mux = WebMuxConsole(tui_console, bridge)
        _swap_console_everywhere(mux)
        self._tui_console = mux
        self._web_mux = mux
        self._web_bridge = bridge
        preferred_port = state.web_port
        try:
            self._web_server, self._web_urls, bound_port = start_web_server(
                bridge=bridge,
                app=self,
                port=preferred_port,
            )
        except OSError as exc:
            self._tui_console = tui_console
            self._web_mux = None
            self._web_bridge = None
            _swap_console_everywhere(tui_console)
            self._tui_console.print(
                f"[{ui.WARN}]Web remote failed: {exc}[/]"
            )
            self._tui_console.print(
                f"[{ui.FG_DIM}]Stop the other jarvis --web session or set "
                f"HARNESS_WEB_PORT to a free port.[/]"
            )
            return False

        state.web_enabled = True
        state.web_port = bound_port
        if bound_port != preferred_port:
            self._tui_console.print(
                f"[{ui.WARN}]Port {preferred_port} in use — "
                f"web remote on [cyan]{bound_port}[/]"
            )
        self._web_primary_url = primary_remote_url(self._web_urls)
        self._render_web_bar()
        return True

    def _stop_web_remote(self) -> None:
        """``/web stop``: close browsers' streams, free the port, unwrap the console."""
        from ...web.server import stop_web_server

        self._stop_tunnel(quiet=True)
        mux = getattr(self, "_web_mux", None)
        server, bridge = getattr(self, "_web_server", None), self._web_bridge
        if mux is not None:
            primary = mux._primary
            _swap_console_everywhere(primary)
            self._tui_console = primary
        self._web_mux = None
        self._web_bridge = None
        self._web_server = None
        self._web_urls = []
        self._web_primary_url = ""
        state.web_enabled = False
        self._render_web_bar()
        # server.shutdown() waits for the accept loop — keep it off the UI thread.
        threading.Thread(
            target=stop_web_server, args=(server, bridge), daemon=True, name="jarvis-web-stop"
        ).start()
        self._tui_console.print(f"[{ui.FG_DIM}]🌐 web remote stopped · /web to start it again[/]")

    # ─── Anywhere (public tunnel) ─────────────────────────────────────
    def _tunnel_preference(self) -> str:
        try:
            from ...storage.settings import get_settings

            return str(get_settings().get("web.tunnel") or "auto")
        except Exception:
            return "auto"

    def _keep_awake_pref(self) -> bool:
        try:
            from ...storage.settings import get_settings

            return bool(get_settings().get("web.keep_awake", True))
        except Exception:
            return True

    def _on_ui_thread(self, fn, *args) -> None:
        """Run ``fn`` on the UI thread from anywhere (tunnel threads call this)."""
        if threading.get_ident() == getattr(self, "_thread_id", None):
            fn(*args)
        else:
            try:
                self.call_from_thread(fn, *args)
            except Exception:
                pass

    def _start_tunnel(self) -> str:
        """Open the public link. Returns "starting", "live", "running" or "missing"."""
        from ...web.tunnel import Tunnel, pick_provider

        if self._web_bridge is None and not self._start_web_remote(self._tui_console):
            return "error"
        current = getattr(self, "_web_tunnel", None)
        if current is not None and current.status in ("starting", "live"):
            return "running"
        provider = pick_provider(self._tunnel_preference())
        if provider is None:
            return "missing"
        tunnel = Tunnel(
            provider=provider,
            port=int(state.web_port),
            keep_awake=self._keep_awake_pref(),
            on_change=lambda t: self._on_ui_thread(self._on_tunnel_change, t),
        )
        self._web_tunnel = tunnel
        self._web_public_link = ""
        tunnel.start()
        return "starting"

    def _on_tunnel_change(self, tunnel) -> None:
        from urllib.parse import urlparse

        from ...web.tunnel import public_link

        if tunnel is not getattr(self, "_web_tunnel", None):
            return  # an older tunnel we already replaced / stopped
        bridge = self._web_bridge
        if tunnel.status == "live" and bridge is not None:
            bridge.public_host = urlparse(tunnel.url).hostname or ""
            self._web_public_link = public_link(tunnel.url, bridge.token)
            esc = _rich_escape(self._web_public_link)
            self._tui_console.print(
                f"[{ui.OK}]🌍 anywhere[/]  [link={esc}]{esc}[/link]  "
                f"[{ui.FG_DIM}]· works from any network"
                f"{' · Mac kept awake (display may sleep)' if tunnel.awake else ''}"
                f" · /web local turns it off[/]"
            )
            self.notify("Anywhere link is live — scan it from /web", timeout=3)
        elif tunnel.status == "error":
            self._web_public_link = ""
            if bridge is not None:
                bridge.public_host = ""
            self._tui_console.print(f"[{ui.WARN}]🌍 anywhere link failed: {_rich_escape(tunnel.error)}[/]")
        # An open /web dialog shows the QR the moment the link is ready.
        from ..web_modal import WebConnectScreen

        if isinstance(self.screen, WebConnectScreen):
            self.screen._paint()
        self._render_footer()

    def _stop_tunnel(self, *, quiet: bool = False) -> None:
        tunnel = getattr(self, "_web_tunnel", None)
        self._web_tunnel = None
        self._web_public_link = ""
        if self._web_bridge is not None:
            self._web_bridge.public_host = ""
        self._render_footer()
        if tunnel is None:
            return
        threading.Thread(target=tunnel.stop, daemon=True, name="tunnel-stop").start()
        if not quiet:
            self._tui_console.print(f"[{ui.FG_DIM}]🌍 anywhere link closed · local network only[/]")

    def _install_cloudflared(self) -> None:
        """One-key install from the /web dialog (Homebrew)."""
        import shutil
        import subprocess

        brew = shutil.which("brew")
        if not brew:
            self.notify("Install cloudflared from developers.cloudflare.com (Homebrew not found)",
                        severity="warning", timeout=5)
            return
        self.notify("Installing cloudflared with Homebrew…", timeout=4)

        def run() -> None:
            try:
                r = subprocess.run([brew, "install", "cloudflared"], capture_output=True, text=True, timeout=600)
                ok = r.returncode == 0
                msg = "cloudflared installed — press a to go Anywhere" if ok else (
                    "brew install cloudflared failed: " + (r.stderr or r.stdout).strip().splitlines()[-1][:120]
                )
            except Exception as exc:
                ok, msg = False, f"brew install cloudflared failed: {exc}"
            self._on_ui_thread(
                lambda: self.notify(msg, severity="information" if ok else "error", timeout=5)
            )

        threading.Thread(target=run, daemon=True, name="install-cloudflared").start()

    # ─── QR + /web ────────────────────────────────────────────────────
    def _web_qr_wanted(self) -> bool:
        """Corner QR preference (settings ``web.qr``, default on)."""
        try:
            from ...storage.settings import get_settings

            return get_settings().get("web.qr") is not False
        except Exception:
            return True

    def _set_web_qr_wanted(self, shown: bool) -> None:
        try:
            from ...storage.settings import get_settings

            get_settings().set("web.qr", bool(shown))
        except Exception:
            pass
        self._render_web_bar()

    def action_toggle_web_qr(self) -> None:
        shown = not self._web_qr_wanted()
        self._set_web_qr_wanted(shown)
        self.notify(
            "QR pinned to the corner" if shown else "QR hidden — /web qr shows it any time",
            timeout=2.5,
        )

    def action_hide_web_qr(self) -> None:
        self._set_web_qr_wanted(False)
        self.notify("QR hidden — /web qr shows it any time", timeout=2.5)

    def action_web_connect(self) -> None:
        self._open_web_modal()

    def _open_web_modal(self, mode: str | None = None) -> None:
        """Start the remote if needed, then show the QR + link dialog."""
        started_now = False
        if self._web_bridge is None:
            if not self._start_web_remote(self._tui_console):
                return
            started_now = True
            esc = _rich_escape(self._web_primary_url)
            self._tui_console.print(
                f"[{ui.FG_DIM}]🌐 web remote on[/]  [link={esc}]{esc}[/link]"
            )
        from ..web_modal import WebConnectScreen

        bridge = self._web_bridge

        def after(result: str | None) -> None:
            if result == "stop":
                self._stop_web_remote()

        self.push_screen(
            WebConnectScreen(
                self._web_primary_url,
                list(self._web_urls or []),
                corner_qr=self._web_qr_wanted(),
                clients=lambda: bridge.subscriber_count() if bridge else 0,
                mode=mode or ("anywhere" if getattr(self, "_web_tunnel", None) else "local"),
            ),
            after,
        )
        if started_now:
            self._set_status("web remote on")

    def _web_local_url(self) -> str:
        """The loopback link (token included) — the one for this computer's browser."""
        for url in self._web_urls or []:
            if "127.0.0.1" in url or "localhost" in url:
                return url
        return self._web_primary_url

    def action_open_web_browser(self) -> None:
        """Composer 🌐 button: start the remote if needed, open it in the browser here."""
        started_now = False
        if self._web_bridge is None:
            if not self._start_web_remote(self._tui_console):
                return
            started_now = True
        url = self._web_local_url()
        if not url:
            return
        if started_now:
            esc = _rich_escape(url)
            self._tui_console.print(
                f"[{ui.FG_DIM}]🌐 web remote on[/]  [link={esc}]{esc}[/link]  "
                f"[{ui.FG_DIM}]· opened in your browser[/]"
            )

        def failed() -> None:
            copied = self._copy_web_url(show_status=False)
            self.notify(
                "Couldn't open a browser — link copied, paste it into one"
                if copied else "Couldn't open a browser — /web shows the link",
                severity="warning", timeout=5,
            )

        def open_it() -> None:
            import webbrowser

            try:
                ok = bool(webbrowser.open(url))
            except Exception:
                ok = False
            if not ok:
                self._on_ui_thread(failed)

        # webbrowser.open can block for a moment (it runs `open` / xdg-open).
        threading.Thread(target=open_it, daemon=True, name="jarvis-web-open").start()
        if not started_now:  # the first time, the transcript notice says it
            self._set_status("🌐 opening the web remote in your browser")

    def _handle_web_command(self, text: str) -> None:
        """``/web`` · ``/web qr`` · ``/web open`` · ``/web show|hide`` · ``/web copy`` · ``/web stop``."""
        parts = (text or "").strip().split()
        sub = parts[1].lower() if len(parts) > 1 else ""
        running = self._web_bridge is not None
        if sub in ("open", "browser"):
            self.action_open_web_browser()
        elif sub in ("", "qr", "start", "on", "link", "url"):
            self._open_web_modal()
        elif sub in ("anywhere", "tunnel", "public", "remote", "internet"):
            if self._start_tunnel() == "error":
                return
            self._open_web_modal("anywhere")
        elif sub in ("local", "lan", "private"):
            if getattr(self, "_web_tunnel", None) is not None:
                self._stop_tunnel()
            self._open_web_modal("local")
        elif sub in ("hide", "off-qr", "noqr"):
            self._set_web_qr_wanted(False)
            self.notify("Corner QR hidden — /web qr shows it any time", timeout=2.5)
        elif sub in ("show", "pin"):
            self._set_web_qr_wanted(True)
            if running:
                self.notify("QR pinned to the corner", timeout=2.5)
            else:
                self._open_web_modal()
        elif sub == "copy":
            if not running and not self._start_web_remote(self._tui_console):
                return
            ok = self._copy_web_url(show_status=False)
            self.notify("Web link copied" if ok else "Copy failed", timeout=2.5)
        elif sub in ("stop", "off"):
            if running:
                self._stop_web_remote()
            else:
                self.notify("The web remote isn't running", timeout=2.5)
        else:
            self._tui_console.print(
                f"[{ui.FG_DIM}]/web · /web qr — QR + link (starts the remote)  ·  "
                f"/web open — open it in this computer's browser  ·  /web anywhere — public link for any network · /web local  ·  "
                f"/web hide · /web show — corner QR  ·  /web copy  ·  /web stop[/]"
            )

    def _web_footer_markup(self) -> tuple[str, str] | None:
        """Footer item while the remote runs: ``(full, short)`` markup.

        Full: where it listens (host:port — the token stays out of the
        always-on footer) + who's on it; short: just ``🌐 web``. Click opens
        the QR + link dialog, ⌃⇧U copies the full link.
        """
        url = self._web_primary_url
        if not url:
            return None
        from urllib.parse import urlparse

        if getattr(self, "_web_public_link", ""):
            icon, where, short = "🌍", "anywhere", "anywhere"
        else:
            icon, where, short = "🌐", urlparse(url).netloc or url, "web"
        bridge = self._web_bridge
        try:
            n = bridge.subscriber_count() if bridge is not None else 0
        except Exception:
            n = 0
        live = f" [{ui.FG_DIM}]·[/] [{ui.OK}]{n} connected[/]" if n else ""
        head = f"[{ui.ACCENT}]{icon}[/] "
        return (
            f"{head}[{ui.FG_MUTE}]{_rich_escape(where)}[/]{live}",
            f"{head}[{ui.FG_MUTE}]{short}[/]{live}",
        )

    def _render_web_bar(self) -> None:
        """Sync the corner QR, the composer's 🌐 button and the footer item with the remote's state."""
        from ..web_button import WebButton

        try:
            self.query_one("#web_button", WebButton).set_live(bool(self._web_primary_url))
        except Exception:
            pass
        try:
            qr = self.query_one("#web_qr_overlay", WebRemoteQR)
        except Exception:
            qr = None
        if qr:
            if self._web_primary_url and self._web_qr_wanted():
                qr.set_url(self._web_primary_url)
            else:
                qr.hide()
        self._render_footer()

    def _copy_web_url(self, *, show_status: bool = True) -> bool:
        # The Anywhere link while it's live — that's the one worth sharing to a phone.
        url = getattr(self, "_web_public_link", "") or self._web_primary_url
        if not url:
            return False
        ok = self._copy_to_system_clipboard(url)
        if show_status:
            self._set_status("web url copied" if ok else "copy failed")
        return ok

    def action_copy_web_url(self) -> None:
        self._copy_web_url(show_status=True)

    def _sync_web_busy(self) -> None:
        bridge = self._web_bridge
        if bridge is None:
            return
        bridge.emit("busy", {"busy": self._busy})

    def _sync_web_queue(self) -> None:
        bridge = self._web_bridge
        if bridge is None:
            return
        from ... import prompt_queue

        bridge.emit("queue", {"items": prompt_queue.labels(), "entries": prompt_queue.public()})

    def _handle_web_submit(self, text: str, files: list[str] | None = None) -> None:
        text = (text or "").strip()
        files = list(files or [])
        if not text and not files:
            return
        # Before the busy check, same as the composer: queued, it would reach
        # the slash dispatcher as "unknown: /loop".
        if not files and self._try_loop_command(text):
            return
        if self._busy:
            self._stash_prompt(text, files=files)
            return
        if files:  # web attachments (the handler refuses them on commands)
            self._begin_turn(text, attachments=files)
            return
        if self._is_web_modal_command(text):
            if self._web_bridge is not None:
                self._web_bridge.emit(
                    "message",
                    {"role": "you", "text": text, "title": "you"},
                )
            self._handle_queued_command(text)
            return
        self._begin_turn(text)

    @staticmethod
    def _is_web_modal_command(text: str) -> bool:
        """Bare slash commands that open TUI modals — route before _begin_turn."""
        s = (text or "").strip()
        if not s.startswith("/"):
            return False
        return (
            _is_bare_model_command(s)
            or _is_provider_hub_command(s)
            or _is_session_picker_command(s)
            or _is_think_picker_command(s)
            or _is_mcp_modal_command(s)
            or _is_agent_picker_command(s)
            or _is_skill_picker_command(s)
            or _is_memory_modal_command(s)
            or _is_pin_modal_command(s)
            or _is_lesson_modal_command(s)
            or _is_settings_modal_command(s)
            or _is_theme_modal_command(s)

            or s.lower() == "/local"
            or s.lower() == "/web"
            or s.lower().startswith("/web ")
            or s.lower() == "/sidebar"
            or s.lower() == "/agent init"
        )

    def _handle_web_cancel(self) -> None:
        self._cancel_turn()

    def _complete_web_settings(self, data: dict, done) -> None:
        try:
            result = self._handle_web_settings(data)
        except Exception as exc:
            result = {"ok": False, "error": str(exc)}
        try:
            done(result)
        except Exception:
            pass

    def _complete_web_action(self, action: str, data: dict, done) -> None:
        try:
            result = self._handle_web_action(action, data)
        except Exception as exc:
            result = {"ok": False, "error": str(exc)}
        if not isinstance(result, dict):
            result = {"ok": False, "error": "invalid action response"}
        try:
            done(result)
        except Exception:
            pass

    def _handle_web_settings(self, data: dict) -> dict:
        from ...web.state_api import apply_settings

        result = apply_settings(data)
        if result:
            parts = []
            if "think_mode" in result:
                parts.append(f"think {'on' if result['think_mode'] else 'off'}")
            if "show_internal" in result:
                parts.append(f"trace {'on' if result['show_internal'] else 'off'}")
            if "auto_approve" in result:
                parts.append(f"auto-approve {'on' if result['auto_approve'] else 'off'}")
            if parts:
                self._tui_console.print(f"[{ui.FG_DIM}]web: {', '.join(parts)}[/]")
        return result

    def _handle_web_action(self, action: str, data: dict) -> dict:
        from ...web.actions_api import run_web_action

        if action == "queue":  # edit / remove / send now a queued message
            return self._handle_web_queue(data)

        mux = getattr(self, "_web_mux", None)
        ctx = mux.suppress_broadcast() if mux is not None else nullcontext()
        # New chat / another session while a turn runs: stop the turn first,
        # outside the suppress block so pages get its stream_end + busy=false.
        # (Left running, it kept "thinking" into the new chat, wrote its reply
        # there and made every new prompt queue behind it.)
        stopped = ""
        if action in ("session_resume", "session_new"):
            stopped = self._stop_turn_for_session_change()
        with ctx:
            result = run_web_action(action, data, console_print=self._tui_console.print)
            if not result.get("ok"):
                return result

            if action in ("session_resume", "session_new"):
                try:
                    self.query_one("#transcript").clear()
                    self._tui_console.forget_tools()
                except Exception:
                    pass
                if action == "session_new":
                    self._mount_welcome()
                if action == "session_resume":
                    try:
                        self._render_loaded_session()
                    except Exception as exc:
                        result = dict(result)
                        result["render_warning"] = str(exc)
                if stopped:
                    result = dict(result)
                    result["stopped"] = stopped
                    self._tui_console.print(f"[{ui.FG_DIM}]⏹ web · {stopped}[/]")

            provider_change = action.startswith("provider_")
            if action == "model_select" or provider_change:
                self._write_status_line(busy=False)

            if action in ("session_resume", "session_new", "model_select", "agent_select") or provider_change:
                self._set_status("ready")

        # The HTTP handler broadcasts the fresh snapshot once this returns.
        return result
