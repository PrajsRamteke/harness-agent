"""Centered MCP control modal — one place for every MCP action.

User interaction:

* ↑/↓        navigate the server list
* Space/Enter  toggle the highlighted server (connect ↔ disconnect)
* g           toggle global scope (project-only ↔ project + global)
* i           open the JSON-import sub-modal to add a new server
* r           re-scan all config files
* d           delete a Jarvis-managed global server (refuses other tools' entries)
* /           focus the filter input
* Esc         close

Designed to make ``/mcp`` the only MCP command a user ever needs.
"""

from __future__ import annotations

import json
import threading
from typing import Any

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import CenterMiddle, Vertical
from textual.widgets import Input, OptionList, Static, TextArea
from textual.widgets.option_list import Option

from rich.text import Text

from .modal_chrome import TUI_MODAL_CHROME_CSS, TuiModalScreen
from .mouse_toggle import enable_mouse, disable_mouse
from . import theme as ui
from .. import state
from ..mcp.config import (
    MCP_GLOBAL_CONFIG_FILE,
    MCP_PROJECT_CONFIG_FILENAME,
    get_config,
    reload_config,
    _project_config_path,
)
from ..mcp.registry import mcp_registry


_SOURCE_ICONS = {
    "project":  "▣",
    "jarvis":   "✦",
    "claude":   "◆",
    "opencode": "◇",
    "cursor":   "⌘",
    "windsurf": "≈",
    "vscode":   "⬡",
}


# ── helpers ──────────────────────────────────────────────────────────────

def _endpoint_text(cfg: dict, max_len: int = 64) -> str:
    if cfg.get("url"):
        s = str(cfg["url"])
    else:
        parts = [str(cfg.get("command", ""))]
        parts += [str(a) for a in cfg.get("args", [])]
        s = " ".join(p for p in parts if p)
    if len(s) > max_len:
        s = s[: max_len - 1] + "…"
    return s


def _row_label(name: str, cfg: dict, scope: str, source: str, is_auto: bool) -> Text:
    connected = mcp_registry.is_connected(name)
    tool_count = len(mcp_registry.get_server_tools(name))
    status_dot = ("●", "bold green") if connected else ("○", "dim")
    status_word = (
        (f" live {tool_count}t", "green") if connected else (" idle", "dim")
    )
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


# ── JSON import sub-modal ────────────────────────────────────────────────

class JSONImportScreen(TuiModalScreen[dict | None]):
    """Paste a JSON snippet that adds servers to the Jarvis global file.

    Accepts either Claude Code style ``{"mcpServers": {...}}`` or Jarvis
    style ``{"servers": {...}, "auto_connect": [...]}``. A single bare server
    entry also works — the user is prompted for the name.
    """

    DEFAULT_CSS = (
        TUI_MODAL_CHROME_CSS
        + """
    JSONImportScreen #modal {
        width: 82%;
        max-width: 110;
        max-height: 80%;
    }
    JSONImportScreen TextArea {
        height: 16;
        margin: 1 0;
    }
    JSONImportScreen #import_status {
        color: {ui.ERR};
        padding: 0 1;
        margin-top: 1;
    }
    JSONImportScreen #import_status.ok { color: {ui.OK}; }
    JSONImportScreen #import_help { padding: 0 1; }
    """
    )

    BINDINGS = [
        Binding("escape", "cancel", "Cancel", show=True),
        Binding("ctrl+s", "save", "Import", show=True),
    ]

    def compose(self) -> ComposeResult:
        with CenterMiddle():
            with Vertical(id="modal"):
                yield Static("▶  Import MCP Server", id="modal_title")
                yield Static(
                    Text.from_markup(
                        f"[{ui.FG_MUTE}]Paste one of:[/]\n"
                        f'  [{ui.ACCENT}]{"mcpServers": {"my-srv": {"command": "npx", "args": ["-y", "@..."]}}}[/]\n'
                        f'  [{ui.ACCENT}]{"servers": {"my-srv": {"type": "stdio", "command": "..."}}, "auto_connect": ["my-srv"]}[/]\n'
                        f'  [{ui.ACCENT}]{"name": "my-srv", "command": "npx", "args": [...]}[/]   [{ui.FG_DIM}](bare form)[/]'
                    ),
                    id="import_help",
                )
                yield TextArea("", id="import_input", show_line_numbers=False)
                yield Static("", id="import_status")
                yield Static(
                    f"[{ui.ACCENT_3}]ctrl+s[/] import   [{ui.ACCENT_3}]esc[/] cancel",
                    id="modal_hint",
                )

    def on_mount(self) -> None:
        enable_mouse()
        self.query_one("#import_input", TextArea).focus()

    def on_unmount(self) -> None:
        disable_mouse()

    # ── actions ──────────────────────────────────────────────────────────
    def action_cancel(self) -> None:
        self.dismiss(None)

    def action_save(self) -> None:
        raw = self.query_one("#import_input", TextArea).text.strip()
        if not raw:
            self._set_status("paste a JSON snippet first", ok=False)
            return
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError as e:
            self._set_status(f"invalid JSON: {e}", ok=False)
            return

        try:
            added = _ingest_json(parsed)
        except ValueError as e:
            self._set_status(str(e), ok=False)
            return

        if not added:
            self._set_status("no servers found in that JSON", ok=False)
            return

        self.dismiss({"added": added})

    def _set_status(self, msg: str, ok: bool) -> None:
        widget = self.query_one("#import_status", Static)
        widget.update(Text(msg, style="bold green" if ok else "bold red"))
        widget.set_class(ok, "ok")


