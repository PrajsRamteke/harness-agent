"""Centered MCP control modal — one place for every MCP action.

User interaction:

* ↑/↓        navigate the server list
* Space/Enter  toggle the highlighted server (connect ↔ disconnect)
* g           toggle global scope (project-only ↔ project + global)
* i           import the highlighted global server into this project's .mcp.json
* e           export the highlighted project server to your global MCP config
* a           add a server: paste a link / npx … / claude mcp add … / JSON / GitHub link / name
* Enter       connect ↔ disconnect — or sign in when the server says "sign-in"
* o           sign out (forget the saved login)      k  enter the keys a server needs
* m           move between project ↔ global
* r           re-scan all config files
* d           delete a server you added (project or global; press twice)
* /           focus the filter input
* Esc         close

Designed to make ``/mcp`` the only MCP command a user ever needs.
"""

from __future__ import annotations

import threading

from textual import work
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import CenterMiddle, Horizontal, Vertical
from textual.timer import Timer
from textual.widgets import Button, Input, OptionList, Static
from textual.widgets.option_list import Option

from rich.text import Text

from .modal_chrome import TUI_MODAL_CHROME_CSS, TuiModalScreen, picker_row
from .mouse_toggle import enable_mouse, disable_mouse
from . import theme as ui
from .. import state
from ..mcp.config import (
    MCP_PROJECT_CONFIG_FILENAME,
    get_config,
    import_server_to_project,
    export_server_to_global,
    reload_config,
    _project_config_path,
)
from ..mcp.registry import mcp_registry, needs_auth
from ..mcp.sources import SOURCE_ICONS as _SOURCE_ICONS, format_endpoint as _endpoint_text


# ── helpers ──────────────────────────────────────────────────────────────


def _row_label(
    name: str,
    cfg: dict,
    scope: str,
    source: str,
    is_auto: bool,
    *,
    connecting: bool = False,
    spinner: str = "⠋",
    health: dict | None = None,
):
    health = health or mcp_registry.get_server_health(name, cfg, connecting=connecting)
    status = health.get("status", "idle")
    tool_count = health.get("tool_count", 0)

    if connecting or status == "connecting":
        dot, color, word = spinner, ui.ACCENT, "connecting…"
    elif status == "live":
        dot, color, word = "●", ui.OK, f"live · {tool_count} tools"
    elif status == "failed":
        dot, color, word = "✗", ui.ERR, "failed"
    elif status == "auth":
        dot, color, word = "◐", ui.WARN, "sign-in"
    elif status == "warn":
        dot, color, word = "▲", ui.WARN, (f"check · {tool_count} tools" if health.get("connected") else "check")
    else:
        dot, color, word = "○", ui.FG_DIM, "idle"
    src_icon = _SOURCE_ICONS.get(source, "•")
    transport = cfg.get("type", "stdio")
    meta = f"{src_icon} {scope} · {transport}" + (" · auto" if is_auto else "")
    return picker_row(
        name,
        detail=_endpoint_text(cfg),
        right=f"{word}   {meta}",
        icon=dot,
        icon_style=f"bold {color}",
        title_style=f"bold {ui.FG}",
        title_width=18,
        right_style=color if status in ("live", "failed", "warn", "connecting", "auth") or connecting else ui.FG_DIM,
    )


# ── main MCP modal ───────────────────────────────────────────────────────

