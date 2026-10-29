"""OpenRouter usage dialog — what the key has spent and has left.

Opened from the sidebar's *OpenRouter* section, the footer's ``◎ $… left``
and the ``◎ usage`` tag / ``u`` on OpenRouter's row in ``/key``. The body is
``commands.usage.usage_body`` (the same numbers ``/usage`` prints); the
fetch runs on a worker, so the dialog opens at once with the last answer.
"""
from __future__ import annotations

from textual import work
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import CenterMiddle, Vertical, VerticalScroll
from textual.widgets import Static

from .modal_chrome import TUI_MODAL_CHROME_CSS, TuiModalScreen, hint_line
from .mouse_toggle import enable_mouse, disable_mouse
from . import theme as ui


class UsageScreen(TuiModalScreen[None]):
    DEFAULT_CSS = TUI_MODAL_CHROME_CSS + """
    UsageScreen #modal { width: 84; max-width: 96%; height: auto; max-height: 90%; }
    UsageScreen #usage_scroll { height: auto; max-height: 30; margin-top: 1; }
    UsageScreen #usage_body { height: auto; padding: 0 1; }
    """

    BINDINGS = [
        Binding("escape", "dismiss_cancel", "Close", show=True),
        Binding("r", "refresh_usage", "Refresh", show=True),
        Binding("q", "dismiss_cancel", show=False),
    ]

    def compose(self) -> ComposeResult:
        with CenterMiddle():
            with Vertical(id="modal"):
                yield Static("◎  OpenRouter usage", id="modal_title")
                yield Static("", id="modal_subtitle")
                with VerticalScroll(id="usage_scroll"):
                    yield Static("", id="usage_body")
                yield Static(hint_line(("r", "refresh"), ("esc", "close")), id="modal_hint")

    def on_mount(self) -> None:
        enable_mouse()
        from ..auth import openrouter_usage as oru

        self._paint(oru.public(kick=False, any_provider=True))
        self.action_refresh_usage()

    def on_unmount(self) -> None:
        disable_mouse()

    def _paint(self, info: dict | None, busy: bool = True) -> None:
        from ..commands.usage import usage_body

        info = info or {"status": "loading"}
        try:
            self.query_one("#usage_body", Static).update(usage_body(info))
            sub = f"[{ui.FG_DIM}]checking with OpenRouter…[/]" if busy else (
                f"[{ui.FG_MUTE}]Spend and limit for the saved key · account credit[/]")
            self.query_one("#modal_subtitle", Static).update(sub)
        except Exception:
            pass

    @work(thread=True, exclusive=True, group="openrouter-usage")
    def action_refresh_usage(self) -> None:
        from ..auth import openrouter_usage as oru

        self.app.call_from_thread(self._paint, oru.public(kick=False, any_provider=True), True)
        info = oru.refresh(wait=True)
        if not info:
            info = {"status": "error", "error": "No OpenRouter key is saved — add one with /key"}
        self.app.call_from_thread(self._paint, info, False)

    def action_dismiss_cancel(self) -> None:
        self.dismiss(None)