def _ingest_json(parsed: Any) -> list[str]:
    """Merge user-pasted JSON into the Jarvis global mcp.json. Returns added names."""
    if not isinstance(parsed, dict):
        raise ValueError("JSON root must be an object")

    candidates: list[tuple[str, dict]] = []
    auto_names: list[str] = []

    # ── Format 1: Claude Code ── {"mcpServers": {...}}
    if isinstance(parsed.get("mcpServers"), dict):
        for n, c in parsed["mcpServers"].items():
            candidates.append((str(n), c))
            auto_names.append(str(n))

    # ── Format 2: Jarvis ── {"servers": {...}, "auto_connect": [...]}
    elif isinstance(parsed.get("servers"), dict):
        for n, c in parsed["servers"].items():
            candidates.append((str(n), c))
        auto_raw = parsed.get("auto_connect", [])
        if isinstance(auto_raw, list):
            auto_names = [str(x) for x in auto_raw]

    # ── Format 3: bare object ── {"name": "...", "command": "...", ...}
    elif "command" in parsed or "url" in parsed:
        name = parsed.get("name") or parsed.get("id")
        if not name:
            raise ValueError("bare-form JSON needs a 'name' field")
        candidates.append((str(name), parsed))
        auto_names.append(str(name))

    else:
        raise ValueError(
            "JSON must contain 'mcpServers', 'servers', or a bare 'command'/'url'"
        )

    if not candidates:
        return []

    # Load existing global file (or start fresh).
    existing: dict[str, Any] = {"servers": {}, "auto_connect": []}
    if MCP_GLOBAL_CONFIG_FILE.exists():
        try:
            on_disk = json.loads(MCP_GLOBAL_CONFIG_FILE.read_text(encoding="utf-8"))
            if isinstance(on_disk, dict):
                if isinstance(on_disk.get("servers"), dict):
                    existing["servers"] = dict(on_disk["servers"])
                if isinstance(on_disk.get("auto_connect"), list):
                    existing["auto_connect"] = list(on_disk["auto_connect"])
        except (OSError, json.JSONDecodeError):
            pass

    from ..mcp.config import _normalize_claude_code_entry

    added: list[str] = []
    for name, cfg in candidates:
        if name in existing["servers"]:
            raise ValueError(f"server '{name}' already exists — remove it first")
        normalized = _normalize_claude_code_entry(cfg) if not isinstance(cfg.get("type"), str) or cfg.get("type") in (
            "stdio", "sse", "local", "remote", "http"
        ) else None
        # If the entry already looks like a Jarvis schema, accept verbatim.
        if cfg.get("type") in ("stdio", "sse") and ("command" in cfg or "url" in cfg):
            normalized = dict(cfg)
        if normalized is None:
            normalized = _normalize_claude_code_entry(cfg)
        if normalized is None:
            raise ValueError(f"server '{name}': missing command or url")
        existing["servers"][name] = normalized
        added.append(name)

    for n in auto_names:
        if n in existing["servers"] and n not in existing["auto_connect"]:
            existing["auto_connect"].append(n)

    MCP_GLOBAL_CONFIG_FILE.parent.mkdir(parents=True, exist_ok=True)
    MCP_GLOBAL_CONFIG_FILE.write_text(
        json.dumps(existing, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    return added


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
    MCPModalScreen Input { margin-bottom: 1; }
    """
    )

    BINDINGS = [
        Binding("escape", "cancel",   "Close",  show=True),
        Binding("space",  "toggle",   "Toggle", show=True),
        Binding("enter",  "toggle",   "Toggle", show=False),
        Binding("g",      "toggle_global", "Global", show=True),
        Binding("i",      "import",   "Import", show=True),
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

    def compose(self) -> ComposeResult:
        with CenterMiddle():
            with Vertical(id="modal"):
                yield Static("▧  MCP Servers", id="modal_title")
                yield Static("", id="mcp_header")
                yield Input(placeholder="filter…  (press / to focus)", id="mcp_filter")
                yield OptionList(id="mcp_list")
                yield Static("", id="mcp_status")
                yield Static(
                    f"[{ui.ACCENT_3}]space[/] toggle   [{ui.ACCENT_3}]g[/] global   "
                    f"[{ui.ACCENT_3}]i[/] import   [{ui.ACCENT_3}]r[/] refresh   "
                    f"[{ui.ACCENT_3}]d[/] delete   [{ui.ACCENT_3}]esc[/] close",
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

    def on_unmount(self) -> None:
        disable_mouse()
        if self._prev_scroll is not None:
            try:
                self.app.scroll_sensitivity_y = self._prev_scroll
            except AttributeError:
                pass

    # ── helpers ──────────────────────────────────────────────────────────

    def _set_status(self, msg: str, *, ok: bool | None = None) -> None:
        widget = self.query_one("#mcp_status", Static)
        widget.update(Text(msg))
        widget.set_class(ok is True, "ok")
        widget.set_class(ok is False, "err")

    def _refresh_rows(self, keep_highlight: bool = True) -> None:
        """Re-read config and re-render the option list."""
        config = get_config()
        servers = config.list_servers()
        auto_connect = set(config.get_auto_connect())

        # Header line
        scope_text = (
            Text.assemble(
                ("scope: ", "dim"),
                ("◉ global on  ", "bold blue"),
                (f"{len(servers)} servers · ", "dim"),
                (f"{len(auto_connect)} auto · ", "dim"),
                (f"{mcp_registry.tool_count()} tools live", "green"),
            )
            if config.include_global()
            else Text.assemble(
                ("scope: ", "dim"),
                ("▣ project-only  ", "bold magenta"),
                (f"{len(servers)} servers · ", "dim"),
                (f"{mcp_registry.tool_count()} tools live", "green"),
                ("  ", ""),
                ("(press g to load Claude Code / OpenCode / Cursor / …)", "dim"),
            )
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
            opts.add_option(
                Option(_row_label(name, cfg, scope, source, name in auto_connect), id=row_id)
            )

        if prev_idx is not None and prev_idx < len(self._row_ids):
            opts.highlighted = prev_idx
        elif self._row_ids:
            opts.highlighted = 0

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
        if not name:
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
            # Run the (potentially slow) connect off the UI thread.
            self._set_status(f"connecting {name}…")

            def _do_connect() -> None:
                err = mcp_registry.connect(name, cfg)
                self.app.call_from_thread(self._after_connect, name, err)

            threading.Thread(target=_do_connect, daemon=True).start()
            return
        self._refresh_rows()

    def _after_connect(self, name: str, err: str | None) -> None:
        from ..mcp.scope import invalidate_mcp_prompt_cache

        if err:
            self._set_status(f"connect failed: {err}", ok=False)
        else:
            tools = len(mcp_registry.get_server_tools(name))
            self._set_status(f"connected {name} — {tools} tools", ok=True)
        invalidate_mcp_prompt_cache()
        self._refresh_rows()

    def action_toggle_global(self) -> None:
        state.global_mcp = not state.global_mcp
        state.save_mcp_config()

        from ..mcp.scope import apply_mcp_scope_change

        enabling = state.global_mcp
        self._set_status(
            "global scope ON — connecting servers…" if enabling
            else "global scope OFF — project servers only",
            ok=True,
        )

        def _reconcile() -> None:
            apply_mcp_scope_change(connect_all=enabling)
            self.app.call_from_thread(self._refresh_rows)

        threading.Thread(target=_reconcile, daemon=True).start()
        self._refresh_rows()

    def action_import(self) -> None:
        def after(result: dict | None) -> None:
            if not result:
                return
            added = result.get("added", [])
            reload_config()
            self._refresh_rows()
            self._set_status(
                f"imported {len(added)}: {', '.join(added)}", ok=True
            )

        self.app.push_screen(JSONImportScreen(), after)

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
