"""MCP sign-in in the terminal UI (mixed into ``JarvisTUI``).

A hosted MCP server that needs a browser sign-in has health status ``auth``.
This mixin keeps a slim bar above the composer (``mcp_auth_bar.McpAuthBar``)
in step with that: ``Authenticate`` opens the browser (the sign-in itself runs
in the MCP layer), the bar then reads "waiting for the browser…" and clears when
the server connects. The sidebar's MCP rows and the ``/mcp`` dialog start the
same flow through ``mcp_authenticate``.

Registry events arrive on whatever thread caused them (a tool call, the MCP
loop, a browser callback), so they only *schedule* a throttled UI refresh.
"""
from __future__ import annotations

import queue
import threading

from rich.markup import escape as _esc
from textual import work
from textual.screen import ModalScreen

from .. import theme as ui


class McpAuthMixin:
    def _mcp_auth_init(self) -> None:
        self._mcp_auth_dismissed: set[str] = set()
        self._mcp_auth_opened: set[str] = set()      # browser was opened for these
        self._mcp_auth_flow: set[str] = set()        # servers whose sign-in we started / saw
        self._mcp_auth_current: str | None = None    # the server the bar is about
        self._mcp_refresh_timer = None
        self._mcp_events: "queue.SimpleQueue[tuple[str, str] | None]" = queue.SimpleQueue()
        self._mcp_subscribers: list = []
        self._mcp_relay_thread: threading.Thread | None = None

    # ── wiring ───────────────────────────────────────────────────────────

    def _mcp_auth_attach(self) -> None:
        from ...mcp.registry import mcp_registry

        mcp_registry.add_listener(self._on_mcp_event)
        self._mcp_relay_thread = threading.Thread(target=self._mcp_relay, daemon=True, name="mcp-ui-relay")
        self._mcp_relay_thread.start()
        self._mcp_auth_sync()

    def _mcp_auth_detach(self) -> None:
        try:
            from ...mcp.registry import mcp_registry

            mcp_registry.remove_listener(self._on_mcp_event)
        except Exception:
            pass
        self._mcp_events.put(None)  # stops the relay

    def mcp_subscribe(self, fn) -> None:
        """``fn()`` runs on the UI thread after MCP connect / sign-in changes (bursts collapse)."""
        if fn not in self._mcp_subscribers:
            self._mcp_subscribers.append(fn)

    def mcp_unsubscribe(self, fn) -> None:
        if fn in self._mcp_subscribers:
            self._mcp_subscribers.remove(fn)

    def _on_mcp_event(self, event: str, name: str) -> None:
        """Registry listener — any thread, so it only queues. The relay thread
        hands batches to the UI; emitters (the MCP loop, a tool call, the
        browser callback) never wait on the UI."""
        self._mcp_events.put((event, name))

    def _mcp_relay(self) -> None:
        while True:
            item = self._mcp_events.get()
            if item is None:
                return
            batch = [item]
            try:
                while True:  # collapse a burst
                    nxt = self._mcp_events.get_nowait()
                    if nxt is None:
                        return
                    batch.append(nxt)
            except queue.Empty:
                pass
            if not getattr(self, "is_running", False):
                continue
            try:
                self.call_from_thread(self._mcp_events_ui, batch)
            except Exception:
                pass

    def _mcp_events_ui(self, batch: list) -> None:
        for event, name in batch:
            self._mcp_event_ui(event, name)
        self._mcp_flush()
        for fn in list(self._mcp_subscribers):
            try:
                fn()
            except Exception:
                pass

    def _mcp_event_ui(self, event: str, name: str) -> None:
        from ...mcp.registry import mcp_registry

        if event in ("auth_required", "auth"):
            self._mcp_auth_dismissed.discard(name)
            self._mcp_auth_flow.add(name)
        elif event == "connected":
            self._mcp_auth_opened.discard(name)
            if name in self._mcp_auth_flow:
                self._mcp_auth_flow.discard(name)
                n = mcp_registry.get_server_health(name).get("tool_count", 0)
                self._tui_console.print(
                    f"[{ui.OK}]✓[/] MCP [bold]{_esc(name)}[/] connected [{ui.FG_DIM}]· {n} tools[/]"
                )
        elif event in ("auth_error", "failed"):
            self._mcp_auth_opened.discard(name)
            if name in self._mcp_auth_flow:
                self._mcp_auth_flow.discard(name)
                err = mcp_registry.get_server_health(name).get("last_connect_error") or "sign-in failed"
                self._tui_console.print(f"[{ui.ERR}]✗[/] MCP [bold]{_esc(name)}[/]: {_esc(str(err))}")
        elif event in ("auth_cancelled", "disconnected"):
            self._mcp_auth_opened.discard(name)
            self._mcp_auth_flow.discard(name)

    def _mcp_flush(self) -> None:
        self._mcp_auth_sync()
        self._refresh_sidebar()

    # ── bar state ────────────────────────────────────────────────────────

    def _mcp_auth_names(self) -> list[str]:
        try:
            from ...mcp.config import get_config
            from ...mcp.registry import mcp_registry

            servers = get_config().list_servers()
            return [
                n for n in sorted(servers)
                if mcp_registry.get_server_health(n, servers[n]).get("status") == "auth"
            ]
        except Exception:
            return []

    def _mcp_auth_sync(self) -> None:
        """Show / update / hide the bar from the registry's current state."""
        from ...mcp.auth import coordinator
        from .. import mcp_auth_bar

        try:
            bar = self.query_one(mcp_auth_bar.McpAuthBar)
        except Exception:
            return
        needing = self._mcp_auth_names()
        names = [n for n in needing if n not in self._mcp_auth_dismissed]
        self._mcp_auth_opened &= set(needing)  # forget browsers whose server moved on
        if not names:
            self._mcp_auth_current = None
            bar.hide()
            return
        current = self._mcp_auth_current if self._mcp_auth_current in names else names[0]
        self._mcp_auth_current = current
        req = coordinator.get(current)
        has_link = req is not None and req.status in ("pending", "working")
        bar.show_state(
            name=current,
            extra=len(names) - 1,
            waiting=current in self._mcp_auth_opened and has_link,
            has_link=has_link,
        )

    # ── the flow ─────────────────────────────────────────────────────────

    def mcp_authenticate(self, name: str) -> None:
        """Sign in to ``name``: sidebar click, bar button, ⌃O. Never blocks the UI."""
        from ...mcp.auth import coordinator

        self._mcp_auth_current = name
        self._mcp_auth_dismissed.discard(name)
        self._mcp_auth_flow.add(name)
        req = coordinator.get(name)
        if req is not None and req.status in ("pending", "working"):
            opened = coordinator.open_browser(name)
            self._mcp_auth_opened.add(name)
            self._mcp_auth_sync()
            if not opened:
                self._mcp_auth_show_link(name, req.url)
            return
        self._set_status(f"signing in to {name}…")
        self._mcp_sign_in_worker(name)

    @work(thread=True, group="mcp-auth")
    def _mcp_sign_in_worker(self, name: str) -> None:
        from ...mcp.auth import coordinator
        from ...mcp.config import get_config
        from ...mcp.registry import mcp_registry

        cfg = get_config().get_server(name)
        if cfg is None:
            res = {"ok": False, "error": f"no MCP server named '{name}'"}
        else:
            res = mcp_registry.authenticate(name, cfg)
            if res.get("ok") and not res.get("connected"):
                res["opened"] = coordinator.open_browser(name)
        try:
            self.call_from_thread(self._mcp_sign_in_done, name, res)
        except Exception:
            pass

    def _mcp_sign_in_done(self, name: str, res: dict) -> None:
        self._set_status("ready")
        if res.get("connected"):
            self._mcp_auth_flow.discard(name)
            self._tui_console.print(f"[{ui.OK}]✓[/] MCP [bold]{_esc(name)}[/] connected")
        elif res.get("ok"):
            self._mcp_auth_opened.add(name)
            if not res.get("opened"):
                self._mcp_auth_show_link(name, res.get("url", ""))
        else:
            self._mcp_auth_flow.discard(name)
            self._tui_console.print(
                f"[{ui.ERR}]✗[/] MCP [bold]{_esc(name)}[/]: {_esc(str(res.get('error', 'sign-in failed')))}"
            )
        self._mcp_auth_sync()
        self._refresh_sidebar()

    def _mcp_auth_show_link(self, name: str, url: str) -> None:
        """No browser could be opened here (SSH, headless): print the link."""
        if url:
            self._tui_console.print(
                f"[{ui.WARN}]◐[/] Open this link to sign in to [bold]{_esc(name)}[/]:\n"
                f"  [{ui.ACCENT}]{_esc(url)}[/]"
            )

    # ── bar actions ──────────────────────────────────────────────────────

    def action_mcp_auth_start(self) -> None:
        if self._mcp_auth_current:
            self.mcp_authenticate(self._mcp_auth_current)

    def action_mcp_auth_copy(self) -> None:
        from ...mcp.auth import coordinator

        req = coordinator.get(self._mcp_auth_current or "")
        if req is None or not req.url:
            return
        self._copy_text(req.url)
        self._set_status("sign-in link copied")

    def action_mcp_auth_paste(self) -> None:
        name = self._mcp_auth_current
        if not name:
            return
        from ..text_input_modal import TextInputScreen

        def after(text: str | None) -> None:
            if not text or not text.strip():
                return
            self._mcp_auth_pasted(name, text)

        self.push_screen(
            TextInputScreen(
                title=f"Finish signing in to {name}",
                body=(
                    "Signed in on another device? Copy the address the browser ended on "
                    "(it starts with http://localhost…) and paste it here."
                ),
                placeholder="http://localhost:33418/callback?code=…",
            ),
            after,
        )

    def _mcp_auth_pasted(self, name: str, text: str) -> None:
        from ...mcp.auth import coordinator

        res = coordinator.submit(name, text)
        if res.get("ok"):
            self._set_status(f"finishing sign-in to {name}…")
        else:
            self._tui_console.print(f"[{ui.ERR}]✗[/] {_esc(str(res.get('error', 'that did not work')))}")

    def action_mcp_auth_cancel(self) -> None:
        name = self._mcp_auth_current
        if not name:
            return
        from ...mcp.auth import coordinator
        from ...mcp.registry import mcp_registry

        coordinator.cancel(name)
        mcp_registry.disconnect(name)
        self._mcp_auth_opened.discard(name)
        self._mcp_auth_dismissed.add(name)
        self._mcp_auth_sync()
        self._refresh_sidebar()

    def action_mcp_auth_dismiss(self) -> None:
        if self._mcp_auth_current:
            self._mcp_auth_dismissed.add(self._mcp_auth_current)
            self._mcp_auth_sync()

    def check_action(self, action: str, parameters: tuple) -> bool | None:
        if action.startswith("mcp_auth_"):
            return bool(self._mcp_auth_current) and not isinstance(self.screen, ModalScreen)
        return super().check_action(action, parameters)  # type: ignore[misc]

    # ── opened from the sidebar ──────────────────────────────────────────

    def mcp_sidebar_click(self, name: str, status: str) -> None:
        if status == "auth":
            self.mcp_authenticate(name)
        else:
            self._open_mcp_modal()

    def open_mcp_add(self) -> None:
        self._open_mcp_modal(add=True)

    def open_skill_add(self) -> None:
        self._open_skill_browser(add=True)

