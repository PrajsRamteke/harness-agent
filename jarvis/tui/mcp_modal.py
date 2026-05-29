"""Centered MCP control modal — one place for every MCP action.

User interaction:

* ↑/↓        navigate the server list
* Space/Enter  toggle the highlighted server (connect ↔ disconnect)
* g           toggle global scope (project-only ↔ project + global)
* i           import the highlighted global server into this project's .mcp.json
* a           manually add MCP JSON (paste/type in input area)
* r           re-scan all config files
* d           delete a Jarvis-managed global server (refuses other tools' entries)
* /           focus the filter input
* Esc         close

Designed to make ``/mcp`` the only MCP command a user ever needs.
"""

from __future__ import annotations

import json
import threading

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import CenterMiddle, Vertical
from textual.timer import Timer
from textual.widgets import Input, OptionList, Static, TextArea
from textual.widgets.option_list import Option

from rich.text import Text

from .modal_chrome import TUI_MODAL_CHROME_CSS, TuiModalScreen
from .mouse_toggle import enable_mouse, disable_mouse
from . import theme as ui
from .. import state
from ..mcp.config import (
    MCP_PROJECT_CONFIG_FILENAME,
    get_config,
    import_server_to_project,
    merge_json_into_project,
    reload_config,
    _project_config_path,
)
from ..mcp.registry import mcp_registry
from ..mcp.sources import SOURCE_ICONS as _SOURCE_ICONS, format_endpoint as _endpoint_text
from ..tools.mac.clipboard import clipboard_get


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
) -> Text:
    health = health or mcp_registry.get_server_health(name, cfg, connecting=connecting)
    status = health.get("status", "idle")
    tool_count = health.get("tool_count", 0)

    if connecting or status == "connecting":
        status_dot = (spinner, "bold yellow")
        status_word = (" connecting", "yellow")
    elif status == "live":
        status_dot = ("●", "bold green")
        status_word = (f" live {tool_count}t", "green")
    elif status == "failed":
        status_dot = ("✗", "bold red")
        status_word = (" failed", "red")
    elif status == "warn":
        status_dot = ("⚠", "bold yellow")
        if health.get("connected"):
            status_word = (f" warn {tool_count}t", "yellow")
        else:
            status_word = (" check", "yellow")
    else:
        status_dot = ("○", "dim")
        status_word = (" idle", "dim")
    scope_color = "magenta" if scope == "project" else "blue"
    src_icon = _SOURCE_ICONS.get(source, "•")
    auto_tag = (" auto", "cyan") if is_auto else ("     ", "")
    transport = cfg.get("type", "stdio")

    return Text.assemble(
        ("  ", ""),
        status_dot,
        status_word,
        ("  ", ""),
        (f"{name:<18}", "bold white"),
        (f"  {src_icon} ", scope_color),
        (f"{scope:<7}", scope_color),
        auto_tag,
        ("  ", ""),
        (f"{transport:<6}", "dim"),
        ("  ", ""),
        (_endpoint_text(cfg), "dim"),
    )


# ── manual add sub-modal ─────────────────────────────────────────────────

_CLAUDE_EXAMPLE = json.dumps(
    {
        "mcpServers": {
            "filesystem": {
                "command": "npx",
                "args": [
                    "-y",
                    "@modelcontextprotocol/server-filesystem",
                    "/Users/you/projects",
                ],
            }
        }
    },
    indent=2,
)

_JARVIS_EXAMPLE = json.dumps(
    {
        "servers": {
            "filesystem": {
                "type": "stdio",
                "command": "npx",
                "args": [
                    "-y",
                    "@modelcontextprotocol/server-filesystem",
                    "/Users/you/projects",
                ],
            }
        },
        "auto_connect": ["filesystem"],
    },
    indent=2,
)


