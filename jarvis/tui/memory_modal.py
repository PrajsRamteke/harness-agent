"""Centered memory control modal — list / add / delete / clear personal facts.

User interaction:
    ↑/↓        navigate the fact list
    a          add a new fact (sub-modal)
    d / del    delete the highlighted fact
    c          wipe all (asks for 'yes' confirmation)
    r          refresh from storage
    Esc        close
"""
from __future__ import annotations

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import CenterMiddle, Vertical
from textual.widgets import Input, OptionList, Static
from textual.widgets.option_list import Option

from rich.text import Text

from ..storage import memory as mem
from .modal_chrome import TUI_MODAL_CHROME_CSS, TuiModalScreen, _ellipsis
from .mouse_toggle import enable_mouse, disable_mouse
from . import theme as ui


# ── add-fact sub-modal ────────────────────────────────────────────────────


class _AddFactScreen(TuiModalScreen[str | None]):
    DEFAULT_CSS = TUI_MODAL_CHROME_CSS + """
    _AddFactScreen #modal { width: 60%; max-width: 80; max-height: 40%; }
    """
    BINDINGS = [Binding("escape", "cancel", "Cancel", show=True)]

    def compose(self) -> ComposeResult:
        with CenterMiddle():
            with Vertical(id="modal"):
                yield Static("➕  New Memory Fact", id="modal_title")
                yield Input(placeholder="e.g. user prefers TypeScript over JavaScript", id="fact_text")
                yield Static(
                    f"[{ui.ACCENT_3}]↵[/] save   [{ui.ACCENT_3}]esc[/] cancel",
                    id="modal_hint",
                )

    def on_mount(self) -> None:
        self.query_one("#fact_text", Input).focus()

    def on_input_submitted(self, event: Input.Submitted) -> None:
        text = (event.value or "").strip()
        if not text:
            return
        self.dismiss(text)

    def action_cancel(self) -> None:
        self.dismiss(None)


# ── confirm sub-modal ─────────────────────────────────────────────────────


class _ConfirmClearScreen(TuiModalScreen[bool]):
    DEFAULT_CSS = TUI_MODAL_CHROME_CSS + """
    _ConfirmClearScreen #modal { width: 52%; max-width: 70; max-height: 32%; }
    _ConfirmClearScreen #confirm_prompt { padding: 0 1; color: {ui.FG}; margin-bottom: 1; }
    """
    BINDINGS = [Binding("escape", "cancel", "Cancel", show=True)]

    def compose(self) -> ComposeResult:
        with CenterMiddle():
            with Vertical(id="modal"):
                yield Static("⚠  Wipe All Memory?", id="modal_title")
                yield Static(
                    f"Type [bold {ui.WARN}]yes[/] to confirm, anything else cancels.",
                    id="confirm_prompt",
                )
                yield Input(placeholder="yes", id="confirm_input")
                yield Static(
                    f"[{ui.ACCENT_3}]↵[/] confirm   [{ui.ACCENT_3}]esc[/] cancel",
                    id="modal_hint",
                )

    def on_mount(self) -> None:
        self.query_one("#confirm_input", Input).focus()

    def on_input_submitted(self, event: Input.Submitted) -> None:
        self.dismiss((event.value or "").strip().lower() == "yes")

    def action_cancel(self) -> None:
        self.dismiss(False)


# ── main memory modal ─────────────────────────────────────────────────────


