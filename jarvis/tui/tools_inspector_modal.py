"""Unified ^F modal — browse file reads and all tool output in one place."""
from __future__ import annotations

import time

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import CenterMiddle, Vertical
from textual.widgets import OptionList, RichLog, Static
from textual.widgets.option_list import Option

from rich.text import Text

from .. import state
from ..repl.tool_display import viewer_text
from ..repl.tool_output_backfill import build_inspector_entries
from .modal_chrome import TUI_MODAL_CHROME_CSS, TuiModalScreen
from .mouse_toggle import enable_mouse, disable_mouse
from . import theme as ui


def _status_glyph(status: str | None) -> str:
    return {
        "queued": "○",
        "running": "◐",
        "done": "✓",
        "error": "✗",
        "cancelled": "⊘",
    }.get(status or "", "·")


def _basename(path: str) -> str:
    p = (path or "").rstrip("/")
    if not p:
        return "?"
    return p.rsplit("/", 1)[-1] or p


def _entry_args(entry: dict) -> tuple[str, str]:
    """(title, one-line args) via the same formatter as transcript tool rows."""
    import json

    from . import tool_format as tf

    name = entry.get("name") or "tool"
    subtitle = (entry.get("subtitle") or "").strip()
    try:
        data = json.loads(subtitle) if subtitle.startswith("{") else None
    except Exception:
        data = None
    args = tf.tool_args(name, data) if isinstance(data, dict) else tf.clip(subtitle, 80)
    return tf.tool_title(name), args


def _label(entry: dict):
    from . import tool_format as tf
    from .modal_chrome import picker_row

    name = entry.get("name") or "tool"
    status = entry.get("status")
    content = entry.get("content") or ""
    title, args = _entry_args(entry)
    color = {
        "error": ui.ERR, "running": ui.ACCENT, "queued": ui.FG_DIM, "cancelled": ui.FG_DIM,
    }.get(status or "", ui.OK)
    if content.lstrip().startswith(("ERROR", "BLOCKED", "TIMEOUT")):
        color = ui.ERR
    bits = []
    if content:
        bits.append(f"{len(content):,} chars")
    if entry.get("ts"):
        bits.append(time.strftime("%H:%M", time.localtime(entry["ts"])))
    return picker_row(
        title,
        detail=args,
        right=" · ".join(bits),
        icon=tf.tool_icon(name),
        icon_style=f"bold {color}",
        title_width=10,
    )


