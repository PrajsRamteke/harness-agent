"""Centered command palette modal (OpenCode / Spotlight style)."""
from __future__ import annotations

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Vertical
from textual.screen import ModalScreen
from textual.widgets import Input, OptionList, Static
from textual.widgets.option_list import Option

from rich.text import Text

from .commands_catalog import filter_commands
from .mouse_toggle import enable_mouse, disable_mouse


class CommandPaletteScreen(ModalScreen[str | None]):
    """Centered overlay. Dismisses with the selected command string, or None."""

    DEFAULT_CSS = """
    CommandPaletteScreen {
        align: center middle;
        background: rgba(0, 0, 0, 0.55);
    }
    CommandPaletteScreen > #modal {
        width: 70%;
        max-width: 90;
        height: auto;
        max-height: 70%;
        background: #12151a;
        border: tall #7aa2f7;
        padding: 1 1;
    }
    CommandPaletteScreen #palette_title {
        color: #bb9af7;
        text-style: bold;
        padding: 0 1 1 1;
    }
    CommandPaletteScreen Input {
        background: #0f1216;
        color: #e6e6e6;
        border: tall #2b3340;
    }
    CommandPaletteScreen OptionList {
        background: #12151a;
        color: #e6e6e6;
        height: 18;
        border: none;
        margin-top: 1;
        overflow-y: auto;
        scrollbar-size-vertical: 1;
    }
    CommandPaletteScreen OptionList:focus > .option-list--option-highlighted,
    CommandPaletteScreen OptionList > .option-list--option-highlighted {
        background: #2b3340;
        color: #ffffff;
    }
    CommandPaletteScreen #palette_hint {
        color: #7aa2f7;
        padding-top: 1;
    }
    """

    BINDINGS = [
        Binding("escape", "cancel", "Cancel", show=True),
        Binding("down", "cursor_down", show=False),
        Binding("up", "cursor_up", show=False),
        Binding("pagedown", "page_down", show=False),
        Binding("pageup", "page_up", show=False),
    ]

    def __init__(self, initial: str = "/"):
        super().__init__()
        self._initial = initial or "/"

    def compose(self) -> ComposeResult:
        with Vertical(id="modal"):
            yield Static("⌘  commands", id="palette_title")
            yield Input(value=self._initial, placeholder="type to filter…", id="palette_input")
            yield OptionList(id="palette_options")
            yield Static(
                "↑/↓ navigate • Enter run • Esc cancel",
                id="palette_hint",
            )

    def on_mount(self):
        enable_mouse()
        self._prev_scroll_y = self.app.scroll_sensitivity_y
        self.app.scroll_sensitivity_y = 1.0
        self._refresh(self._initial)
        inp = self.query_one("#palette_input", Input)
        inp.focus()
        inp.cursor_position = len(inp.value)

    def on_unmount(self):
        disable_mouse()
        try:
            self.app.scroll_sensitivity_y = self._prev_scroll_y
        except AttributeError:
            pass

    def _refresh(self, query: str):
        opts = self.query_one("#palette_options", OptionList)
        opts.clear_options()
        matches = filter_commands(query)
        for cmd, desc in matches[:60]:
            label = Text.assemble(
                (f"{cmd:<22}", "bold cyan"), ("  ", ""), (desc, "dim")
            )
            opts.add_option(Option(label, id=cmd))
        if opts.option_count:
            opts.highlighted = 0

    # ─── events ────────────────────────────────────────────────────────
    def on_input_changed(self, event: Input.Changed) -> None:
        if event.input.id == "palette_input":
            self._refresh(event.value or "")

    def on_input_submitted(self, event: Input.Submitted) -> None:
        self._accept()

    def on_option_list_option_selected(self, event: OptionList.OptionSelected) -> None:
        self.dismiss(event.option.id)

    # ─── actions ───────────────────────────────────────────────────────
    def action_cancel(self):
        self.dismiss(None)

    def action_cursor_down(self):
        self.query_one("#palette_options", OptionList).action_cursor_down()

    def action_cursor_up(self):
        self.query_one("#palette_options", OptionList).action_cursor_up()

    def action_page_down(self):
        self.query_one("#palette_options", OptionList).action_page_down()

    def action_page_up(self):
        self.query_one("#palette_options", OptionList).action_page_up()


    def _accept(self):
        opts = self.query_one("#palette_options", OptionList)
        if opts.option_count == 0 or opts.highlighted is None:
            self.dismiss(None)
            return
        opt = opts.get_option_at_index(opts.highlighted)
        self.dismiss(opt.id)