class ManualAddScreen(TuiModalScreen[dict | None]):
    """Paste or type MCP JSON into the project ``.mcp.json``."""

    DEFAULT_CSS = (
        TUI_MODAL_CHROME_CSS
        + """
    ManualAddScreen #modal {
        width: 86%;
        max-width: 120;
        max-height: 88%;
    }
    ManualAddScreen #import_help {
        padding: 0 1;
        color: {ui.FG_MUTE};
    }
    ManualAddScreen #import_examples {
        padding: 0 1;
        margin: 1 0;
        color: {ui.FG_DIM};
        max-height: 10;
        overflow-y: auto;
    }
    ManualAddScreen TextArea {
        height: 14;
        margin: 0 0 1 0;
    }
    ManualAddScreen #import_status {
        color: {ui.ERR};
        padding: 0 1;
        margin-top: 1;
    }
    ManualAddScreen #import_status.ok { color: {ui.OK}; }
    """
    )

    BINDINGS = [
        Binding("escape", "cancel", "Cancel", show=True),
        Binding("ctrl+s", "submit", "Submit", show=True),
        Binding("p", "paste_clipboard", "Paste", show=True),
    ]

    def compose(self) -> ComposeResult:
        project_file = _project_config_path()
        with CenterMiddle():
            with Vertical(id="modal"):
                yield Static("▶  Add MCP Server", id="modal_title")
                yield Static(
                    Text.from_markup(
                        f"[{ui.FG_MUTE}]Paste or edit JSON below — saved to[/]\n"
                        f"[{ui.ACCENT}]{project_file}[/]"
                    ),
                    id="import_help",
                )
                yield Static(self._examples_text(), id="import_examples")
                yield TextArea("", id="import_input", show_line_numbers=False)
                yield Static("", id="import_status")
                yield Static(
                    f"[{ui.ACCENT_3}]ctrl+s[/] submit   [{ui.ACCENT_3}]p[/] paste clipboard   "
                    f"[{ui.ACCENT_3}]esc[/] cancel",
                    id="modal_hint",
                )

    @staticmethod
    def _examples_text() -> Text:
        return Text.assemble(
            ("Claude Code\n", "bold"),
            (_CLAUDE_EXAMPLE, "dim"),
            ("\n\nJarvis / project .mcp.json\n", "bold"),
            (_JARVIS_EXAMPLE, "dim"),
        )

    def on_mount(self) -> None:
        enable_mouse()
        self.query_one("#import_input", TextArea).focus()

    def on_unmount(self) -> None:
        disable_mouse()

    def action_cancel(self) -> None:
        self.dismiss(None)

    def action_paste_clipboard(self) -> None:
        raw = clipboard_get()
        if not raw.strip():
            self._set_status("clipboard is empty", ok=False)
            return
        self.query_one("#import_input", TextArea).text = raw
        self._set_status("pasted from clipboard — review and press ctrl+s", ok=True)

    def action_submit(self) -> None:
        raw = self.query_one("#import_input", TextArea).text.strip()
        self._import_raw(raw)

    def _import_raw(self, raw: str) -> None:
        if not raw:
            self._set_status("paste or type JSON first (or press p)", ok=False)
            return
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError as e:
            self._set_status(f"invalid JSON: {e}", ok=False)
            return

        try:
            result = merge_json_into_project(parsed)
        except ValueError as e:
            self._set_status(str(e), ok=False)
            return

        added = result.get("added", [])
        if not added:
            skipped = result.get("skipped", [])
            if skipped:
                self._set_status(
                    f"no new servers — already in project: {', '.join(skipped[:5])}",
                    ok=False,
                )
            else:
                self._set_status("no servers found in that JSON", ok=False)
            return

        self.dismiss(result)

    def _set_status(self, msg: str, ok: bool) -> None:
        widget = self.query_one("#import_status", Static)
        widget.update(Text(msg, style="bold green" if ok else "bold red"))
        widget.set_class(ok, "ok")


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
    """
    )

    BINDINGS = [
        Binding("escape", "cancel",   "Close",  show=True),
        Binding("space",  "toggle",   "Toggle", show=True),
        Binding("enter",  "toggle",   "Toggle", show=False),
        Binding("g",      "toggle_global", "Global", show=True),
        Binding("i",      "import_global", "Import", show=True),
        Binding("a",      "manual_add",    "Add",    show=True),
        Binding("r",      "refresh",  "Refresh", show=True),
        Binding("d",      "delete",   "Delete", show=True),
        Binding("slash",  "focus_filter", "Filter", show=False),
        Binding("down",   "cursor_down",  show=False),
        Binding("up",     "cursor_up",    show=False),
        Binding("pagedown", "page_down",  show=False),
        Binding("pageup",   "page_up",    show=False),
    ]

    def __init__(self) -> None:
        super().__init__()
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
                yield Static(
                    f"[{ui.ACCENT_3}]space[/] toggle   [{ui.ACCENT_3}]g[/] global   "
                    f"[{ui.ACCENT_3}]i[/] import selected   [{ui.ACCENT_3}]a[/] add   "
                    f"[{ui.ACCENT_3}]r[/] refresh   [{ui.ACCENT_3}]d[/] delete   "
                    f"[{ui.ACCENT_3}]esc[/] close",
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

    def on_option_list_option_highlighted(
        self, event: OptionList.OptionHighlighted
    ) -> None:
        if event.option_list.id != "mcp_list":
            return
        self._refresh_health_detail()

    def on_unmount(self) -> None:
        disable_mouse()
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
        }.get(status, ui.FG_MUTE)

    def _header_health_bits(self, names: list[str]) -> Text:
        counts = mcp_registry.health_counts(names, connecting=self._connecting_names)
        parts: list[tuple[str, str]] = []
        if counts.get("live"):
            parts.append((f"{counts['live']} live", "green"))
        if counts.get("connecting"):
            parts.append((f"{counts['connecting']} connecting", "yellow"))
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
            return
        config = get_config()
        cfg = config.get_server(name)
        health = mcp_registry.get_server_health(
            name, cfg, connecting=name in self._connecting_names
        )
        detail = health.get("detail") or health.get("summary", "")
        hint = ""
        if health["status"] == "failed":
            hint = " — space to retry"
        elif health["status"] == "warn" and health.get("hints"):
            hint = " — fix hints then space to connect"
        widget.update(Text(detail + hint, style=self._health_color(health["status"])))

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

    def action_toggle(self) -> None:
        name = self._selected_name()
        if not name or name in self._connecting_names or self._scope_busy:
            return
        config = get_config()
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

    def action_manual_add(self) -> None:
        def after(result: dict | None) -> None:
            if not result:
                return
            added = result.get("added", [])
            skipped = result.get("skipped", [])
            reload_config()
            self._refresh_rows()
            msg = f"added {len(added)} to project: {', '.join(added[:6])}"
            if len(added) > 6:
                msg += "…"
            if skipped:
                msg += f" ({len(skipped)} skipped)"
            self._set_status(msg, ok=True)

        self.app.push_screen(ManualAddScreen(), after)

    def action_delete(self) -> None:
        name = self._selected_name()
        if not name:
            return
        config = get_config()
        scope = config.get_scope(name)
        source = config.get_source(name)
        if scope == "project":
            self._set_status(
                f"{name} is in {_project_config_path()}; edit that file directly",
                ok=False,
            )
            return
        if source and source != "jarvis":
            self._set_status(
                f"{name} is provided by {source} — remove it in that tool, not Jarvis",
                ok=False,
            )
            return
        try:
            if mcp_registry.is_connected(name):
                mcp_registry.disconnect(name)
            if config.remove_server(name):
                config.save()
                reload_config()
                self._refresh_rows()
                self._set_status(f"removed {name}", ok=True)
            else:
                self._set_status(f"{name} not found", ok=False)
        except Exception as e:
            self._set_status(f"delete failed: {e}", ok=False)

    # ── filter input events ──────────────────────────────────────────────

    def on_input_changed(self, event: Input.Changed) -> None:
        if event.input.id == "mcp_filter":
            self._filter = event.value or ""
            self._refresh_rows(keep_highlight=False)

    def on_input_submitted(self, event: Input.Submitted) -> None:
        if event.input.id == "mcp_filter":
            self.query_one("#mcp_list", OptionList).focus()
