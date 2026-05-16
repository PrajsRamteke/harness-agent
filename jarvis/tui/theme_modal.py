"""Centered theme picker — switch between red / purple."""
from __future__ import annotations

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import CenterMiddle, Vertical
from textual.widgets import OptionList, Static
from textual.widgets.option_list import Option

from rich.text import Text

from ..storage.settings import get_settings
from .. import state
from .modal_chrome import TUI_MODAL_CHROME_CSS, TuiModalScreen
from .mouse_toggle import enable_mouse, disable_mouse


_THEMES = [
    ("red",    "original green + magenta accents"),
    ("purple", "soft purple + green accents"),
]


class ThemePickerScreen(TuiModalScreen[str | None]):
    DEFAULT_CSS = TUI_MODAL_CHROME_CSS + """
    ThemePickerScreen #modal { width: 56%; max-width: 80; max-height: 50%; }
    ThemePickerScreen OptionList { height: 6; }
    """

    BINDINGS = [
        Binding("escape", "cancel", "Cancel", show=True),
        Binding("down", "cursor_down", show=False),
        Binding("up", "cursor_up", show=False),
    ]

    def compose(self) -> ComposeResult:
        with CenterMiddle():
            with Vertical(id="modal"):
                yield Static("🎨  Theme", id="modal_title")
                yield OptionList(id="theme_list")
                yield Static(
                    "[#f0b3ff]↑↓[/] navigate   [#f0b3ff]↵[/] apply   [#f0b3ff]esc[/] cancel",
                    id="modal_hint",
                )

    def on_mount(self) -> None:
        enable_mouse()
        opts = self.query_one("#theme_list", OptionList)
        active_idx = 0
        for i, (name, desc) in enumerate(_THEMES):
            is_active = name == state.theme
            marker = "● " if is_active else "  "
            row = Text.assemble(
                (marker, "bold #3fb950"),
                (f"{name:<10s}", "bold #79c0ff" if is_active else "#79c0ff"),
                ("  ", ""),
                (desc, "#8b949e"),
            )
            opts.add_option(Option(row, id=name))
            if is_active:
                active_idx = i
        opts.highlighted = active_idx
        opts.focus()

    def on_unmount(self) -> None:
        disable_mouse()

    def on_option_list_option_selected(self, event: OptionList.OptionSelected) -> None:
        name = event.option.id
        if not name:
            self.dismiss(None)
            return
        try:
            get_settings().set("theme", name)
            state._reload_saved_theme()
            self.dismiss(name)
        except Exception:
            self.dismiss(None)

    def action_cancel(self) -> None:
        self.dismiss(None)

    def action_cursor_down(self) -> None:
        self.query_one("#theme_list", OptionList).action_cursor_down()

    def action_cursor_up(self) -> None:
        self.query_one("#theme_list", OptionList).action_cursor_up()