class MCPModalScreen(TuiModalScreen[None]):
    """Single modal for everything MCP — list, toggle, import."""

    DEFAULT_CSS = (
        TUI_MODAL_CHROME_CSS
        + """
    MCPModalScreen #modal {
        width: 88%;
        max-width: 140;
        max-height: 85%;
    }
    MCPModalScreen OptionList {
        height: 18;
    }
    MCPModalScreen #mcp_header {
        padding: 0 1;
        margin-bottom: 1;
        color: #e6edf3;
    }
    MCPModalScreen #mcp_status {
        padding: 0 1;
        margin-top: 1;
        color: {ui.FG_MUTE};
    }
    MCPModalScreen #mcp_status.ok { color: {ui.OK}; }
    MCPModalScreen #mcp_status.err { color: {ui.ERR}; }
    MCPModalScreen #mcp_status.connecting { color: {ui.WARN}; }
    MCPModalScreen #mcp_health {
        padding: 0 1;
        margin-top: 1;
        color: {ui.FG_MUTE};
        min-height: 1;
    }
    MCPModalScreen Input { margin-bottom: 1; }
    MCPModalScreen #mcp_buttons { height: 1; margin-top: 1; }
    MCPModalScreen #mcp_buttons Button { margin: 0 1 0 0; }
    """
    )

    BINDINGS = [
        Binding("escape", "cancel",   "Close",  show=True),
        Binding("space",  "toggle",   "Toggle", show=True),
        Binding("enter",  "toggle",   "Toggle", show=False),
        Binding("g",      "toggle_global", "Global", show=True),
        Binding("i",      "import_global", "Import", show=True),
        Binding("e",      "export_global", "Export", show=True),
        Binding("a",      "manual_add",    "Add",    show=True),
        Binding("r",      "refresh",  "Refresh", show=True),
        Binding("d",      "delete",   "Delete", show=True),
        Binding("o",      "sign_out", "Sign out", show=False),
        Binding("m",      "move",     "Move",   show=False),
        Binding("k",      "keys",     "Keys",   show=False),
        Binding("c",      "copy_link", "Copy link", show=False),
        Binding("slash",  "focus_filter", "Filter", show=False),
        Binding("down",   "cursor_down",  show=False),
        Binding("up",     "cursor_up",    show=False),
        Binding("pagedown", "page_down",  show=False),
        Binding("pageup",   "page_up",    show=False),
    ]

    def __init__(self, *, add: bool = False) -> None:
        super().__init__()
        self._open_add = add
        self._armed_delete: str | None = None
        self._filter: str = ""
        self._row_ids: list[str] = []   # parallel to OptionList rows
        self._connecting_names: set[str] = set()
        self._connect_status_msg: str = ""
        self._scope_busy: bool = False
        self._spinner_i: int = 0
        self._spinner_timer: Timer | None = None

    def compose(self) -> ComposeResult:
        with CenterMiddle():
            with Vertical(id="modal"):
                yield Static("▧  MCP Servers", id="modal_title")
                yield Static("", id="mcp_header")
                yield Input(placeholder="filter…  (press / to focus)", id="mcp_filter")
                yield OptionList(id="mcp_list")
                yield Static("", id="mcp_health")
                yield Static("", id="mcp_status")
                with Horizontal(id="mcp_buttons"):
                    yield Button("Connect", id="mcp_primary", variant="primary", compact=True)
                    yield Button("+ Add server", id="mcp_add", compact=True)
                    yield Button("Keys…", id="mcp_keys", compact=True)
                yield Static(
                    f"[bold {ui.FG_MUTE}]↵[/] connect / sign in   [bold {ui.FG_MUTE}]a[/] add   "
                    f"[bold {ui.FG_MUTE}]d[/] delete   [bold {ui.FG_MUTE}]m[/] move   "
                    f"[bold {ui.FG_MUTE}]o[/] sign out   [bold {ui.FG_MUTE}]k[/] keys   "
                    f"[bold {ui.FG_MUTE}]g[/] global   [bold {ui.FG_MUTE}]esc[/] close",
                    id="modal_hint",
                )

    # ── lifecycle ────────────────────────────────────────────────────────

    def on_mount(self) -> None:
        enable_mouse()
        try:
            self._prev_scroll = self.app.scroll_sensitivity_y
            self.app.scroll_sensitivity_y = 1.0
        except AttributeError:
            self._prev_scroll = None
        self._refresh_rows()
        self.query_one("#mcp_list", OptionList).focus()
        self._refresh_health_detail()
        subscribe = getattr(self.app, "mcp_subscribe", None)
        if subscribe:
            subscribe(self._registry_changed)
        if self._open_add:
            self.call_after_refresh(self.action_manual_add)

    def _registry_changed(self) -> None:
        if not self.is_mounted:
            return
        self._refresh_rows()

    def on_option_list_option_highlighted(
        self, event: OptionList.OptionHighlighted
    ) -> None:
        if event.option_list.id != "mcp_list":
            return
        if self._armed_delete and self._selected_name() != self._armed_delete:
            self._armed_delete = None
        self._refresh_health_detail()

    def on_unmount(self) -> None:
        disable_mouse()
        unsubscribe = getattr(self.app, "mcp_unsubscribe", None)
        if unsubscribe:
            unsubscribe(self._registry_changed)
        self._stop_connect_spinner()
        if self._prev_scroll is not None:
            try:
                self.app.scroll_sensitivity_y = self._prev_scroll
            except AttributeError:
                pass

    # ── helpers ──────────────────────────────────────────────────────────

    def _set_status(
        self,
        msg: str,
        *,
        ok: bool | None = None,
        connecting: bool = False,
    ) -> None:
        widget = self.query_one("#mcp_status", Static)
        widget.update(Text(msg))
        widget.set_class(ok is True, "ok")
        widget.set_class(ok is False, "err")
        widget.set_class(connecting, "connecting")

    def _spinner_char(self) -> str:
        frames = ui.SPINNER_FRAMES
        return frames[self._spinner_i % len(frames)]

    def _start_connect_spinner(self) -> None:
        if self._spinner_timer is not None:
            return
        self._spinner_i = 0
        self._spinner_timer = self.set_interval(0.1, self._tick_connect_spinner)

    def _stop_connect_spinner(self) -> None:
        if self._spinner_timer is not None:
            self._spinner_timer.stop()
            self._spinner_timer = None

    def _tick_connect_spinner(self) -> None:
        if not self._connecting_names and not self._scope_busy:
            self._stop_connect_spinner()
            return
        self._spinner_i += 1
        self._refresh_rows()
        if self._connect_status_msg:
            self._set_status(
                f"{self._spinner_char()} {self._connect_status_msg}",
                connecting=True,
            )

    def _update_connect_status(self) -> None:
        pending = len(self._connecting_names)
        if pending:
            self._connect_status_msg = (
                f"connecting {pending} server{'s' if pending != 1 else ''}…"
            )
            self._set_status(
                f"{self._spinner_char()} {self._connect_status_msg}",
                connecting=True,
            )

    def _health_color(self, status: str) -> str:
        return {
            "live": ui.OK,
            "idle": ui.FG_MUTE,
            "failed": ui.ERR,
            "warn": ui.WARN,
            "connecting": ui.WARN,
            "auth": ui.WARN,
        }.get(status, ui.FG_MUTE)

    def _header_health_bits(self, names: list[str]) -> Text:
        counts = mcp_registry.health_counts(names, connecting=self._connecting_names)
        parts: list[tuple[str, str]] = []
        if counts.get("live"):
            parts.append((f"{counts['live']} live", "green"))
        if counts.get("connecting"):
            parts.append((f"{counts['connecting']} connecting", "yellow"))
        if counts.get("auth"):
            parts.append((f"{counts['auth']} sign-in", "yellow"))
        if counts.get("failed"):
            parts.append((f"{counts['failed']} failed", "red"))
        if counts.get("warn"):
            parts.append((f"{counts['warn']} warn", "yellow"))
        if counts.get("idle"):
            parts.append((f"{counts['idle']} idle", "dim"))
        if not parts:
            return Text("")
        segs: list[tuple[str, str]] = [(" · ", "dim")]
        for i, (label, style) in enumerate(parts):
            if i:
                segs.append((" · ", "dim"))
            segs.append((label, style))
        return Text.assemble(*segs)

    def _refresh_health_detail(self) -> None:
        name = self._selected_name()
        try:
            widget = self.query_one("#mcp_health", Static)
        except Exception:
            return
        if not name:
            widget.update("")
            self._sync_buttons(None)
            return
        config = get_config()
        cfg = config.get_server(name)
        health = mcp_registry.get_server_health(
            name, cfg, connecting=name in self._connecting_names
        )
        detail = health.get("detail") or health.get("summary", "")
        hint = ""
        if health["status"] == "auth":
            hint = " — press Enter to sign in"
        elif health.get("needs_credentials"):
            hint = " — press k to enter " + ", ".join(health["needs_credentials"])
        elif health["status"] == "failed":
            hint = " — space to retry"
        elif health["status"] == "warn" and health.get("hints"):
            hint = " — fix hints then space to connect"
        widget.update(Text(detail + hint, style=self._health_color(health["status"])))
        self._sync_buttons(name, health)

    def _refresh_rows(self, keep_highlight: bool = True) -> None:
        """Re-read config and re-render the option list."""
        config = get_config()
        servers = config.list_servers()
        auto_connect = set(config.get_auto_connect())

        # Header line
        spinner = self._spinner_char()
        connecting_count = len(self._connecting_names)
        if connecting_count:
            scope_text = Text.assemble(
                ("scope: ", "dim"),
                (
                    ("◉ global on  ", "bold blue")
                    if config.include_global()
                    else ("▣ project-only  ", "bold magenta")
                ),
                (f"{spinner} connecting {connecting_count} server", "bold yellow"),
                ("s…" if connecting_count != 1 else "…", "bold yellow"),
            )
        elif config.include_global():
            scope_text = Text.assemble(
                ("scope: ", "dim"),
                ("◉ global on  ", "bold blue"),
                (f"{len(servers)} servers", "dim"),
                self._header_health_bits(sorted(servers.keys())),
            )
        else:
            scope_text = Text.assemble(
                ("scope: ", "dim"),
                ("▣ project-only  ", "bold magenta"),
                (f"{len(servers)} servers", "dim"),
                self._header_health_bits(sorted(servers.keys())),
                ("  ", ""),
                ("(press g to load Claude Code / OpenCode / Cursor / …)", "dim"),
            )
        self.query_one("#mcp_header", Static).update(scope_text)

        # Apply filter
        names_sorted = sorted(servers.keys())
        if self._filter:
            f = self._filter.lower()
            names_sorted = [
                n for n in names_sorted
                if f in n.lower()
                or f in str(servers[n].get("command", "")).lower()
                or f in str(servers[n].get("url", "")).lower()
            ]

        opts = self.query_one("#mcp_list", OptionList)
        prev_idx = opts.highlighted if keep_highlight else None
        opts.clear_options()
        self._row_ids = []

        if not names_sorted:
            opts.add_option(
                Option(
                    Text("  (no servers match this filter)", style="dim italic"),
                    id="__empty__",
                    disabled=True,
                )
            )
            return

        for name in names_sorted:
            cfg = servers[name]
            scope = config.get_scope(name) or "global"
            source = config.get_source(name) or scope
            row_id = f"srv::{name}"
            self._row_ids.append(name)
            is_connecting = name in self._connecting_names
            health = mcp_registry.get_server_health(
                name, cfg, connecting=is_connecting
            )
            opts.add_option(
                Option(
                    _row_label(
                        name,
                        cfg,
                        scope,
                        source,
                        name in auto_connect,
                        connecting=is_connecting,
                        spinner=spinner,
                        health=health,
                    ),
                    id=row_id,
                )
            )

        if prev_idx is not None and prev_idx < len(self._row_ids):
            opts.highlighted = prev_idx
        elif self._row_ids:
            opts.highlighted = 0
        self._refresh_health_detail()

    def _selected_name(self) -> str | None:
        opts = self.query_one("#mcp_list", OptionList)
        idx = opts.highlighted
        if idx is None or idx >= len(self._row_ids):
            return None
        return self._row_ids[idx]

    # ── actions ──────────────────────────────────────────────────────────

    def action_cancel(self) -> None:
        self.dismiss(None)

    def action_cursor_down(self) -> None:
        self.query_one("#mcp_list", OptionList).action_cursor_down()

    def action_cursor_up(self) -> None:
        self.query_one("#mcp_list", OptionList).action_cursor_up()

    def action_page_down(self) -> None:
        self.query_one("#mcp_list", OptionList).action_page_down()

    def action_page_up(self) -> None:
        self.query_one("#mcp_list", OptionList).action_page_up()

    def action_focus_filter(self) -> None:
        self.query_one("#mcp_filter", Input).focus()

    def action_refresh(self) -> None:
        from ..mcp.scope import apply_mcp_scope_change
        apply_mcp_scope_change()
        self._refresh_rows()
        self._set_status("config reloaded", ok=True)

    def _sync_buttons(self, name: str | None, health: dict | None = None) -> None:
        try:
            primary = self.query_one("#mcp_primary", Button)
            keys = self.query_one("#mcp_keys", Button)
        except Exception:
            return
        if not name:
            primary.display = False
            keys.display = False
            return
        health = health or mcp_registry.get_server_health(name, get_config().get_server(name))
        status = health.get("status")
        primary.display = True
        primary.label = (
            "Authenticate" if status == "auth"
            else "Disconnect" if health.get("connected")
            else "Connect"
        )
        keys.display = bool(health.get("needs_credentials"))

    def on_button_pressed(self, event: Button.Pressed) -> None:
        event.stop()
        bid = event.button.id
        if bid == "mcp_primary":
            self.action_toggle()
        elif bid == "mcp_add":
            self.action_manual_add()
        elif bid == "mcp_keys":
            self.action_keys()

    def action_authenticate(self) -> None:
        name = self._selected_name()
        if not name:
            return
        self._sign_in(name)

    def _sign_in(self, name: str) -> None:
        from .extension_modals import McpSignInScreen

        def after(ok: bool | None) -> None:
            self._refresh_rows()
            if ok:
                tools = len(mcp_registry.get_server_tools(name))
                self._set_status(f"connected {name} — {tools} tools", ok=True)
            elif mcp_registry.get_server_health(name).get("status") == "auth":
                self._set_status(f"{name}: still waiting for sign-in — Enter to reopen", ok=None)

        self.app.push_screen(McpSignInScreen(name), after)

    def action_copy_link(self) -> None:
        from ..mcp.auth import coordinator

        name = self._selected_name()
        req = coordinator.get(name or "")
        if req is None or not req.url:
            return
        copier = getattr(self.app, "_copy_text", None)
        if copier:
            copier(req.url)
            self._set_status("sign-in link copied", ok=True)

    def action_sign_out(self) -> None:
        name = self._selected_name()
        if not name:
            return
        cfg = get_config().get_server(name) or {}
        if not cfg.get("url"):
            self._set_status(f"{name} is a local server — nothing to sign out of", ok=False)
            return
        mcp_registry.sign_out(name, cfg)
        from ..mcp.scope import invalidate_mcp_prompt_cache
        invalidate_mcp_prompt_cache()
        self._refresh_rows()
        self._set_status(f"signed out of {name} (saved login forgotten)", ok=True)

    def action_keys(self) -> None:
        name = self._selected_name()
        if not name:
            return
        health = mcp_registry.get_server_health(name, get_config().get_server(name))
        needed = list(health.get("needs_credentials") or [])
        if not needed:
            self._set_status(f"{name} isn't waiting for any keys", ok=False)
            return
        from .extension_modals import KeysScreen

        def after(values: dict | None) -> None:
            if not values:
                return
            self._connecting_names.add(name)
            self._connect_status_msg = f"saving keys and connecting {name}…"
            self._start_connect_spinner()
            self._refresh_rows()
            self._keys_worker(name, values)

        self.app.push_screen(KeysScreen(name, needed), after)

    @work(thread=True)
    def _keys_worker(self, name: str, values: dict) -> None:
        from ..mcp.install import set_credentials

        try:
            res = set_credentials(name, values)
        except Exception as exc:
            res = {"ok": False, "status": "failed", "error": str(exc)}
        try:
            self.app.call_from_thread(self._keys_done, name, res)
        except Exception:
            pass

    def _keys_done(self, name: str, res: dict) -> None:
        self._connecting_names.discard(name)
        if not self._connecting_names:
            self._stop_connect_spinner()
        st = res.get("status")
        if st == "connected":
            self._set_status(f"connected {name} — {res.get('tool_count', 0)} tools", ok=True)
        elif st == "auth_required":
            self._set_status(f"{name}: keys saved — sign-in needed (Enter)", ok=None)
        else:
            self._set_status(f"{name}: {res.get('error') or res.get('message') or 'not connected'}", ok=False)
        from ..mcp.scope import invalidate_mcp_prompt_cache
        invalidate_mcp_prompt_cache()
        self._refresh_rows()

    def action_move(self) -> None:
        name = self._selected_name()
        if not name:
            return
        config = get_config()
        scope = config.get_scope(name)
        source = config.get_source(name)
        if source not in ("project", "jarvis"):
            self._set_status(f"{name} comes from {source or 'another tool'} — move it there", ok=False)
            return
        from ..mcp.install import move_mcp

        res = move_mcp(name, "global" if scope == "project" else "project")
        self._refresh_rows()
        self._set_status(res.get("message") or res.get("error", "could not move"), ok=bool(res.get("ok")))

    def action_toggle(self) -> None:
        name = self._selected_name()
        if not name or name in self._connecting_names or self._scope_busy:
            return
        config = get_config()
        if not mcp_registry.is_connected(name):
            health = mcp_registry.get_server_health(name, config.get_server(name))
            if health.get("status") == "auth":
                self._sign_in(name)
                return
        if mcp_registry.is_connected(name):
            err = mcp_registry.disconnect(name)
            if err:
                self._set_status(f"disconnect failed: {err}", ok=False)
            else:
                from ..mcp.scope import invalidate_mcp_prompt_cache
                invalidate_mcp_prompt_cache()
                self._set_status(f"disconnected {name}", ok=True)
        else:
            cfg = config.get_server(name)
            if cfg is None:
                self._set_status(f"{name}: not in current scope (press g)", ok=False)
                return
            self._connecting_names.add(name)
            self._connect_status_msg = f"connecting {name}…"
            self._start_connect_spinner()
            self._set_status(
                f"{self._spinner_char()} connecting {name}…",
                connecting=True,
            )
            self._refresh_rows()

            def _do_connect() -> None:
                err = mcp_registry.connect(name, cfg)
                self.app.call_from_thread(self._after_connect, name, err)

            threading.Thread(target=_do_connect, daemon=True).start()
            return
        self._refresh_rows()

    def _after_connect(self, name: str, err: str | None) -> None:
        from ..mcp.scope import invalidate_mcp_prompt_cache

        self._connecting_names.discard(name)
        if not self._connecting_names and not self._scope_busy:
            self._stop_connect_spinner()
            self._connect_status_msg = ""

        if err and needs_auth(err):
            self._set_status(f"{name} needs sign-in", ok=None)
            invalidate_mcp_prompt_cache()
            self._refresh_rows()
            self._sign_in(name)
            return
        if err:
            self._set_status(f"connect failed: {err}", ok=False)
        else:
            tools = len(mcp_registry.get_server_tools(name))
            self._set_status(f"connected {name} — {tools} tools", ok=True)
        invalidate_mcp_prompt_cache()
        self._refresh_rows()
        self._refresh_health_detail()

    def _begin_global_connect(self, pending: list[str], server_count: int) -> None:
        self._connecting_names = set(pending)
        if pending:
            self._connect_status_msg = (
                f"connecting {len(pending)} of {server_count} server"
                f"{'s' if server_count != 1 else ''}…"
            )
            self._update_connect_status()
        self._refresh_rows()

    def _on_connect_start(self, name: str) -> None:
        self._connecting_names.add(name)
        self._update_connect_status()
        self._refresh_rows()

    def _on_connect_done(self, name: str, _err: str | None) -> None:
        self._connecting_names.discard(name)
        self._update_connect_status()
        self._refresh_rows()

    def _after_global_reconcile(self, result: dict) -> None:
        self._connecting_names.clear()
        self._connect_status_msg = ""
        self._scope_busy = False
        self._stop_connect_spinner()

        connected = result.get("connected", [])
        failed = result.get("failed", [])
        if state.global_mcp:
            if connected and failed:
                self._set_status(
                    f"global ON — connected {len(connected)}, failed {len(failed)}",
                    ok=False,
                )
            elif failed:
                names = ", ".join(n for n, _ in failed[:3])
                suffix = "…" if len(failed) > 3 else ""
                self._set_status(
                    f"global ON — connect failed: {names}{suffix}",
                    ok=False,
                )
            elif connected:
                self._set_status(
                    f"global ON — connected {len(connected)} server"
                    f"{'s' if len(connected) != 1 else ''}",
                    ok=True,
                )
            else:
                self._set_status("global scope ON", ok=True)
        else:
            self._set_status("global scope OFF — project servers only", ok=True)
        self._refresh_rows()

    def action_toggle_global(self) -> None:
        if self._scope_busy or self._connecting_names:
            return

        state.global_mcp = not state.global_mcp
        state.save_mcp_config()

        from ..mcp.scope import apply_mcp_scope_change

        enabling = state.global_mcp
        self._scope_busy = True
        self._connect_status_msg = (
            "loading global MCP config…" if enabling else "updating scope…"
        )
        self._start_connect_spinner()
        self._set_status(
            f"{self._spinner_char()} {self._connect_status_msg}",
            connecting=True,
        )

        def _reconcile() -> None:
            from ..mcp.config import reload_config

            config = reload_config()
            visible = set(config.list_servers().keys())
            if enabling:
                pending = [
                    n for n in sorted(visible)
                    if not mcp_registry.is_connected(n)
                ]
                self.app.call_from_thread(
                    self._begin_global_connect,
                    pending,
                    len(visible),
                )

            def on_start(name: str) -> None:
                self.app.call_from_thread(self._on_connect_start, name)

            def on_done(name: str, err: str | None) -> None:
                self.app.call_from_thread(self._on_connect_done, name, err)

            result = apply_mcp_scope_change(
                connect_all=enabling,
                on_connect_start=on_start if enabling else None,
                on_connect_done=on_done if enabling else None,
            )
            self.app.call_from_thread(self._after_global_reconcile, result)

        threading.Thread(target=_reconcile, daemon=True).start()

    def action_import_global(self) -> None:
        """Copy the highlighted global MCP server into project .mcp.json."""
        if self._scope_busy or self._connecting_names:
            return
        name = self._selected_name()
        if not name:
            return

        config = get_config()
        if config.get_scope(name) == "project":
            self._set_status(f"{name} is already in project .mcp.json", ok=False)
            return

        try:
            result = import_server_to_project(name)
        except OSError as e:
            self._set_status(f"import failed: {e}", ok=False)
            return

        if result.get("error"):
            self._set_status(str(result["error"]), ok=False)
            return

        reload_config()
        self._refresh_rows()

        dest = result.get("path", MCP_PROJECT_CONFIG_FILENAME)
        if result.get("added"):
            self._set_status(f"imported {name} → {dest}", ok=True)
        elif result.get("skipped"):
            self._set_status(f"{name} already in project", ok=True)
        else:
            self._set_status("nothing to import", ok=False)

    def action_export_global(self) -> None:
        """Copy the highlighted project MCP server into the Jarvis global config."""
        if self._scope_busy or self._connecting_names:
            return
        name = self._selected_name()
        if not name:
            return

        config = get_config()
        if config.get_scope(name) == "global":
            self._set_status(f"{name} is already in global MCP config", ok=False)
            return

        try:
            result = export_server_to_global(name)
        except OSError as e:
            self._set_status(f"export failed: {e}", ok=False)
            return

        if result.get("error"):
            self._set_status(str(result["error"]), ok=False)
            return

        reload_config()
        self._refresh_rows()

        dest = result.get("path", "~/.config/harness-agent/mcp.json")
        if result.get("added"):
            self._set_status(f"exported {name} → {dest}", ok=True)
        elif result.get("skipped"):
            self._set_status(f"{name} already in global", ok=True)
        else:
            self._set_status("nothing to export", ok=False)

    def action_manual_add(self) -> None:
        from .extension_modals import McpAddScreen

        def after(result: dict | None) -> None:
            if not result:
                return
            reload_config()
            self._refresh_rows()
            servers = result.get("servers") or []
            done = [r["name"] for r in servers if r.get("status") in ("connected", "added", "auth_required", "needs_credentials")]
            if done:
                self._set_status(f"added {', '.join(done[:5])}" + ("…" if len(done) > 5 else ""), ok=True)
            from ..mcp.scope import invalidate_mcp_prompt_cache
            invalidate_mcp_prompt_cache()

        self.app.push_screen(McpAddScreen(), after)

    def action_delete(self) -> None:
        name = self._selected_name()
        if not name:
            return
        config = get_config()
        source = config.get_source(name)
        if source not in ("project", "jarvis"):
            self._set_status(
                f"{name} is provided by {source} — remove it in that tool, not Jarvis", ok=False,
            )
            return
        if self._armed_delete != name:
            self._armed_delete = name
            self._set_status(f"press d again to remove {name} ({config.get_scope(name)})", ok=False)
            return
        self._armed_delete = None
        from ..mcp.install import remove_mcp

        res = remove_mcp(name, scope=config.get_scope(name))
        self._refresh_rows()
        self._set_status(res.get("message") or res.get("error", "could not remove"), ok=bool(res.get("ok")))

    # ── filter input events ──────────────────────────────────────────────

    def on_input_changed(self, event: Input.Changed) -> None:
        if event.input.id == "mcp_filter":
            self._filter = event.value or ""
            self._refresh_rows(keep_highlight=False)

    def on_input_submitted(self, event: Input.Submitted) -> None:
        if event.input.id == "mcp_filter":
            self.query_one("#mcp_list", OptionList).focus()
