"""The MCP dialog — your servers and the marketplace, in one searchable list.

Type to search 60+ well-known servers (Slack, Notion, Linear, GitHub…), pick
one and press Enter: hosted ones open their sign-in page, key / app ones ask
for exactly what they need (with a link to get it), local ones just start.
Your own servers sit on top with their health.

* type          search (servers + marketplace); paste a link / npx … to add it
* ↑/↓ · Enter   move · connect / disconnect / sign in / set up
* space · t     switch the highlighted server on / off (or click its ON / OFF pill)
* T             MCP as a whole on / off (or click the switch in the header)
* ←/→           marketplace category (while the list has focus)
* a             add a custom server (link, npx …, claude mcp add …, JSON, GitHub)
* o             sign out (forget the saved login)      k  enter the keys a server needs
* p             a server's OAuth app (Client ID / Secret) — for hosts without self sign-up
* m             move between project ↔ global          d  remove (press twice)
* g             toggle global scope    i / e  import / export    r  re-scan configs
* /             focus search    Esc  close
"""

from __future__ import annotations

import re
import threading

from textual import work
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import CenterMiddle, Horizontal, Vertical
from textual.message import Message
from textual.timer import Timer
from textual.widget import Widget
from textual.widgets import Button, Input, OptionList, Static
from textual.widgets.option_list import Option

from rich.text import Text

from .modal_chrome import TUI_MODAL_CHROME_CSS, TuiModalScreen, hint_line, picker_row, section_header
from .mouse_toggle import enable_mouse, disable_mouse
from . import theme as ui
from .. import state
from ..mcp import catalog as mcp_catalog
from ..mcp.config import (
    MCP_PROJECT_CONFIG_FILENAME,
    get_config,
    import_server_to_project,
    export_server_to_global,
    reload_config,
)
from ..mcp.registry import mcp_registry, needs_auth
from ..mcp import toggle as mcp_toggle
from ..mcp.sources import SOURCE_ICONS as _SOURCE_ICONS, format_endpoint as _endpoint_text
from ..utils.origins import tool_tag


# Marketplace categories as the chip row names them (short enough for 100 columns).
_CHIP_LABELS = {
    "Communication": "Chat",
    "Developer tools": "Dev tools",
    "Cloud & data": "Cloud & data",
    "Search & web": "Web",
    "On this computer": "Local",
}
_ALL = "All"

_SOURCE_RE = re.compile(r"^(https?://|npx\s|uvx\s|docker\s|claude\s+mcp\s|\{|\./|/|~/|[A-Z_][A-Z0-9_]*=)")


def _looks_like_source(text: str) -> bool:
    """Pasted install text (an address, a command, JSON) rather than a search."""
    return bool(_SOURCE_RE.match((text or "").strip()))


# ── helpers ──────────────────────────────────────────────────────────────


def _tile(mono: str, color: str) -> tuple[str, str]:
    """A small colored logo tile: ``(text, style)`` for ``picker_row(icon=…)``."""
    mono = (mono or "?")[:2]
    bg = color if re.fullmatch(r"#[0-9a-fA-F]{6}", color or "") else ui.BG_4
    return f"{mono:^3}", f"bold #ffffff on {bg}"


_AUTH_BADGES = {
    "oauth": ("↗ Sign in", "ACCENT"),
    "open": ("● No account", "OK"),
    "key": ("⚿ API key", "WARN"),
    "app": ("◆ Your app", "ACCENT_2"),
    "desktop": ("◧ Desktop app", "ACCENT_2"),
    "local": ("▸ Runs locally", "FG_MUTE"),
}


def _badge(auth: str) -> tuple[str, str]:
    text, tok = _AUTH_BADGES.get(auth, ("", "FG_DIM"))
    return text, getattr(ui, tok, ui.FG_DIM)


_TAGS_W = 54  # switch · status · where it comes from — the same column on every server row