class MemoryModalScreen(TuiModalScreen[None]):
    DEFAULT_CSS = TUI_MODAL_CHROME_CSS + """
    MemoryModalScreen #modal { width: 78%; max-width: 120; max-height: 82%; }
    MemoryModalScreen OptionList { height: 1fr; min-height: 12; }
    """

    BINDINGS = [
        Binding("escape", "dismiss_cancel", "Close", show=True),
        Binding("down", "cursor_down", show=False),
        Binding("up", "cursor_up", show=False),
        Binding("a", "add", "Add", show=True),
        Binding("d", "delete", "Delete", show=True),
        Binding("delete", "delete", show=False),
        Binding("c", "clear", "Clear", show=True),
        Binding("r", "refresh", "Refresh", show=True),
    ]

    def compose(self) -> ComposeResult:
        with CenterMiddle():
            with Vertical(id="modal"):
                yield Static("◆  Memory", id="modal_title")
                yield Static("", id="modal_status")
                yield OptionList(id="fact_list")
                yield Static(
                    f"[{ui.ACCENT_3}]↑↓[/] navigate   [{ui.ACCENT_3}]a[/] add   "
                    f"[{ui.ACCENT_3}]d[/] delete   [{ui.ACCENT_3}]c[/] clear   "
                    f"[{ui.ACCENT_3}]r[/] refresh   [{ui.ACCENT_3}]esc[/] close",
                    id="modal_hint",
                )

    def on_mount(self) -> None:
        enable_mouse()
        self._populate()

    def on_unmount(self) -> None:
        disable_mouse()

    def _populate(self) -> None:
        opts = self.query_one("#fact_list", OptionList)
        opts.clear_options()
        facts = mem.list_facts()
        if not facts:
            opts.add_option(Option(
                Text("  memory is empty — press 'a' to add a fact",
                     style=f"italic {ui.FG_DIM}"),
                disabled=True,
            ))
        else:
            for f in facts:
                row = Text.assemble(
                    ("  ", ""),
                    (f"#{f['id']:<5d}", ui.FG_DIM),
                    ("  ", ""),
                    (_ellipsis(f["text"], 78), ui.FG),
                )
                opts.add_option(Option(row, id=f"fact:{f['id']}"))
        try:
            self.query_one("#modal_title", Static).update(
                f"◆  Memory   [{ui.FG_DIM}]{len(facts)} fact{'s' if len(facts) != 1 else ''}[/]"
            )
        except Exception:
            pass
        opts.focus()

    def _notify(self, msg: str, error: bool = False) -> None:
        try:
            color = ui.ERR if error else ui.OK
            self.query_one("#modal_status", Static).update(f"[{color}]{msg}[/]")
        except Exception:
            pass

    def _highlighted_id(self) -> int | None:
        opts = self.query_one("#fact_list", OptionList)
        if opts.highlighted is None:
            return None
        try:
            opt = opts.get_option_at_index(opts.highlighted)
        except Exception:
            return None
        oid = getattr(opt, "id", None)
        if not oid or not oid.startswith("fact:"):
            return None
        try:
            return int(oid.split(":", 1)[1])
        except ValueError:
            return None

    # ── actions ────────────────────────────────────────────────────────────

    def action_dismiss_cancel(self) -> None:
        self.dismiss(None)

    def action_cursor_down(self) -> None:
        self.query_one("#fact_list", OptionList).action_cursor_down()

    def action_cursor_up(self) -> None:
        self.query_one("#fact_list", OptionList).action_cursor_up()

    def action_add(self) -> None:
        def after(text: str | None) -> None:
            if not text:
                return
            f = mem.add_fact(text)
            self._populate()
            self._notify(f"✓ saved #{f['id']}: {f['text'][:80]}")
        self.app.push_screen(_AddFactScreen(), after)

    def action_delete(self) -> None:
        fid = self._highlighted_id()
        if fid is None:
            self._notify("(highlight a fact to delete)")
            return
        if mem.delete_fact(fid):
            self._populate()
            self._notify(f"✓ deleted #{fid}")
        else:
            self._notify(f"no fact #{fid}", error=True)

    def action_clear(self) -> None:
        def after(ok: bool) -> None:
            if not ok:
                self._notify("clear cancelled")
                return
            n = mem.clear_all()
            self._populate()
            self._notify(f"✓ cleared {n} fact(s)")
        self.app.push_screen(_ConfirmClearScreen(), after)

    def action_refresh(self) -> None:
        self._populate()
        self._notify("re-read storage")
