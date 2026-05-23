"""Modal model picker — replaces console.input-based /model flow in the TUI."""
from __future__ import annotations

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import CenterMiddle, Vertical
from textual.widgets import Input, OptionList, Static
from textual.widgets.option_list import Option

from rich.text import Text

from ..constants import (
    MODEL_SOURCE_LABELS, models_for_source, connected_model_sources,
    model_option_id,
    PROVIDER_ANTHROPIC, PROVIDER_ANTHROPIC_API, PROVIDER_ANTHROPIC_AUTH,
    PROVIDER_OPENAI_CODEX, PROVIDER_OPENAI_CODEX_AUTH,
    PROVIDER_HARNESS_AGENT, PROVIDER_OPENCODE_ZEN,
    is_harness_agent_model,
    AUTH_API_KEY, AUTH_OAUTH,
)
from .. import state
from .modal_chrome import (
    TUI_MODAL_CHROME_CSS,
    TuiModalScreen,
    active_marker,
    modal_key,
    primary_style,
    secondary_style,
)
from .mouse_toggle import enable_mouse, disable_mouse
from . import theme as ui


class ModelPickerScreen(TuiModalScreen[str | None]):
    """Lists configured models. Dismisses with the selected model id, or None."""

    DEFAULT_CSS = (
        TUI_MODAL_CHROME_CSS
        + """
    ModelPickerScreen #modal {
        width: 82%;
        max-width: 130;
        max-height: 80%;
    }
    ModelPickerScreen OptionList {
        height: 22;
        margin-top: 1;
    }
    """
    )

    BINDINGS = [
        Binding("escape", "dismiss_cancel", "Cancel", show=True),
        Binding("enter", "accept_selection", "Select", show=True),
        Binding("down", "cursor_down", show=False),
        Binding("up", "cursor_up", show=False),
        Binding("pagedown", "page_down", show=False),
        Binding("pageup", "page_up", show=False),
        Binding("slash", "focus_search", "Search", show=True),
    ]

    def compose(self) -> ComposeResult:
        with CenterMiddle():
            with Vertical(id="modal"):
                yield Static("✦  Models", id="modal_title")
                yield Input(value="", placeholder="search models…", id="model_search")
                yield OptionList(id="model_list")
                yield Static(
                    f"{modal_key('↑↓')} navigate   {modal_key('↵')} select   "
                    f"{modal_key('/')} search   {modal_key('esc')} cancel",
                    id="modal_hint",
                )

    def on_mount(self) -> None:
        enable_mouse()
        self._prev_scroll_y = self.app.scroll_sensitivity_y
        self.app.scroll_sensitivity_y = 1.0
        self._populate()
        self.query_one("#model_search", Input).focus()

    def on_unmount(self) -> None:
        disable_mouse()
        try:
            self.app.scroll_sensitivity_y = self._prev_scroll_y
        except AttributeError:
            pass

    def _is_active(self, source: str, model_id: str) -> bool:
        if model_id != state.MODEL:
            return False
        if source == PROVIDER_HARNESS_AGENT:
            return state.provider == PROVIDER_OPENCODE_ZEN and state.harness_agent_free
        if source == PROVIDER_OPENCODE_ZEN:
            return state.provider == PROVIDER_OPENCODE_ZEN and not state.harness_agent_free
        if source == PROVIDER_ANTHROPIC_AUTH:
            return state.provider == PROVIDER_ANTHROPIC and state.auth_mode == AUTH_OAUTH
        if source == PROVIDER_ANTHROPIC_API:
            return state.provider == PROVIDER_ANTHROPIC and state.auth_mode == AUTH_API_KEY
        if source == PROVIDER_OPENAI_CODEX_AUTH:
            return state.provider == PROVIDER_OPENAI_CODEX and state.auth_mode == AUTH_OAUTH
        return state.provider == source

    def _populate(self, query: str = "") -> None:
        q = query.strip().lower()
        opts = self.query_one("#model_list", OptionList)
        opts.clear_options()
        sources = connected_model_sources()
        rows: list[tuple[str, str, str]] = []
        for src in sources:
            rows += [(src, m, d) for m, d in models_for_source(src)]
        matched = 0
        for src, m, desc in rows:
            label_name = MODEL_SOURCE_LABELS.get(src, src)
            if q and q not in m.lower() and q not in desc.lower() and q not in label_name.lower():
                continue
            is_active = self._is_active(src, m)
            marker, marker_style = active_marker(is_active)
            label = Text.assemble(
                (marker, marker_style),
                (f"{m:<40s}", primary_style(is_active)),
                ("  ", ""),
                (f"{label_name:<14s}", secondary_style()),
                ("  ", ""),
                (desc[:60], ui.FG_MUTE),
            )
            opts.add_option(Option(label, id=model_option_id(src, m)))
            matched += 1
        if opts.option_count:
            opts.highlighted = 0
            opts.disabled = False
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
        if event.input.id != "model_search":
            return
        self.query_one("#model_list", OptionList).focus()

    def on_option_list_option_selected(self, event: OptionList.OptionSelected) -> None:
        oid = event.option.id
        if oid == "__none__":
            return
        self.dismiss(str(oid) if oid else None)

    # ─── actions ───────────────────────────────────────────────────────
    def action_dismiss_cancel(self) -> None:
        try:
            sb = self.query_one("#model_search", Input)
            if sb.value:
                sb.value = ""
                self._populate()
                sb.focus()
                return
        except Exception:
            pass
        self.dismiss(None)

    def action_focus_search(self) -> None:
        self.query_one("#model_search", Input).focus()

    def action_cursor_down(self) -> None:
        self.query_one("#model_list", OptionList).action_cursor_down()

    def action_cursor_up(self) -> None:
        self.query_one("#model_list", OptionList).action_cursor_up()

    def action_page_down(self) -> None:
        self.query_one("#model_list", OptionList).action_page_down()

    def action_page_up(self) -> None:
        self.query_one("#model_list", OptionList).action_page_up()

    def action_accept_selection(self) -> None:
        self._accept()

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