def _switch(on: bool, *, live: bool = True, meta: dict | None = None) -> Text:
    """An ON / OFF pill. ``live=False`` = on, but MCP as a whole is off.
    ``meta`` rides on the pill so a click on it can be told apart from the row."""
    from rich.style import Style

    if on and live:
        text, style = " ON  ", f"bold {ui.BG_0} on {ui.OK}"
    elif on:
        text, style = " ON  ", f"{ui.FG_MUTE} on {ui.BG_4}"
    else:
        text, style = " OFF ", f"{ui.FG_DIM} on {ui.BG_3}"
    t = Text(no_wrap=True)
    t.append(text, style=Style.parse(style) + Style(meta=meta) if meta else style)
    t.append(" ")
    return t


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
    also: list[str] | None = None,
    item: dict | None = None,
):
    health = health or mcp_registry.get_server_health(name, cfg, connecting=connecting)
    status = health.get("status", "idle")
    tool_count = health.get("tool_count", 0)
    enabled = mcp_toggle.server_enabled(name)
    mcp_on = mcp_toggle.mcp_enabled()

    if connecting or status == "connecting":
        dot, color, word = spinner, ui.ACCENT, "connecting…"
    elif status == "live":
        dot, color, word = "●", ui.OK, f"live · {tool_count} tools"
    elif status == "failed":
        dot, color, word = "✗", ui.ERR, "failed"
    elif status == "auth":
        dot, color, word = "◐", ui.WARN, "sign in"
    elif status == "off":
        dot, color, word = "○", ui.FG_DIM, ("off" if mcp_on else "MCP off")
    elif health.get("needs_credentials"):
        dot, color, word = "◈", ui.WARN, "needs keys"
    elif status == "warn":
        dot, color, word = "▲", ui.WARN, (f"check · {tool_count} tools" if health.get("connected") else "check")
    else:
        dot, color, word = "○", ui.FG_DIM, "idle"
    src_icon = _SOURCE_ICONS.get(source, "•")
    transport = cfg.get("type", "stdio")
    # Where it comes from (claude, cursor, codex …), "+2" when other tools define it too.
    meta = f"{src_icon} {tool_tag(source, also or ())} · {transport}" + (" · auto" if is_auto else "")
    if item:
        icon, icon_style = _tile(item.get("monogram") or mcp_catalog.monogram(item.get("label", name)),
                                 item.get("color", ""))
        detail = item.get("desc", "")
    else:
        icon, icon_style = _tile(mcp_catalog.monogram(name), "")
        detail = _endpoint_text(cfg)
    off = status == "off"
    right = _switch(enabled, live=mcp_on, meta={"mcp_switch": name})
    right.append(f"{dot} {word}", style=color if status != "idle" or connecting else ui.FG_DIM)
    right.append(f"   {meta}", style=ui.FG_DIM)
    if off:
        icon_style = f"{ui.FG_DIM} on {ui.BG_3}"
    return picker_row(
        f" {name}",
        detail=detail,
        right="",
        icon=icon,
        icon_style=icon_style,
        title_style=ui.FG_DIM if off else f"bold {ui.FG}",
        detail_style=ui.FG_DIM,
        title_width=20,
        tags=right,
        tags_width=_TAGS_W,  # one width for every row, so the switches line up
    )


def _market_row(item: dict, *, query: str = "", busy: bool = False, spinner: str = "⠋"):
    icon, icon_style = _tile(item.get("monogram", ""), item.get("color", ""))
    if busy:
        text, color = f"{spinner} connecting…", ui.ACCENT
    else:
        text, color = _badge(item.get("auth", ""))
    tags = Text(text, style=color, no_wrap=True)
    return picker_row(
        f" {item['label']}",
        detail=item.get("desc", ""),
        query=query,
        icon=icon,
        icon_style=icon_style,
        title_style=f"bold {ui.FG}",
        title_width=20,
        tags=tags,
        tags_width=16,
    )


class CategoryBar(Widget):
    """``All  Popular  Chat  Productivity …`` — click a chip, or ←/→ from the list."""

    DEFAULT_CSS = """
    CategoryBar { height: auto; padding: 0 1; margin-bottom: 1; }
    """

    class Changed(Message):
        def __init__(self, value: str) -> None:
            super().__init__()
            self.value = value

    def __init__(self, categories: list[str], value: str = _ALL, *, id: str | None = None) -> None:
        super().__init__(id=id)
        self.categories = [_ALL, *categories]
        self.value = value
        self._spans: list[tuple[int, int, int, str]] = []

    def _layout(self, width: int) -> list[list[tuple[int, str, str]]]:
        """Chips per line — a chip never breaks across lines."""
        lines: list[list[tuple[int, str, str]]] = [[]]
        x = 0
        for cat in self.categories:
            label = f" {_CHIP_LABELS.get(cat, cat)} "
            gap = 2 if lines[-1] else 0
            if lines[-1] and width and x + gap + len(label) > width:
                lines.append([])
                x, gap = 0, 0
            lines[-1].append((x + gap, label, cat))
            x += gap + len(label)
        return lines

    def get_content_height(self, container, viewport, width: int) -> int:
        return len(self._layout(width))

    def render(self) -> Text:
        t = Text(no_wrap=True)
        self._spans = []
        for row, line in enumerate(self._layout(self.content_size.width)):
            if row:
                t.append("\n")
            x = 0
            for start, label, cat in line:
                t.append(" " * (start - x))
                if cat == self.value:
                    t.append(label, style=f"bold {ui.ACCENT} reverse")
                else:
                    t.append(label, style=ui.FG_MUTE)
                self._spans.append((row, start, start + len(label), cat))
                x = start + len(label)
        return t

    def set_value(self, value: str, *, notify: bool = True) -> None:
        if value not in self.categories or value == self.value:
            return
        self.value = value
        self.refresh()
        if notify:
            self.post_message(self.Changed(value))

    def step(self, delta: int) -> None:
        i = self.categories.index(self.value) if self.value in self.categories else 0
        self.set_value(self.categories[(i + delta) % len(self.categories)])

    def on_click(self, event) -> None:
        off = event.get_content_offset(self)
        if off is None:
            return
        for row, start, end, cat in self._spans:
            if row == off.y and start <= off.x < end:
                self.set_value(cat)
                return


class McpHeader(Static):
    """The scope / counts line — its ``MCP ON`` pill switches MCP as a whole."""

    class MasterClicked(Message):
        pass

    def on_click(self, event) -> None:
        if event.style.meta.get("mcp_master"):
            event.stop()
            self.post_message(self.MasterClicked())