class ToolsInspectorScreen(TuiModalScreen[None]):
    """One picker for parallel file reads and every recent tool result."""

    DEFAULT_CSS = (
        TUI_MODAL_CHROME_CSS
        + """
    ToolsInspectorScreen #modal {
        width: 92%;
        max-width: 140;
        max-height: 88%;
    }
    ToolsInspectorScreen #tools_inspector_list {
        height: auto;
        max-height: 14;
        min-height: 3;
        margin-top: 1;
    }
    ToolsInspectorScreen #tools_inspector_log {
        height: 1fr;
        min-height: 12;
        margin-top: 1;
        background: {ui.BG_2};
        border: none;
        padding: 0 1;
        scrollbar-size-vertical: 1;
    }
    """
    )

    BINDINGS = [
        Binding("escape", "dismiss_cancel", "Close", show=True),
        Binding("tab", "focus_output", "Scroll output", show=True),
        Binding("v", "toggle_verbose", "Trace", show=True),
        Binding("down", "cursor_down", show=False),
        Binding("up", "cursor_up", show=False),
        Binding("pagedown", "page_down", show=False),
        Binding("pageup", "page_up", show=False),
    ]

    def compose(self) -> ComposeResult:
        trace = "on" if state.show_internal else "off"
        with CenterMiddle():
            with Vertical(id="modal"):
                yield Static("⚒  Tool output", id="modal_title")
                yield Static("", id="modal_status")
                yield OptionList(id="tools_inspector_list")
                yield RichLog(
                    id="tools_inspector_log",
                    wrap=True,
                    highlight=False,
                    markup=False,
                    auto_scroll=False,
                )
                yield Static(
                    f"[bold {ui.FG_MUTE}]↑↓[/] pick   [bold {ui.FG_MUTE}]tab[/] scroll output   "
                    f"[bold {ui.FG_MUTE}]v[/] trace:{trace}   [bold {ui.FG_MUTE}]esc[/] close",
                    id="modal_hint",
                )

    def on_mount(self) -> None:
        enable_mouse()
        self._entries: list[dict] = []
        self._status_summary = ""
        self._populate()
        opts = self.query_one("#tools_inspector_list", OptionList)
        if opts.option_count:
            opts.highlighted = opts.option_count - 1
            self._show_index(opts.highlighted)
        opts.focus()

    def on_unmount(self) -> None:
        disable_mouse()

    def _populate(self) -> None:
        opts = self.query_one("#tools_inspector_list", OptionList)
        status = self.query_one("#modal_status", Static)
        opts.clear_options()
        self._entries = build_inspector_entries()
        if not self._entries:
            self._status_summary = "No tools yet — run a command, then press ^F again."
            status.update(self._status_line())
            return
        n_files = sum(1 for e in self._entries if e.get("section") == "file")
        n = len(self._entries)
        parts = [f"{n} tool call{'s' if n != 1 else ''}"]
        if n_files:
            parts.append(f"{n_files} file read{'s' if n_files != 1 else ''}")
        self._status_summary = " · ".join(parts)
        status.update(self._status_line())
        for entry in self._entries:
            opts.add_option(Option(_label(entry), id=str(entry.get("id") or "")))

    def _show_index(self, index: int | None) -> None:
        log = self.query_one("#tools_inspector_log", RichLog)
        if index is None or index < 0 or index >= len(self._entries):
            return
        entry = self._entries[index]
        log.clear()
        from . import tool_format as tf

        title, args = _entry_args(entry)
        header = Text()
        header.append(f"{tf.tool_icon(entry.get('name') or '')} ", style=f"bold {ui.ACCENT}")
        header.append(title, style=f"bold {ui.FG}")
        if args:
            header.append(f"  {args}", style=ui.FG_MUTE)
        header.append("\n")
        log.write(header)
        content = entry.get("content") or ""
        st = entry.get("status")
        if content:
            log.write(Text(viewer_text(content)))
        elif st == "running":
            log.write(Text("Still running…", style="dim italic"))
        elif st == "queued":
            log.write(Text("Queued…", style="dim italic"))
        else:
            log.write(Text("(no output captured)", style="dim"))

    def _status_line(self) -> str:
        trace = "on" if state.show_internal else "off"
        base = self._status_summary or ""
        if base:
            return f"{base} · trace {trace}"
        return f"trace {trace}"

    def action_toggle_verbose(self) -> None:
        """Same as ^T — rebuild transcript when idle."""
        self.app.action_toggle_internal()
        mode = "on" if state.show_internal else "off"
        hint = self.query_one("#modal_hint", Static)
        hint.update(
            f"[bold {ui.FG_MUTE}]↑↓[/] pick   [bold {ui.FG_MUTE}]tab[/] scroll output   "
            f"[bold {ui.FG_MUTE}]v[/] trace:{mode}   [bold {ui.FG_MUTE}]esc[/] close"
        )
        status = self.query_one("#modal_status", Static)
        if self.app._busy:
            status.update(f"{self._status_line()} · applies after turn")
        else:
            status.update(self._status_line())

    def on_option_list_option_highlighted(self, event: OptionList.OptionHighlighted) -> None:
        if event.option_list.id != "tools_inspector_list":
            return
        self._show_index(event.option_list.highlighted)

    def on_option_list_option_selected(self, event: OptionList.OptionSelected) -> None:
        if event.option_list.id != "tools_inspector_list":
            return
        self._show_index(event.option_list.highlighted)
        self.query_one("#tools_inspector_log", RichLog).focus()

    def action_focus_output(self) -> None:
        self.query_one("#tools_inspector_log", RichLog).focus()

    def action_dismiss_cancel(self) -> None:
        self.dismiss(None)

    def action_cursor_down(self) -> None:
        self.query_one("#tools_inspector_list", OptionList).action_cursor_down()

    def action_cursor_up(self) -> None:
        self.query_one("#tools_inspector_list", OptionList).action_cursor_up()

    def action_page_down(self) -> None:
        self.query_one("#tools_inspector_log", RichLog).scroll_page_down(animate=False)

    def action_page_up(self) -> None:
        self.query_one("#tools_inspector_log", RichLog).scroll_page_up(animate=False)
