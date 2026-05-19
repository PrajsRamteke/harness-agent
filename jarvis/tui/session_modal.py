"""Modal session picker — replaces the console.input-based /session flow."""
from __future__ import annotations

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import CenterMiddle, Vertical
from textual.widgets import OptionList, Static
from textual.widgets.option_list import Option

from rich.text import Text

from ..storage.sessions import db_list_sessions, db_delete_session, db_load_session
from ..utils.time_fmt import _fmt_ts
from ..repl.trim import estimate_session_tokens
from .. import state
from .modal_chrome import TUI_MODAL_CHROME_CSS, TuiModalScreen
from .mouse_toggle import enable_mouse, disable_mouse


class SessionPickerScreen(TuiModalScreen[int | None]):
    """Lists saved sessions. Returns the selected session id, or None if cancelled."""

    DEFAULT_CSS = (
        TUI_MODAL_CHROME_CSS
        + """
    SessionPickerScreen #modal {
        width: 85%;
        max-width: 130;
        max-height: 80%;
    }
    SessionPickerScreen OptionList {
        height: 20;
    }
    """
    )

    BINDINGS = [
        Binding("escape", "dismiss_cancel", "Cancel", show=True),
        Binding("d", "delete", "Delete", show=True),
        Binding("down", "cursor_down", show=False),
        Binding("up", "cursor_up", show=False),
        Binding("pagedown", "page_down", show=False),
        Binding("pageup", "page_up", show=False),
    ]

    PAGE_SIZE = 50

    def compose(self) -> ComposeResult:
        with CenterMiddle():
            with Vertical(id="modal"):
                yield Static("▤  Sessions", id="modal_title")
                yield OptionList(id="session_list")
                yield Static(
                    "[#f0b3ff]↑↓[/] navigate   [#f0b3ff]↵[/] resume   "
                    "[#f0b3ff]d[/] delete   [#f0b3ff]esc[/] cancel",
                    id="modal_hint",
                )

    def on_mount(self):
        enable_mouse()
        self._prev_scroll_y = self.app.scroll_sensitivity_y
        self.app.scroll_sensitivity_y = 1.0
        self._offset = 0
        self._has_more = True
        self._loaded_ids: set[int] = set()
        self._populate()

    def on_unmount(self):
        disable_mouse()
        try:
            self.app.scroll_sensitivity_y = self._prev_scroll_y
        except AttributeError:
            pass

    def _populate(self):
        opts = self.query_one("#session_list", OptionList)
        opts.clear_options()
        self._offset = 0
        self._loaded_ids.clear()
        self._has_more = True
        self._append_page()
        if opts.option_count == 0:
            opts.add_option(Option("(no saved sessions yet)", id="__none__"))
            opts.disabled = True
            return
        opts.disabled = False
        opts.highlighted = 0
        opts.focus()

    def _append_page(self):
        """Fetch the next page of sessions and append them to the OptionList."""
        opts = self.query_one("#session_list", OptionList)
        rows = db_list_sessions(limit=self.PAGE_SIZE, offset=self._offset)
        if not rows:
            self._has_more = False
            return
        for r in rows:
            sid = r["id"]
            if sid in self._loaded_ids:
                continue
            self._loaded_ids.add(sid)
            is_active = sid == state.current_session_id
            marker = "● " if is_active else "  "
            title = r["title"] or "(untitled)"
            label = Text.assemble(
                (marker, "bold #3fb950"),
                (f"#{sid:<5d}", "bold #79c0ff" if is_active else "#79c0ff"),
                ("  ", ""),
                (f"{title[:50]:<50s}", "#e6edf3"),
                ("  ", ""),
                (f"{r['msg_count']:>4d} msgs", "#8b949e"),
                ("   ", ""),
                (f"{(r['model'] or '-'):<24s}", "#bc8cff"),
                ("  ", ""),
                (_fmt_ts(r["updated_at"]), "#8b949e"),
            )
            opts.add_option(Option(label, id=str(sid)))
        self._offset += len(rows)
        if len(rows) < self.PAGE_SIZE:
            self._has_more = False

    def _on_option_list_option_highlighted(self, event: OptionList.OptionHighlighted) -> None:
        """Auto-load more sessions when approaching the bottom of the list."""
        if not self._has_more:
            return
        opts = self.query_one("#session_list", OptionList)
        total = opts.option_count
        if total == 0:
            return
        try:
            idx = int(event.option_index)
        except (TypeError, ValueError):
            return
        # Load more when within the last 5 items of the current page
        if idx >= total - 5:
            self._append_page()

    def _current_id(self) -> int | None:
        opts = self.query_one("#session_list", OptionList)
        if opts.option_count == 0 or opts.highlighted is None:
            return None
        opt = opts.get_option_at_index(opts.highlighted)
        if not opt.id or opt.id == "__none__":
            return None
        try:
            return int(opt.id)
        except ValueError:
            return None

    # ─── bindings ──────────────────────────────────────────────────────
    def action_dismiss_cancel(self):
        self.dismiss(None)

    def action_select(self):
        sid = self._current_id()
        self.dismiss(sid)

    def action_cursor_down(self):
        self.query_one("#session_list", OptionList).action_cursor_down()

    def action_cursor_up(self):
        self.query_one("#session_list", OptionList).action_cursor_up()

    def action_page_down(self):
        self.query_one("#session_list", OptionList).action_page_down()

    def action_page_up(self):
        self.query_one("#session_list", OptionList).action_page_up()

    def action_delete(self):
        sid = self._current_id()
        if sid is None:
            return
        opts = self.query_one("#session_list", OptionList)
        prev_idx = opts.highlighted
        if prev_idx is None:
            return
        db_delete_session(sid)
        self._loaded_ids.discard(sid)
        opts.remove_option_at_index(prev_idx)
        if opts.option_count == 0:
            opts.add_option(Option("(no saved sessions yet)", id="__none__"))
            opts.disabled = True
            return
        opts.highlighted = min(prev_idx, opts.option_count - 1)
        opts.scroll_to_highlight()
        opts.focus()

    def on_option_list_option_selected(self, event: OptionList.OptionSelected) -> None:
        try:
            self.dismiss(int(event.option.id))
        except (TypeError, ValueError):
            self.dismiss(None)


def resume_session_into_state(sid: int, console_print, preview: bool = True) -> bool:
    """Shared helper: load session into state, render a short tail preview."""
    loaded = db_load_session(sid)
    if loaded is None:
        console_print(f"[red]session {sid} not found[/]")
        return False
    state.messages = loaded
    state.current_session_id = sid
    state.tool_calls_count = 0
    state.total_in, state.total_out, state.total_tokens = estimate_session_tokens(loaded)
    console_print(f"[green]▶ resumed session #{sid} ({len(state.messages)} messages)[/]")
    if not preview:
        return True
    tail = state.messages[-6:]
    for m in tail:
        cn = m["content"]
        if isinstance(cn, str):
            preview = cn[:200]
        else:
            texts = [b.get("text", "") for b in cn
                     if isinstance(b, dict) and b.get("type") == "text"]
            preview = (" ".join(texts))[:200] or "[tool blocks]"
        console_print(f"  [dim]{m['role']}:[/] {preview}")
    return True
