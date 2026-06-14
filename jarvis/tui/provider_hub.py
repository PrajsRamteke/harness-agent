"""Unified provider setup hub — single entry point for /provider, /key, /login, /logout, /auth.

Replaces the confusing multi-command flow with one modal:
  Level 1: Auth (OAuth)  or  API Key
  Level 2a: OAuth provider picker (Anthropic / OpenAI Codex)
  Level 2b: API key list (all providers) with edit/delete/add
"""
from __future__ import annotations

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import CenterMiddle, Vertical
from textual.widgets import OptionList, Static
from textual.widgets.option_list import Option

from rich.text import Text

from .modal_chrome import TUI_MODAL_CHROME_CSS, TuiModalScreen, active_marker, primary_style
from .mouse_toggle import enable_mouse, disable_mouse
from . import theme as ui


_MODE_OPTIONS = [
    ("oauth", "Auth (OAuth)",     "Anthropic / OpenAI Codex — subscription sign-in"),
    ("api",   "API Key",          "Anthropic, OpenRouter, OpenCode, Kimchi, Codex — enter API key"),
]


class ProviderHubScreen(TuiModalScreen[str | None]):
    """Unified provider setup hub.

    Two choices: Auth (OAuth) or API Key.  Delegates to existing modals.
    Dismisses with the selected mode id or ``None`` on cancel.
    """

    DEFAULT_CSS = (
        TUI_MODAL_CHROME_CSS
        + """
    ProviderHubScreen #modal {
        width: 56%;
        max-width: 96;
        max-height: 54%;
    }
    ProviderHubScreen OptionList {
        height: 8;
        margin-top: 1;
    }
    """
    )

    BINDINGS = [
        Binding("escape", "dismiss_cancel", "Close", show=True),
        Binding("down", "cursor_down", show=False),
        Binding("up", "cursor_up", show=False),
    ]

    def compose(self) -> ComposeResult:
        with CenterMiddle():
            with Vertical(id="modal"):
                yield Static("◎  Provider Setup", id="modal_title")
                yield Static(
                    f"[{ui.FG_MUTE}]Choose how you want to connect to AI models.[/]",
                    id="modal_subtitle",
                )
                yield OptionList(id="mode_list")
                yield Static(
                    f"[{ui.ACCENT_3}]↑↓[/] navigate   [{ui.ACCENT_3}]↵[/] select   "
                    f"[{ui.ACCENT_3}]esc[/] close",
                    id="modal_hint",
                )

    def on_mount(self) -> None:
        enable_mouse()
        opts = self.query_one("#mode_list", OptionList)
        for mid, label, desc in _MODE_OPTIONS:
            row = Text.assemble(
                ("  ", ui.FG_DIM),
                (f"{label:<20s}", ui.ACCENT),
                (desc, ui.FG_MUTE),
            )
            opts.add_option(Option(row, id=mid))
        if opts.option_count:
            opts.highlighted = 0
        opts.focus()
        self._prev_scroll_y = getattr(self.app, "scroll_sensitivity_y", 1.0)
        try:
            self.app.scroll_sensitivity_y = 1.0
        except AttributeError:
            pass

    def on_unmount(self) -> None:
        disable_mouse()
        try:
            self.app.scroll_sensitivity_y = self._prev_scroll_y
        except AttributeError:
            pass

    # ─── events ────────────────────────────────────────────────────────

    def on_option_list_option_selected(self, event: OptionList.OptionSelected) -> None:
        """Fired by OptionList when Enter is pressed or option clicked."""
        mode = str(event.option.id) if event.option.id else ""
        if mode == "oauth":
            self.dismiss("oauth")
        elif mode == "api":
            self.dismiss("api")
        else:
            self.dismiss(None)

    # ─── actions ───────────────────────────────────────────────────────

    def action_dismiss_cancel(self) -> None:
        self.dismiss(None)

    def action_cursor_down(self) -> None:
        self.query_one("#mode_list", OptionList).action_cursor_down()

    def action_cursor_up(self) -> None:
        self.query_one("#mode_list", OptionList).action_cursor_up()
