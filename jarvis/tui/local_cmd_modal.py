"""Modal that lists all local (non-LLM) shell commands with search and one-click run.

Triggered by the ``/local`` slash command from the palette or prompt.
"""
from __future__ import annotations

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import CenterMiddle, Vertical
from textual.widgets import Input, OptionList, Static
from textual.widgets.option_list import Option

from rich.text import Text

from .modal_chrome import TUI_MODAL_CHROME_CSS, TuiModalScreen, ROW_NAME_WIDTH
from .mouse_toggle import enable_mouse, disable_mouse


# ─── local command catalog ───────────────────────────────────────────────
# These are shell/git/file commands that run locally without any LLM call.

LOCAL_COMMANDS: list[tuple[str, str]] = [
    ("/ls", "list directory"),
    ("/cd <dir>", "change working dir"),
    ("/pwd", "print working dir"),
    ("/find <pat>", "glob find files (e.g. **/*.py)"),
    ("/run <cmd>", "run a shell command (auto-approved)"),
    ("/undo", "restore last file edit"),
    ("/git", "git status"),
    ("/diff [path]", "git diff"),
]


def filter_local_commands(query: str):
    """Return commands whose cmd or description matches the query substring."""
    q = query.strip().lower()
    if not q or q == "/":
        return LOCAL_COMMANDS
    return [
        (c, d) for (c, d) in LOCAL_COMMANDS
        if q in c.lower() or q in d.lower()
    ]


# ─── modal screen ────────────────────────────────────────────────────────


class LocalCmdModalScreen(TuiModalScreen[str | None]):
    """Centered overlay listing local shell/file/git commands.

    Dismisses with the selected command label (e.g. ``"/ls"``, ``"/cd <dir>"``)
    or ``None`` if cancelled. The caller is responsible for extracting the bare
    command name from the label and dispatching it.
    """

    DEFAULT_CSS = (
        TUI_MODAL_CHROME_CSS
        + """
    LocalCmdModalScreen #modal {
        width: 76%;
        max-width: 100;
        max-height: 72%;
    }
    LocalCmdModalScreen OptionList {
        height: 1fr;
        min-height: 8;
        margin-top: 1;
    }
    """
    )

    BINDINGS = [
        Binding("escape", "dismiss_cancel", "Cancel", show=True),
        Binding("down", "cursor_down", show=False),
        Binding("up", "cursor_up", show=False),
        Binding("pagedown", "page_down", show=False),
        Binding("pageup", "page_up", show=False),
        Binding("slash", "focus_search", "Search", show=True),
    ]

    def __init__(self, initial: str = "") -> None:
        super().__init__()
        self._initial = initial

    def compose(self) -> ComposeResult:
        with CenterMiddle():
            with Vertical(id="modal"):
                yield Static("▣  Local Commands", id="modal_title")
                yield Static("", id="modal_status")
                yield Input(
                    value=self._initial,
                    placeholder="search local commands…",
                    id="local_search",
                )
                yield OptionList(id="local_options")
                yield Static(
                    "[#f0b3ff]↑↓[/] nav   [#f0b3ff]/[/] search   "
                    "[#f0b3ff]↵[/] run   [#f0b3ff]esc[/] close",
                    id="modal_hint",
                )

    # ── lifecycle ─────────────────────────────────────────────────────────

    def on_mount(self) -> None:
        enable_mouse()
        self._populate(self._initial)
        inp = self.query_one("#local_search", Input)
        inp.focus()
        inp.cursor_position = len(inp.value)
        self._update_status()

    def on_unmount(self) -> None:
        disable_mouse()

    def _update_status(self, msg: str = "") -> None:
        count = len(LOCAL_COMMANDS)
        try:
            status = f"[#8b949e]{count} local command{'s' if count != 1 else ''}[/]"
            if msg:
                status += f"  ·  {msg}"
            self.query_one("#modal_status", Static).update(status)
        except Exception:
            pass

    def _populate(self, query: str = "") -> None:
        opts = self.query_one("#local_options", OptionList)
        opts.clear_options()
        matches = filter_local_commands(query)
        for cmd_label, desc in matches:
            label = Text.assemble(
                ("  ", ""),
                (f"{cmd_label:<{ROW_NAME_WIDTH}s}", "bold #79c0ff"),
                ("  ", ""),
                (desc, "#8b949e"),
            )
            opts.add_option(Option(label, id=cmd_label))
        if opts.option_count:
            opts.highlighted = 0

    # ── events ────────────────────────────────────────────────────────────

    def on_input_changed(self, event: Input.Changed) -> None:
        if event.input.id == "local_search":
            self._populate(event.value or "")

    def on_input_submitted(self, event: Input.Submitted) -> None:
        if event.input.id == "local_search":
            # Enter from the search field focuses the list.
            self.query_one("#local_options", OptionList).focus()
            return
        # Should not happen, but handle it.
        self._accept()

    def on_option_list_option_selected(self, event: OptionList.OptionSelected) -> None:
        self.dismiss(event.option.id)

    # ── actions ───────────────────────────────────────────────────────────

    def action_dismiss_cancel(self) -> None:
        """Esc: clear search if non-empty, otherwise close."""
        try:
            sb = self.query_one("#local_search", Input)
            if sb.value:
                sb.value = ""
                self._populate()
                self.query_one("#local_options", OptionList).focus()
                return
        except Exception:
            pass
        self.dismiss(None)

    def action_focus_search(self) -> None:
        self.query_one("#local_search", Input).focus()

    def action_cursor_down(self) -> None:
        self.query_one("#local_options", OptionList).action_cursor_down()

    def action_cursor_up(self) -> None:
        self.query_one("#local_options", OptionList).action_cursor_up()

    def action_page_down(self) -> None:
        self.query_one("#local_options", OptionList).action_page_down()

    def action_page_up(self) -> None:
        self.query_one("#local_options", OptionList).action_page_up()

    def _accept(self) -> None:
        opts = self.query_one("#local_options", OptionList)
        if opts.option_count == 0 or opts.highlighted is None:
            self.dismiss(None)
            return
        opt = opts.get_option_at_index(opts.highlighted)
        self.dismiss(opt.id)
