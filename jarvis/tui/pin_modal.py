"""Pinned context modal — preview, append, and clear standing instructions."""
from __future__ import annotations

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import CenterMiddle, ScrollableContainer, Vertical
from textual.widgets import Input, Static, TextArea

from rich.text import Text

from ..constants import PIN_FILE
from ..storage import pin as pin_store
from .modal_chrome import TUI_MODAL_CHROME_CSS, TuiModalScreen, _ellipsis
from .mouse_toggle import enable_mouse, disable_mouse
from . import theme as ui


class _AddPinScreen(TuiModalScreen[str | None]):
    DEFAULT_CSS = TUI_MODAL_CHROME_CSS + """
    _AddPinScreen #modal { width: 72%; max-width: 100; max-height: 58%; }
    _AddPinScreen TextArea { height: 1fr; min-height: 6; margin-bottom: 1; }
    """

    BINDINGS = [
        Binding("escape", "cancel", "Cancel", show=True),
        Binding("ctrl+s", "submit", "Append", show=True),
    ]

    def compose(self) -> ComposeResult:
        with CenterMiddle():
            with Vertical(id="modal"):
                yield Static("➕  Append Pinned Context", id="modal_title")
                yield Static(
                    f"Appended to every system prompt · saved in [{ui.FG_DIM}]{PIN_FILE}[/]",
                    id="modal_status",
                )
                yield TextArea("", id="pin_input", show_line_numbers=False)
                yield Static(
                    f"[{ui.ACCENT_3}]ctrl+s[/] append   [{ui.ACCENT_3}]esc[/] cancel",
                    id="modal_hint",
                )

    def on_mount(self) -> None:
        self.query_one("#pin_input", TextArea).focus()

    def action_submit(self) -> None:
        text = self.query_one("#pin_input", TextArea).text.strip()
        if not text:
            return
        self.dismiss(text)

    def action_cancel(self) -> None:
        self.dismiss(None)


class _ConfirmClearPinScreen(TuiModalScreen[bool]):
    DEFAULT_CSS = TUI_MODAL_CHROME_CSS + """
    _ConfirmClearPinScreen #modal { width: 52%; max-width: 70; max-height: 32%; }
    _ConfirmClearPinScreen #confirm_prompt { padding: 0 1; color: {ui.FG}; margin-bottom: 1; }
    """

    BINDINGS = [Binding("escape", "cancel", "Cancel", show=True)]

    def compose(self) -> ComposeResult:
        with CenterMiddle():
            with Vertical(id="modal"):
                yield Static("⚠  Clear Pinned Context?", id="modal_title")
                yield Static(
                    f"Type [bold {ui.WARN}]yes[/] to remove all pinned instructions.",
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


class PinModalScreen(TuiModalScreen[None]):
    DEFAULT_CSS = TUI_MODAL_CHROME_CSS + """
    PinModalScreen #modal { width: 80%; max-width: 120; max-height: 84%; }
    PinModalScreen ScrollableContainer {
        height: 1fr;
        min-height: 14;
        margin-top: 1;
        border: round {ui.BORDER};
        padding: 0 1;
    }
    PinModalScreen #pin_preview { width: 100%; }
    PinModalScreen #modal_meta {
        color: {ui.FG_DIM};
        padding: 0 1;
        margin-top: 1;
    }
    """

    BINDINGS = [
        Binding("escape", "dismiss_cancel", "Close", show=True),
        Binding("a", "add", "Add", show=True),
        Binding("t", "toggle", "Toggle", show=True),
        Binding("c", "clear", "Clear", show=True),
        Binding("r", "refresh", "Refresh", show=True),
    ]

    def compose(self) -> ComposeResult:
        with CenterMiddle():
            with Vertical(id="modal"):
                yield Static("📌  Pinned Context", id="modal_title")
                yield Static("", id="modal_status")
                with ScrollableContainer():
                    yield Static("", id="pin_preview")
                yield Static("", id="modal_meta")
                yield Static(
                    f"[{ui.ACCENT_3}]a[/] append   [{ui.ACCENT_3}]t[/] enable/disable   "
                    f"[{ui.ACCENT_3}]c[/] clear   [{ui.ACCENT_3}]r[/] refresh   "
                    f"[{ui.ACCENT_3}]esc[/] close",
                    id="modal_hint",
                )

    def on_mount(self) -> None:
        enable_mouse()
        self._refresh()

    def on_unmount(self) -> None:
        disable_mouse()

    def _preview_markup(self) -> Text:
        rows = pin_store.preview_lines()
        if not rows:
            return Text.assemble(
                ("  No pinned context yet.\n\n", f"italic {ui.FG_DIM}"),
                ("  Press ", ui.FG_DIM),
                ("a", ui.ACCENT_3),
                (" to append standing instructions the agent should always remember.", ui.FG_DIM),
            )
        body = Text()
        if not pin_store.is_enabled():
            body.append("  Injection paused — text is saved but not sent to the model.\n\n", style=f"italic {ui.WARN}")
        for line_no, content in rows:
            body.append(f"{line_no:>3}  ", style=ui.FG_DIM)
            body.append(_ellipsis(content, 96) + "\n", style=ui.FG)
        return body

    def _refresh(self) -> None:
        text = pin_store.pin_text()
        enabled = pin_store.is_enabled()
        lines, chars = pin_store.pin_stats(text)
        try:
            title = "📌  Pinned Context"
            if text:
                noun = "lines" if lines != 1 else "line"
                title += f"   [{ui.FG_DIM}]{lines} {noun} · {chars} chars[/]"
            self.query_one("#modal_title", Static).update(title)
            self.query_one("#pin_preview", Static).update(self._preview_markup())
            inject_label = "Injected into every system prompt" if enabled else "Saved locally — injection paused"
            self.query_one("#modal_meta", Static).update(
                f"[{ui.FG_DIM}]{inject_label} · {PIN_FILE}[/]"
            )
            status = self.query_one("#modal_status", Static)
            if text:
                first = text.splitlines()[0]
                state_word = f"[{ui.OK}]enabled[/]" if enabled else f"[{ui.WARN}]paused[/]"
                status.update(
                    f"{state_word} [{ui.FG_DIM}]preview:[/] [{ui.ACCENT_2}]{_ellipsis(first, 64)}[/]"
                )
            else:
                status.update(f"[{ui.FG_DIM}]nothing pinned[/]")
        except Exception:
            pass

    def _notify(self, msg: str, *, error: bool = False) -> None:
        try:
            color = ui.ERR if error else ui.OK
            self.query_one("#modal_status", Static).update(f"[{color}]{msg}[/]")
        except Exception:
            pass

    def action_refresh(self) -> None:
        self._refresh()

    def action_toggle(self) -> None:
        on = pin_store.toggle_enabled()
        if on:
            self._notify("injection enabled")
        else:
            self._notify("injection paused — text kept")
        self._refresh()

    def action_add(self) -> None:
        def after(text: str | None) -> None:
            if not text:
                return
            lines, chars = pin_store.append_pin(text)
            self._notify(f"appended · {lines} lines · {chars} chars")
            self._refresh()

        self.app.push_screen(_AddPinScreen(), after)

    def action_clear(self) -> None:
        if not pin_store.pin_text():
            self._notify("already empty", error=True)
            return

        def after(confirmed: bool) -> None:
            if not confirmed:
                self._notify("clear cancelled")
                return
            pin_store.clear_pin()
            self._notify("cleared")
            self._refresh()

        self.app.push_screen(_ConfirmClearPinScreen(), after)

    def action_dismiss_cancel(self) -> None:
        self.dismiss(None)
