"""Modal provider picker — replaces console.input-based /provider flow in the TUI."""
from __future__ import annotations

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import CenterMiddle, Vertical
from textual.widgets import OptionList, Static
from textual.widgets.option_list import Option

from rich.text import Text

from ..constants import PROVIDERS, PROVIDER_LABELS, connected_providers
from .. import state
from .modal_chrome import TUI_MODAL_CHROME_CSS, TuiModalScreen
from .mouse_toggle import enable_mouse, disable_mouse


_PROVIDER_DESCRIPTIONS = {
    "anthropic":     "Claude models — Haiku, Sonnet, Opus",
    "openrouter":    "Free & paid models — open models, OpenAI, more",
    "opencode":      "OpenCode Go — GLM, Kimi, DeepSeek, MiMo, MiniMax, Qwen",
    "opencode_zen":  "OpenCode Zen — MiniMax, HY3, Nemotron, DeepSeek Flash",
}


class ProviderPickerScreen(TuiModalScreen[str | None]):
    """Lists configured providers. Dismisses with the selected provider id, or None."""

    DEFAULT_CSS = (
        TUI_MODAL_CHROME_CSS
        + """
    ProviderPickerScreen #modal {
        width: 64%;
        max-width: 100;
        max-height: 60%;
    }
    ProviderPickerScreen OptionList {
        height: 8;
        margin-top: 1;
    }
    """
    )

    BINDINGS = [
        Binding("escape", "dismiss_cancel", "Cancel", show=True),
        Binding("down", "cursor_down", show=False),
        Binding("up", "cursor_up", show=False),
    ]

    def compose(self) -> ComposeResult:
        with CenterMiddle():
            with Vertical(id="modal"):
                yield Static("◎  Provider", id="modal_title")
                yield OptionList(id="provider_list")
                yield Static(
                    "[#f0b3ff]↑↓[/] navigate   [#f0b3ff]↵[/] select   [#f0b3ff]esc[/] cancel",
                    id="modal_hint",
                )

    def on_mount(self) -> None:
        enable_mouse()
        self._prev_scroll_y = self.app.scroll_sensitivity_y
        self.app.scroll_sensitivity_y = 1.0
        self._populate()

    def on_unmount(self) -> None:
        disable_mouse()
        try:
            self.app.scroll_sensitivity_y = self._prev_scroll_y
        except AttributeError:
            pass

    def _populate(self) -> None:
        opts = self.query_one("#provider_list", OptionList)
        opts.clear_options()
        connected = connected_providers()
        for prov in PROVIDERS:
            label = PROVIDER_LABELS.get(prov, prov)
            desc = _PROVIDER_DESCRIPTIONS.get(prov, "")
            is_active = prov == state.provider
            is_connected = prov in connected
            marker = "● " if is_active else "  "
            name_style = "bold #79c0ff" if is_active else "#79c0ff"
            status = "  connected" if is_connected else "  [dim]not configured[/]"
            row = Text.assemble(
                (marker, "bold #3fb950"),
                (f"{label:<14s}", name_style),
                ("  ", ""),
                (desc, "#8b949e"),
                (status, ""),
            )
            opts.add_option(Option(row, id=prov))
        if opts.option_count:
            opts.highlighted = 0
        opts.focus()

    # ─── events ────────────────────────────────────────────────────────
    def on_option_list_option_selected(self, event: OptionList.OptionSelected) -> None:
        pid = event.option.id
        if pid and pid in PROVIDERS:
            self.dismiss(str(pid))
        else:
            self.dismiss(None)

    # ─── actions ───────────────────────────────────────────────────────
    def action_dismiss_cancel(self) -> None:
        self.dismiss(None)

    def action_cursor_down(self) -> None:
        self.query_one("#provider_list", OptionList).action_cursor_down()

    def action_cursor_up(self) -> None:
        self.query_one("#provider_list", OptionList).action_cursor_up()