class McpList(OptionList):
    """An ``OptionList`` that says whether a row was picked by mouse or keyboard,
    so a click can connect a marketplace server but never disconnects a live one."""

    class Activated(Message):
        def __init__(self, index: int, by_mouse: bool) -> None:
            super().__init__()
            self.index = index
            self.by_mouse = by_mouse

    class SwitchClicked(Message):
        """The ON / OFF pill of a server row was clicked."""

        def __init__(self, name: str) -> None:
            super().__init__()
            self.name = name

    async def _on_click(self, event) -> None:
        event.prevent_default()  # OptionList's own handler would select it as if by keyboard
        clicked = event.style.meta.get("option")
        if clicked is not None and not self._options[clicked].disabled:
            self.highlighted = clicked
            switch = event.style.meta.get("mcp_switch")
            if switch:
                self.post_message(self.SwitchClicked(str(switch)))
                return
            self.post_message(self.Activated(clicked, True))

    def action_select(self) -> None:
        idx = self.highlighted
        if idx is not None and not self._options[idx].disabled:
            self.post_message(self.Activated(idx, False))


# ── main MCP modal ───────────────────────────────────────────────────────

class MCPModalScreen(TuiModalScreen[None]):
    """Single modal for everything MCP — your servers, the marketplace, every action."""

    DEFAULT_CSS = (
        TUI_MODAL_CHROME_CSS
        + """
    MCPModalScreen #modal {
        width: 92%;
        max-width: 150;
        height: 90%;
        max-height: 52;
    }
    MCPModalScreen McpList {
        height: 1fr;
        min-height: 8;
    }
    MCPModalScreen #mcp_header {
        padding: 0 1;
        margin-bottom: 1;
        color: {ui.FG_MUTE};
        height: 1;
    }
    MCPModalScreen #mcp_status {
        padding: 0 1;
        color: {ui.FG_MUTE};
        height: auto;
    }
    MCPModalScreen #mcp_status.ok { color: {ui.OK}; }
    MCPModalScreen #mcp_status.err { color: {ui.ERR}; }
    MCPModalScreen #mcp_status.connecting { color: {ui.WARN}; }
    MCPModalScreen #mcp_health {
        padding: 1 1 0 1;
        margin-top: 1;
        color: {ui.FG_MUTE};
        height: auto;
        min-height: 3;
        border-top: solid {ui.BORDER};
    }
    MCPModalScreen #mcp_filter { margin-bottom: 1; }
    MCPModalScreen #mcp_buttons { height: 1; margin-top: 1; }
    MCPModalScreen #mcp_buttons Button { margin: 0 1 0 0; }
    """
    )

    BINDINGS = [
        Binding("escape", "cancel",   "Close",  show=True),
        Binding("space",  "switch",   "On/off", show=True),
        Binding("t",      "switch",   "On/off", show=False),
        Binding("T",      "switch_all", "All MCP", show=True),
        Binding("g",      "toggle_global", "Global", show=True),
        Binding("i",      "import_global", "Import", show=True),
        Binding("e",      "export_global", "Export", show=True),
        Binding("a",      "manual_add",    "Add",    show=True),
        Binding("r",      "refresh",  "Refresh", show=True),
        Binding("d",      "delete",   "Delete", show=True),
        Binding("o",      "sign_out", "Sign out", show=False),
        Binding("m",      "move",     "Move",   show=False),
        Binding("k",      "keys",     "Keys",   show=False),
        Binding("p",      "oauth_app", "OAuth app", show=False),
        Binding("c",      "copy_link", "Copy link", show=False),
        Binding("slash",  "focus_filter", "Search", show=False),
        Binding("left",   "category(-1)", show=False),
        Binding("right",  "category(1)", show=False),
        Binding("down",   "cursor_down",  show=False),
        Binding("up",     "cursor_up",    show=False),
        Binding("pagedown", "page_down",  show=False),
        Binding("pageup",   "page_up",    show=False),
    ]

    def __init__(self, *, add: bool = False, query: str = "") -> None:
        super().__init__()
        self._open_add = add
        self._armed_delete: str | None = None
        self._filter: str = query
        self._category: str = _ALL
        self._row_ids: list[str] = []   # parallel to OptionList rows: srv::name · cat::id · add::… · ""
        self._connecting_names: set[str] = set()
        self._adding: set[str] = set()  # marketplace ids being added / connected
        self._connect_status_msg: str = ""
        self._scope_busy: bool = False
        self._spinner_i: int = 0
        self._spinner_timer: Timer | None = None
        self._market: dict[str, dict] = {}

    def compose(self) -> ComposeResult:
        with CenterMiddle():
            with Vertical(id="modal"):
                yield Static("▧  MCP servers & marketplace", id="modal_title")
                yield McpHeader("", id="mcp_header")
                yield Input(
                    value=self._filter,
                    placeholder=f"Search {len(mcp_catalog.CATALOG)}+ servers — Slack, Notion, GitHub…  or paste a link / npx command",
                    id="mcp_filter",
                )
                yield CategoryBar(mcp_catalog.CATEGORIES, id="mcp_cats")
                yield McpList(id="mcp_list")
                yield Static("", id="mcp_health")
                yield Static("", id="mcp_status")
                with Horizontal(id="mcp_buttons"):
                    yield Button("Connect", id="mcp_primary", variant="primary", compact=True)
                    yield Button("Turn off", id="mcp_switch", compact=True)
                    yield Button("Keys…", id="mcp_keys", compact=True)
                    yield Button("OAuth app…", id="mcp_app", compact=True)
                    yield Button("+ Custom server", id="mcp_add", compact=True)
                yield Static(
                    hint_line(("↵", "connect"), ("space", "on/off"), ("T", "all MCP"), ("↑↓", "move"),
                              ("←→", "category"), ("/", "search"), ("a", "custom"), ("d", "remove"),
                              ("g", "global"), ("esc", "close")),
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
        self._refresh_rows(keep_highlight=False)
        if self._open_add or self._filter or not get_config().list_servers():
            self.query_one("#mcp_filter", Input).focus()
            if self._open_add and not self._filter:
                self._focus_first_market_row()
        else:
            self.query_one("#mcp_list", McpList).focus()
        self._refresh_health_detail()
        subscribe = getattr(self.app, "mcp_subscribe", None)
        if subscribe:
            subscribe(self._registry_changed)

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

    def on_mcp_list_activated(self, event: McpList.Activated) -> None:
        event.stop()
        kind, key = self._selected_row()
        if kind == "srv" and event.by_mouse:
            return  # a click only selects a server of yours; Enter / the button acts
        self._activate()

    def on_category_bar_changed(self, event: CategoryBar.Changed) -> None:
        self._category = event.value
        self._refresh_rows(keep_highlight=False)
        self._focus_first_market_row()

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
        if not self._connecting_names and not self._scope_busy and not self._adding:
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
            "off": ui.FG_DIM,
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
        if counts.get("off"):
            parts.append((f"{counts['off']} off", "dim"))
        if not parts:
            return Text("")
        segs: list[tuple[str, str]] = [(" · ", "dim")]
        for i, (label, style) in enumerate(parts):
            if i:
                segs.append((" · ", "dim"))
            segs.append((label, style))
        return Text.assemble(*segs)

    def _global_hint(self) -> str:
        """What `g` would add, read once per dialog (it parses every tool's config)."""
        if getattr(self, "_global_hint_text", None) is None:
            try:
                from ..mcp.config import global_source_summary

                count, tools = global_source_summary()
            except Exception:
                count, tools = 0, []
            if count:
                self._global_hint_text = (
                    f"(g: also load {count} server{'s' if count != 1 else ''} from {' · '.join(tools)})"
                )
            else:
                self._global_hint_text = "(g: also load Claude / Cursor / Codex / …)"
        return self._global_hint_text

    def _refresh_health_detail(self) -> None:
        try:
            widget = self.query_one("#mcp_health", Static)
        except Exception:
            return
        kind, key = self._selected_row()
        if kind == "cat":
            item = self._market.get(key) or {}
            widget.update(self._market_detail(item))
            self._sync_buttons(None, item=item)
            return
        if kind == "add":
            text = Text()
            if key == "custom":
                text.append("Add any MCP server", style=f"bold {ui.FG}")
                text.append("\nPaste a link (https://…/mcp), an install command (npx -y … / uvx …), "
                            "a `claude mcp add …` line, a JSON config or a GitHub repo — Jarvis works out the rest.",
                            style=ui.FG_MUTE)
            else:
                text.append("Add this as a custom server", style=f"bold {ui.FG}")
                text.append(f"\n{self._filter.strip()[:200]}", style=ui.ACCENT)
                text.append("\n↵ shows what it will run or connect to before anything is added.", style=ui.FG_MUTE)
            widget.update(text)
            self._sync_buttons(None, add=True)
            return
        name = key if kind == "srv" else None
        if not name:
            widget.update(Text("No match. Try another word — or paste a link / npx command to add your own.",
                               style=ui.FG_DIM) if self._filter else "")
            self._sync_buttons(None)
            return
        config = get_config()
        cfg = config.get_server(name)
        health = mcp_registry.get_server_health(
            name, cfg, connecting=name in self._connecting_names
        )
        item = mcp_catalog.for_server(name, cfg or {})
        detail = health.get("detail") or health.get("summary", "")
        hint = ""
        if health["status"] == "off":
            hint = (" — space turns it back on" if mcp_toggle.mcp_enabled()
                    else " — press T (or Enter) to turn MCP back on")
        elif health["status"] == "auth":
            hint = " — press Enter to sign in"
        elif health.get("needs_credentials"):
            hint = " — press Enter (or k) to add " + ", ".join(self._field_labels(name, health["needs_credentials"]))
        elif health["status"] == "failed":
            hint = " — Enter to retry"
            if "registered apps" in (health.get("last_connect_error") or ""):
                hint = " — press p to add its OAuth app (Client ID / Secret)"
        elif health["status"] == "warn" and health.get("hints"):
            hint = " — fix hints then Enter to connect"
        text = Text()
        text.append(name, style=f"bold {ui.FG}")
        bits = [b for b in ((item or {}).get("label", ""), config.get_scope(name) or "", _endpoint_text(cfg or {}, max_len=90)) if b]
        text.append("  " + " · ".join(bits), style=ui.FG_DIM)
        text.append("\n" + detail + hint, style=self._health_color(health["status"]))
        widget.update(text)
        self._sync_buttons(name, health)

    def _field_labels(self, name: str, variables: list[str]) -> list[str]:
        item = mcp_catalog.for_server(name, get_config().get_server(name) or {})
        fields = (item or {}).get("fields") or {}
        return [fields.get(v, {}).get("label", v) for v in variables]

    def _market_detail(self, item: dict) -> Text:
        if not item:
            return Text("")
        t = Text()
        t.append(item["label"], style=f"bold {ui.FG}")
        t.append(f"  {item.get('category', '')} · {item.get('endpoint', '')}", style=ui.FG_DIM)
        auth = item.get("auth")
        labels = [f.get("label", v) for v, f in (item.get("fields") or {}).items()]
        if item.get("id") in self._adding:
            msg = f"Adding {item['label']}…"
        elif auth == "oauth":
            msg = f"Hosted. ↵ adds it and opens {item['label']}'s sign-in page in your browser — nothing to paste."
        elif auth == "open":
            msg = "Hosted, no account needed. ↵ connects right away."
        elif auth == "key":
            msg = (f"Needs your {' and '.join(labels) or 'API key'}. ↵ asks for it, with a link to get one "
                   "(kept private, never in a config file).")
        elif auth in ("app", "desktop"):
            msg = (item.get("setup") or {}).get("why", "") + " ↵ walks you through it."
        else:
            msg = f"Runs on this computer: {item.get('endpoint', '')}. ↵ adds and starts it."
        t.append("\n" + msg, style=ui.FG_MUTE)
        return t

    def _visible_market(self, installed_ids: set[str]) -> list[dict]:
        q = "" if _looks_like_source(self._filter) else self._filter
        cat = "" if self._category == _ALL else self._category
        rows = mcp_catalog.search(q, cat)
        return [mcp_catalog.public_item(r) for r in rows if r["id"] not in installed_ids]

    def _refresh_rows(self, keep_highlight: bool = True) -> None:
        """Re-read config and re-render the list: your servers, then the marketplace."""
        config = get_config()
        servers = config.list_servers()
        auto_connect = set(config.get_auto_connect())
        spinner = self._spinner_char()

        # Header line — the MCP master switch first, then scope and counts
        mcp_on = mcp_toggle.mcp_enabled()
        master = Text.assemble(("MCP ", f"bold {ui.FG}"),
                               _switch(mcp_on, meta={"mcp_master": True}), ("  ", ""))
        connecting_count = len(self._connecting_names)
        scope_bits = (
            ("◉ global on", f"bold {ui.ACCENT}") if config.include_global() else ("▣ project only", f"bold {ui.ACCENT_2}")
        )
        if not mcp_on:
            scope_text = Text.assemble(
                master,
                ("all servers off — nothing connects, Jarvis gets no MCP tools", ui.WARN),
                ("   T or click to turn on", ui.FG_DIM),
            )
        elif connecting_count:
            scope_text = Text.assemble(
                master,
                ("scope ", ui.FG_DIM), scope_bits, ("   ", ""),
                (f"{spinner} connecting {connecting_count} server", f"bold {ui.WARN}"),
                ("s…" if connecting_count != 1 else "…", f"bold {ui.WARN}"),
            )
        else:
            scope_text = Text.assemble(
                master,
                ("scope ", ui.FG_DIM), scope_bits, ("   ", ""),
                (f"{len(servers)} server{'s' if len(servers) != 1 else ''}", ui.FG_MUTE),
                self._header_health_bits(sorted(servers.keys())),
                ("" if config.include_global() else "   " + self._global_hint(), ui.FG_DIM),
            )
        self.query_one("#mcp_header", Static).update(scope_text)

        q = self._filter.strip()
        looks_source = _looks_like_source(q)
        names_sorted = sorted(servers.keys())
        items = {n: mcp_catalog.for_server(n, servers[n] or {}) for n in names_sorted}
        if q and not looks_source:
            ql = q.lower()
            names_sorted = [
                n for n in names_sorted
                if ql in n.lower()
                or ql in str(servers[n].get("command", "")).lower()
                or ql in str(servers[n].get("url", "")).lower()
                or (items[n] is not None and mcp_catalog.matches(items[n], q))
            ]
        installed_ids = {it["id"] for it in items.values() if it}
        market = self._visible_market(installed_ids)
        self._market = {m["id"]: m for m in market}

        opts = self.query_one("#mcp_list", McpList)
        prev_key = self._row_ids[opts.highlighted] if (keep_highlight and opts.highlighted is not None
                                                        and opts.highlighted < len(self._row_ids)) else None
        prev_idx = opts.highlighted if keep_highlight else None
        options: list[Option] = []
        ids: list[str] = []

        def add(opt: Option, rid: str) -> None:
            options.append(opt)
            ids.append(rid)

        if looks_source:
            add(Option(picker_row(f"Add “{q[:70]}{'…' if len(q) > 70 else ''}”", detail="as a custom server",
                                  icon="  + ", icon_style=f"bold {ui.ACCENT} reverse", title_style=f"bold {ui.ACCENT}"),
                       id="add::source"), "add::source")

        if names_sorted:
            counts = mcp_registry.health_counts(names_sorted, connecting=self._connecting_names)
            note = f"{len(names_sorted)}" + (f" · {counts['live']} live" if counts.get("live") else "")
            add(section_header("Your servers", note, first=not options), "")
            for name in names_sorted:
                cfg = servers[name]
                scope = config.get_scope(name) or "global"
                source = config.get_source(name) or scope
                is_connecting = name in self._connecting_names
                health = mcp_registry.get_server_health(name, cfg, connecting=is_connecting)
                add(Option(_row_label(name, cfg, scope, source, name in auto_connect, connecting=is_connecting,
                                      spinner=spinner, health=health, also=config.get_also(name), item=items[name]),
                           id=f"srv::{name}"), f"srv::{name}")

        if not looks_source:
            cat_note = "" if self._category == _ALL else f" · {self._category}"
            add(section_header("Marketplace", f"{len(market)} server{'s' if len(market) != 1 else ''}{cat_note}"
                               + ("" if q else "  ↵ to connect"), first=not options), "")
            for item in market:
                add(Option(_market_row(item, query=q, busy=item["id"] in self._adding, spinner=spinner),
                           id=f"cat::{item['id']}"), f"cat::{item['id']}")
            if not market:
                add(Option(Text(f"  no marketplace match for “{q}”" if q else "  nothing in this category",
                                style=f"italic {ui.FG_DIM}"), disabled=True), "")
            add(Option(picker_row("Custom server…", detail="paste a link, npx / uvx command, JSON or GitHub repo",
                                  icon="  + ", icon_style=f"bold {ui.FG_MUTE} reverse", title_style=f"bold {ui.FG_MUTE}"),
                       id="add::custom"), "add::custom")

        opts.clear_options()
        opts.add_options(options)
        self._row_ids = ids

        target = None
        if prev_key and prev_key in ids:
            target = ids.index(prev_key)
        elif prev_idx is not None and prev_idx < len(ids) and ids[prev_idx]:
            target = prev_idx
        if target is None:
            target = next((i for i, rid in enumerate(ids) if rid), None)
        if target is not None:
            opts.highlighted = target
        self._refresh_health_detail()

    def _focus_first_market_row(self) -> None:
        opts = self.query_one("#mcp_list", McpList)
        for i, rid in enumerate(self._row_ids):
            if rid.startswith("cat::"):
                opts.highlighted = i
                opts.scroll_to_highlight()
                break
        self._refresh_health_detail()

    def _selected_row(self) -> tuple[str, str]:
        """``(kind, key)`` of the highlighted row — kind is srv | cat | add | ""."""
        try:
            opts = self.query_one("#mcp_list", McpList)
        except Exception:
            return "", ""
        idx = opts.highlighted
        if idx is None or idx >= len(self._row_ids) or not self._row_ids[idx]:
            return "", ""
        kind, _, key = self._row_ids[idx].partition("::")
        return kind, key

    def _selected_name(self) -> str | None:
        kind, key = self._selected_row()
        return key if kind == "srv" else None

    # ── actions ──────────────────────────────────────────────────────────

    def action_cancel(self) -> None:
        self.dismiss(None)

    def action_cursor_down(self) -> None:
        self.query_one("#mcp_list", McpList).action_cursor_down()

    def action_cursor_up(self) -> None:
        self.query_one("#mcp_list", McpList).action_cursor_up()

    def action_page_down(self) -> None:
        self.query_one("#mcp_list", McpList).action_page_down()

    def action_page_up(self) -> None:
        self.query_one("#mcp_list", McpList).action_page_up()

    def action_focus_filter(self) -> None:
        self.query_one("#mcp_filter", Input).focus()

    def action_category(self, delta: int) -> None:
        self.query_one("#mcp_cats", CategoryBar).step(int(delta))

    def action_refresh(self) -> None:
        from ..mcp.scope import apply_mcp_scope_change
        apply_mcp_scope_change()
        self._refresh_rows()
        self._set_status("config reloaded", ok=True)

    def _activate(self) -> None:
        """Enter / click / the primary button on whatever row is highlighted."""
        kind, key = self._selected_row()
        if kind == "cat":
            self._connect_market(key)
        elif kind == "add":
            self.action_manual_add(self._filter.strip() if key == "source" else "")
        elif kind == "srv":
            if not mcp_toggle.mcp_enabled():
                self.action_switch_all()
                return
            if not mcp_toggle.server_enabled(key):
                self.action_switch()
                return
            health = mcp_registry.get_server_health(key, get_config().get_server(key))
            if health.get("needs_credentials") and not mcp_registry.is_connected(key):
                self.action_keys()
            else:
                self.action_toggle()

    def _sync_buttons(self, name: str | None, health: dict | None = None, *, item: dict | None = None,
                      add: bool = False) -> None:
        try:
            primary = self.query_one("#mcp_primary", Button)
            keys = self.query_one("#mcp_keys", Button)
            app_btn = self.query_one("#mcp_app", Button)
            switch_btn = self.query_one("#mcp_switch", Button)
        except Exception:
            return

        def set_label(label: str) -> None:
            primary.display = True
            if str(primary.label) != label:
                primary.label = label
                # A Button keeps its old width when only the label changes.
                primary.styles.width = len(label) + 2
                primary.refresh(layout=True)

        keys.display = False
        app_btn.display = False
        switch_btn.display = False
        if item:
            set_label({"app": "Set up", "desktop": "Set up", "key": "Connect…"}.get(item.get("auth", ""), "Connect"))
            return
        if add:
            set_label("Add…")
            return
        if not name:
            primary.display = False
            return
        health = health or mcp_registry.get_server_health(name, get_config().get_server(name))
        status = health.get("status")
        if not mcp_toggle.mcp_enabled():
            set_label("Turn MCP on")
            return
        if not mcp_toggle.server_enabled(name):
            set_label("Turn on")
            return
        switch_btn.display = True
        set_label(
            "Authenticate" if status == "auth"
            else "Disconnect" if health.get("connected")
            else "Add keys" if health.get("needs_credentials")
            else "Connect"
        )
        keys.display = bool(health.get("needs_credentials"))
        cfg = get_config().get_server(name) or {}
        app_btn.display = bool(cfg.get("url")) and (
            isinstance(cfg.get("oauth"), dict) or "registered apps" in (health.get("last_connect_error") or "")
        )

    def on_button_pressed(self, event: Button.Pressed) -> None:
        event.stop()
        bid = event.button.id
        if bid == "mcp_primary":
            self._activate()
        elif bid == "mcp_add":
            self.action_manual_add()
        elif bid == "mcp_switch":
            self.action_switch()
        elif bid == "mcp_keys":
            self.action_keys()
        elif bid == "mcp_app":
            self.action_oauth_app()

    # ── marketplace ──────────────────────────────────────────────────────

    def _connect_market(self, item_id: str) -> None:
        if item_id in self._adding:
            return
        item = self._market.get(item_id) or mcp_catalog.public_item(mcp_catalog.lookup(item_id) or {})
        if not item.get("id"):
            return
        from ..mcp import secrets as mcp_secrets

        missing = [v for v in item.get("credentials") or [] if not mcp_secrets.get_secret(v)]
        if missing or item.get("auth") == "desktop":
            from .extension_modals import ConnectSetupScreen

            def after(values: dict | None) -> None:
                if values is not None:
                    self._start_market_add(item, values)

            self.app.push_screen(ConnectSetupScreen(item, missing), after)
            return
        self._start_market_add(item, {})

    def _start_market_add(self, item: dict, values: dict) -> None:
        self._adding.add(item["id"])
        self._connect_status_msg = f"adding {item['label']}…"
        self._start_connect_spinner()
        self._set_status(f"{self._spinner_char()} adding {item['label']}…", connecting=True)
        self._refresh_rows()
        self._market_worker(item["id"], values)

    @work(thread=True, group="mcp-market")
    def _market_worker(self, item_id: str, values: dict) -> None:
        from ..mcp.install import add_mcp

        try:
            res = add_mcp(item_id, credentials=values or None)
        except Exception as exc:  # noqa: BLE001
            res = {"ok": False, "error": str(exc), "servers": []}
        try:
            self.app.call_from_thread(self._market_done, item_id, res)
        except Exception:
            pass

    def _market_done(self, item_id: str, res: dict) -> None:
        from ..mcp.scope import invalidate_mcp_prompt_cache

        self._adding.discard(item_id)
        self._connect_status_msg = ""
        if not self._connecting_names and not self._scope_busy and not self._adding:
            self._stop_connect_spinner()
        reload_config()
        invalidate_mcp_prompt_cache()
        servers = res.get("servers") or []
        r = servers[0] if servers else {}
        name = r.get("name") or item_id
        st = r.get("status")
        label = (self._market.get(item_id) or {}).get("label") or name
        if self.is_mounted:
            # Follow the new server to the top section.
            self._filter = self.query_one("#mcp_filter", Input).value or ""
        self._refresh_rows(keep_highlight=False)
        self._highlight_server(name)
        where = f" ({r.get('scope')})" if r.get("scope") else ""
        if st == "connected":
            self._set_status(f"✓ {label} connected{where} — {r.get('tool_count', 0)} tools ready", ok=True)
        elif st == "auth_required":
            self._set_status(f"{label} added{where} — finish signing in in your browser", ok=None)
            self._sign_in(name)
        elif st == "needs_credentials":
            self._set_status(f"{label} added{where} — needs {', '.join(r.get('missing') or [])}", ok=None)
            self.action_keys()
        elif st in ("exists", "added"):
            self._set_status(r.get("message") or f"{label} is already added", ok=None)
        else:
            self._set_status(f"✗ {label}: {r.get('error') or res.get('error') or 'could not add'}", ok=False)

    def _highlight_server(self, name: str) -> None:
        rid = f"srv::{name}"
        if rid in self._row_ids:
            opts = self.query_one("#mcp_list", McpList)
            opts.highlighted = self._row_ids.index(rid)
            opts.scroll_to_highlight()
            self._refresh_health_detail()

    def action_oauth_app(self) -> None:
        name = self._selected_name()
        if not name:
            return
        cfg = get_config().get_server(name) or {}
        if not cfg.get("url"):
            self._set_status(f"{name} runs locally — it has no sign-in", ok=False)
            return
        from ..mcp.catalog import oauth_redirect_url
        from .extension_modals import ConnectSetupScreen

        item = {
            "id": name, "label": name, "auth": "app", "redirect_url": oauth_redirect_url(),
            "credentials": ["client_id", "client_secret"],
            "fields": {
                "client_id": {"label": "Client ID", "hint": "from the provider's developer settings", "secret": False},
                "client_secret": {"label": "Client Secret", "hint": "leave as-is if the app has none", "secret": True},
            },
            "setup": {
                "title": f"Sign in to {name} with your own OAuth app",
                "why": "This server doesn't let new apps sign up on their own. Register an OAuth app with its "
                       "provider, use the redirect URL below, then paste the app's Client ID and Secret.",
            },
        }

        def after(values: dict | None) -> None:
            if not values:
                return
            self._connecting_names.add(name)
            self._connect_status_msg = f"saving app and connecting {name}…"
            self._start_connect_spinner()
            self._refresh_rows()
            self._oauth_app_worker(name, values.get("client_id", ""), values.get("client_secret", ""))

        self.app.push_screen(ConnectSetupScreen(item), after)

    @work(thread=True)
    def _oauth_app_worker(self, name: str, client_id: str, client_secret: str) -> None:
        from ..mcp.install import set_oauth_app

        try:
            res = set_oauth_app(name, client_id, client_secret)
        except Exception as exc:  # noqa: BLE001
            res = {"ok": False, "status": "failed", "error": str(exc)}
        try:
            self.app.call_from_thread(self._keys_done, name, res)
        except Exception:
            pass

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
        from .extension_modals import ConnectSetupScreen, KeysScreen

        def after(values: dict | None) -> None:
            if not values:
                return
            self._connecting_names.add(name)
            self._connect_status_msg = f"saving keys and connecting {name}…"
            self._start_connect_spinner()
            self._refresh_rows()
            self._keys_worker(name, values)

        item = mcp_catalog.for_server(name, get_config().get_server(name) or {})
        if item:
            self.app.push_screen(ConnectSetupScreen(mcp_catalog.public_item(item), needed), after)
        else:
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
            self._set_status(f"✓ connected {name} — {res.get('tool_count', 0)} tools", ok=True)
        elif st == "auth_required":
            self._set_status(f"{name}: saved — finish signing in in your browser", ok=None)
            from ..mcp.scope import invalidate_mcp_prompt_cache
            invalidate_mcp_prompt_cache()
            self._refresh_rows()
            self._sign_in(name)
            return
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
        if not mcp_toggle.is_enabled(name):
            self._activate()  # turns it (or MCP) on first
            return
        config = get_config()
        if not mcp_registry.is_connected(name):
            health = mcp_registry.get_server_health(name, config.get_server(name))
            if health.get("status") == "auth":
                self._sign_in(name)
                return
        if not mcp_registry.is_connected(name) and mcp_registry.get_server_health(
            name, config.get_server(name)
        ).get("needs_credentials"):
            self.action_keys()
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

    # ── on / off ─────────────────────────────────────────────────────────

    def on_mcp_list_switch_clicked(self, event: McpList.SwitchClicked) -> None:
        event.stop()
        self._switch_server(event.name)

    def on_mcp_header_master_clicked(self, event: McpHeader.MasterClicked) -> None:
        event.stop()
        self.action_switch_all()

    def action_switch(self) -> None:
        """Space / t / the ON-OFF pill: switch the highlighted server on or off."""
        name = self._selected_name()
        if name:
            self._switch_server(name)

    def _switch_server(self, name: str) -> None:
        if name in self._connecting_names or self._scope_busy:
            return
        on = not mcp_toggle.server_enabled(name)
        if on and mcp_toggle.mcp_enabled():
            self._connecting_names.add(name)
            self._connect_status_msg = f"turning {name} on…"
            self._start_connect_spinner()
            self._set_status(f"{self._spinner_char()} turning {name} on…", connecting=True)
        self._switch_worker(name, on)
        self._refresh_rows()

    @work(thread=True, exclusive=False, group="mcp-switch")
    def _switch_worker(self, name: str, on: bool) -> None:
        from ..mcp.install import set_server_enabled

        try:
            res = set_server_enabled(name, on)
        except Exception as exc:  # never leave the row spinning
            res = {"ok": False, "error": str(exc)}
        self.app.call_from_thread(self._switch_done, name, on, res)

    def _switch_done(self, name: str, on: bool, res: dict) -> None:
        self._connecting_names.discard(name)
        if not self._connecting_names and not self._scope_busy and not self._adding:
            self._stop_connect_spinner()
            self._connect_status_msg = ""
        if not self.is_mounted:
            return
        st = res.get("status")
        ok = None if st in ("auth_required", "needs_credentials") else (
            bool(res.get("ok")) and st != "failed" and "couldn't" not in str(res.get("message", "")))
        self._set_status(res.get("message") or res.get("error", "could not change it"), ok=ok)
        self._refresh_rows()
        if st == "auth_required":
            self._sign_in(name)

    def action_switch_all(self) -> None:
        """T / the header pill: MCP as a whole on or off."""
        if self._scope_busy:
            return
        on = not mcp_toggle.mcp_enabled()
        self._scope_busy = True
        self._connect_status_msg = "turning MCP on…" if on else "turning MCP off…"
        self._start_connect_spinner()
        self._set_status(f"{self._spinner_char()} {self._connect_status_msg}", connecting=True)
        self._switch_all_worker(on)

    @work(thread=True, exclusive=True, group="mcp-switch-all")
    def _switch_all_worker(self, on: bool) -> None:
        from ..mcp.install import set_mcp_enabled

        try:
            res = set_mcp_enabled(on)
        except Exception as exc:
            res = {"ok": False, "message": f"couldn't switch MCP: {exc}"}
        self.app.call_from_thread(self._switch_all_done, res)

    def _switch_all_done(self, res: dict) -> None:
        self._scope_busy = False
        self._connect_status_msg = ""
        if not self._connecting_names and not self._adding:
            self._stop_connect_spinner()
        if not self.is_mounted:
            return
        self._set_status(res.get("message", ""), ok=bool(res.get("ok")) and not res.get("failed"))
        self._refresh_rows()

    def action_toggle_global(self) -> None:
        if self._scope_busy or self._connecting_names:
            return

        state.global_mcp = not state.global_mcp
        state.save_mcp_config()
        self._global_hint_text = None  # re-read: servers may have been added since

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

    def action_manual_add(self, initial: str = "") -> None:
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

        self.app.push_screen(McpAddScreen(initial), after)

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
            # Enter while typing acts on the highlighted row, like a command palette.
            if self._selected_row()[0]:
                self._activate()
            else:
                self.query_one("#mcp_list", McpList).focus()
