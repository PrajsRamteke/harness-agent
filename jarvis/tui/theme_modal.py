"""Centered theme picker — switch between red / blue / purple / green."""
from __future__ import annotations

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import CenterMiddle, Vertical
from textual.widgets import OptionList, Static
from textual.widgets.option_list import Option

from rich.text import Text

from ..storage.settings import get_settings
from .. import state
from .modal_chrome import TUI_MODAL_CHROME_CSS, TuiModalScreen, active_marker, get_modal_chrome_css
from .mouse_toggle import enable_mouse, disable_mouse
from . import theme as ui


_THEMES = [
    ("red",       "warm coral tones, soft pink highlights"),
    ("blue",      "cool blue tones, teal secondary, sky highlights"),
    ("purple",    "soft violet accents, warm amber warnings"),
    ("green",     "nature green primary, teal secondary, mint highlights"),
    ("orange",    "fiery orange accents, golden highlights"),
    ("yellow",    "gold and amber tones, bright highlights"),
    ("rose",      "hot pink accents, magenta borders, romantic"),
    ("slate",     "neutral grays, no color bias, professional"),
    ("ocean",     "deep navy backgrounds, ice-blue accents"),
    ("cyberpunk", "neon cyan + magenta on dark purple, high contrast"),
    ("monochrome","pure black, zero color — white/gray only"),
    ("forest",    "deep earthy greens, brown borders, amber highlights"),
    ("dracula",   "classic dark: purple/pink accents, green highlights"),
    ("sunset",    "warm brick bg, orange coral accents, amber glow"),
    ("dark",      "pure black bg, clean blue/teal accents, classic dark"),
]


class ThemePickerScreen(TuiModalScreen[str | None]):
    DEFAULT_CSS = get_modal_chrome_css() + """
    ThemePickerScreen #modal { width: 56%; max-width: 80; max-height: 80%; }
    ThemePickerScreen OptionList { height: 14; }
    """

    def __init__(self) -> None:
        super().__init__()
        self._original_theme = state.theme
        self._selected_theme = state.theme

    BINDINGS = [
        Binding("escape", "cancel", "Cancel", show=True),
        Binding("down", "cursor_down", show=False),
        Binding("up", "cursor_up", show=False),
    ]

    def compose(self) -> ComposeResult:
        with CenterMiddle():
            with Vertical(id="modal"):
                yield Static("◉  Theme", id="modal_title")
                yield OptionList(id="theme_list")
                yield Static(
                    f"[{ui.ACCENT_3}]↑↓[/] preview   [{ui.ACCENT_3}]↵[/] save   "
                    f"[{ui.ACCENT_3}]esc[/] cancel",
                    id="modal_hint",
                )

    def on_mount(self) -> None:
        enable_mouse()
        opts = self.query_one("#theme_list", OptionList)
        active_idx = 0
        for i, (name, desc) in enumerate(_THEMES):
            is_active = name == state.theme
            marker = "● " if is_active else "  "
            row = self._format_row(name, desc, is_active=is_active)
            opts.add_option(Option(row, id=name))
            if is_active:
                active_idx = i
        opts.highlighted = active_idx
        self._selected_theme = state.theme
        opts.focus()

    def on_unmount(self) -> None:
        disable_mouse()

    def _format_row(self, name: str, desc: str, *, is_active: bool) -> Text:
        marker, marker_style = active_marker(is_active)
        return Text.assemble(
            (marker, marker_style),
            (f"{name:<10s}", f"bold {ui.ACCENT_2}" if is_active else ui.ACCENT_2),
            ("  ", ""),
            (desc, ui.FG_MUTE),
        )

    def _refresh_rows(self) -> None:
        opts = self.query_one("#theme_list", OptionList)
        highlighted = opts.highlighted
        scroll_y = opts.scroll_y
        opts.clear_options()
        for name, desc in _THEMES:
            opts.add_option(
                Option(self._format_row(name, desc, is_active=name == self._selected_theme), id=name)
            )
        if opts.option_count:
            opts.highlighted = highlighted if highlighted is not None else 0
            opts.scroll_y = scroll_y
            opts.scroll_to_highlight()
            opts.focus()

    def _preview_theme(self, name: str | None) -> None:
        if not name or name == self._selected_theme:
            return
        self._selected_theme = name
        try:
            self.app._apply_theme_runtime(name, rebuild_transcript=False)
        except Exception:
            ui.set_theme(name)
        self._refresh_rows()

    def on_option_list_option_highlighted(self, event: OptionList.OptionHighlighted) -> None:
        self._preview_theme(str(event.option.id) if event.option.id else None)

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
        if self._selected_theme != self._original_theme:
            self._preview_theme(self._original_theme)
        self.dismiss(None)

    def action_cursor_down(self) -> None:
        self.query_one("#theme_list", OptionList).action_cursor_down()

    def action_cursor_up(self) -> None:
        self.query_one("#theme_list", OptionList).action_cursor_up()
