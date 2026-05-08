"""Modal model picker — replaces console.input-based /model flow in the TUI."""
from __future__ import annotations

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import CenterMiddle, Vertical
from textual.widgets import Input, OptionList, Static
from textual.widgets.option_list import Option

from rich.text import Text

from ..constants import PROVIDER_LABELS, models_for
from .. import state
from .modal_chrome import TUI_MODAL_CHROME_CSS, TuiModalScreen
from .mouse_toggle import enable_mouse, disable_mouse


class ModelPickerScreen(TuiModalScreen[str | None]):
    """Lists configured models. Dismisses with the selected model id, or None."""

    DEFAULT_CSS = (
        TUI_MODAL_CHROME_CSS
        + """
    ModelPickerScreen #modal {
        width: 80%;
        max-width: 120;
        max-height: 80%;
        padding: 2 3;
    }
    ModelPickerScreen OptionList {
        height: 22;
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
    ]

    def compose(self) -> ComposeResult:
        with CenterMiddle():
            with Vertical(id="modal"):
                yield Static("🤖  models", id="modal_title")
                yield Input(value="", placeholder="type to search models…", id="model_search")
                yield OptionList(id="model_list")
                yield Static(
                    "↑/↓ navigate • Enter select • Esc cancel",
                    id="modal_hint",
                )

    def on_mount(self) -> None:
        enable_mouse()
        self._prev_scroll_y = self.app.scroll_sensitivity_y
        self.app.scroll_sensitivity_y = 1.0
        self._populate()
        inp = self.query_one("#model_search", Input)
        inp.focus()

    def on_unmount(self) -> None:
        disable_mouse()
        try:
            self.app.scroll_sensitivity_y = self._prev_scroll_y
        except AttributeError:
            pass

    def _populate(self, query: str = "") -> None:
        q = query.strip().lower()
        opts = self.query_one("#model_list", OptionList)
        opts.clear_options()
        rows = (
            [("anthropic", m, d) for m, d in models_for("anthropic")]
            + [("openrouter", m, d) for m, d in models_for("openrouter")]
            + [("opencode", m, d) for m, d in models_for("opencode")]
        )
        matched = 0
        for prov, m, desc in rows:
            if q and q not in m.lower() and q not in desc.lower() and q not in PROVIDER_LABELS.get(prov, prov).lower():
                continue
            marker = " ●" if m == state.MODEL else "  "
            label = Text.assemble(
                (marker, "green bold"),
                (f"{m:<42}", "cyan"),
                (f"  {PROVIDER_LABELS.get(prov, prov):<12}", "magenta"),
                (desc[:52], "dim"),
            )
            opts.add_option(Option(label, id=m))
            matched += 1
        if opts.option_count:
            opts.highlighted = 0
        elif matched == 0 and q:
            opts.add_option(Option(f"(no models matching \"{q}\")", id="__none__"))
            opts.disabled = True
        else:
            opts.disabled = False

    # ─── events ────────────────────────────────────────────────────────
    def on_input_changed(self, event: Input.Changed) -> None:
        if event.input.id == "model_search":
            self._populate(event.value or "")

    def on_input_submitted(self, event: Input.Submitted) -> None:
        self._accept()

    def on_option_list_option_selected(self, event: OptionList.OptionSelected) -> None:
        mid = event.option.id
        if mid == "__none__":
            return
        self.dismiss(str(mid) if mid else None)

    # ─── actions ───────────────────────────────────────────────────────
    def action_dismiss_cancel(self) -> None:
        self.dismiss(None)

    def action_cursor_down(self) -> None:
        self.query_one("#model_list", OptionList).action_cursor_down()

    def action_cursor_up(self) -> None:
        self.query_one("#model_list", OptionList).action_cursor_up()

    def action_page_down(self) -> None:
        self.query_one("#model_list", OptionList).action_page_down()

    def action_page_up(self) -> None:
        self.query_one("#model_list", OptionList).action_page_up()

    def _accept(self) -> None:
        opts = self.query_one("#model_list", OptionList)
        if opts.option_count == 0 or opts.highlighted is None:
            self.dismiss(None)
            return
        opt = opts.get_option_at_index(opts.highlighted)
        if not opt.id or opt.id == "__none__":
            self.dismiss(None)
            return
        self.dismiss(str(opt.id) if opt.id else None)
