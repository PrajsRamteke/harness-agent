"""Web-remote bridge wiring for the Jarvis TUI.

Owns everything behind ``jarvis --web``: starting the bridge/mux/server,
rendering the web bar, and routing submits/cancels/settings/actions that
arrive from the browser back into the normal turn loop.

Mixed into ``JarvisTUI``; all ``self.*`` references resolve on the composed
app instance.
"""
from __future__ import annotations

from contextlib import nullcontext

from textual.widgets import RichLog

from ..console_shim import TUIConsole
from ..console_swap import _swap_console_everywhere
from ..web_bar import WebRemoteBar, WebRemoteQR
from ..app_commands import (
    _is_bare_model_command,
    _is_bare_provider_command,
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
    _is_oauth_modal_command,
    _is_key_command,
)
from .. import theme as ui
from ... import state


class WebRemoteMixin:
    """Web-remote (browser bridge) behaviour for ``JarvisTUI``."""

    def _start_web_remote(self, tui_console: TUIConsole) -> None:
        from ...web.bridge import WebBridge
        from ...web.console_mux import WebMuxConsole
        from ...web.server import primary_remote_url, start_web_server

        bridge = WebBridge()
        bridge.set_handlers(
            on_submit=lambda text: self.call_from_thread(lambda: self._handle_web_submit(text)),
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
            return

        state.web_port = bound_port
        if bound_port != preferred_port:
            self._tui_console.print(
                f"[{ui.WARN}]Port {preferred_port} in use — "
                f"web remote on [cyan]{bound_port}[/]"
            )
        self._web_primary_url = primary_remote_url(self._web_urls)
        self._render_web_bar()

    def _render_web_bar(self) -> None:
        try:
            bar = self.query_one("#webar", WebRemoteBar)
        except Exception:
            bar = None
        try:
            qr = self.query_one("#web_qr_overlay", WebRemoteQR)
        except Exception:
            qr = None
        if self._web_primary_url:
            bar and bar.set_url(self._web_primary_url)
            qr and qr.set_url(self._web_primary_url)
        else:
            bar and bar.hide_bar()
            qr and qr.hide()

    def _copy_web_url(self, *, show_status: bool = True) -> bool:
        url = self._web_primary_url
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
        items: list[str] = []
        for msg in state.prompt_queue:
            if isinstance(msg, tuple):
                items.append(str(msg[0]).strip())
            else:
                items.append(str(msg).strip())
        bridge.emit("queue", {"items": [i for i in items if i]})

    def _handle_web_submit(self, text: str) -> None:
        text = (text or "").strip()
        if not text:
            return
        if self._busy:
            self._stash_prompt(text)
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
            or _is_bare_provider_command(s)
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
            or _is_oauth_modal_command(s)
            or _is_key_command(s)
            or s.lower() == "/local"
            or s.lower() == "/agent init"
        )

    def _handle_web_cancel(self) -> None:
        if not self._busy:
            return
        from ...repl.stream import cancel_current_stream

        cancel_current_stream()
        if hasattr(self._tui_console, "cancel_pending_prompts"):
            self._tui_console.cancel_pending_prompts()
        self._sync_activity_phase("Cancelling…")
        self._tui_console.print(f"[{ui.WARN}]⏹ cancelled by user (web)[/]")
        self._turn_done()

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

        mux = getattr(self, "_web_mux", None)
        ctx = mux.suppress_broadcast() if mux is not None else nullcontext()
        with ctx:
            result = run_web_action(action, data, console_print=self._tui_console.print)
            if not result.get("ok"):
                return result

            if action in ("session_resume", "session_new"):
                try:
                    log = self.query_one("#transcript", RichLog)
                    log.clear()
                except Exception:
                    pass
                if action == "session_resume":
                    try:
                        self._render_loaded_session()
                    except Exception as exc:
                        result = dict(result)
                        result["render_warning"] = str(exc)

            if action == "model_select":
                self._write_status_line(busy=False)

            if action in ("session_resume", "session_new", "model_select", "agent_select"):
                self._set_status("ready")

        if self._web_bridge is not None and result.get("ok"):
            try:
                from ...web.state_api import snapshot_from_state

                snap = snapshot_from_state(busy=self._busy())
                self._web_bridge.emit("snapshot", snap)
            except Exception:
                pass

        return result
