"""Keyboard shortcuts help overlay (? / F1)."""
from __future__ import annotations

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import CenterMiddle, ScrollableContainer, Vertical
from textual.widgets import Static

from .modal_chrome import TUI_MODAL_CHROME_CSS, TuiModalScreen
from .mouse_toggle import enable_mouse, disable_mouse
from . import theme as ui

_SHORTCUTS = """
[bold]Chat[/]
  ↵              send message
  ⇧↵  ⌃J         new line in prompt
  esc            cancel turn (when busy)
  ⌃C             copy selection / cancel

[bold]Navigation[/]
  /              command palette
  @              attach file (type @path in prompt)
  ⇥              cycle agent
  ↑↓             scroll transcript (or @file picker)

[bold]Tools & trace[/]
  ⌃F             tools inspector — files + shell/git output
  ⌃T             trace on/off — thinking + tool panels in chat
  /verbose       same as ⌃T

[bold]Session[/]
  ⌃D             quit
  ?  F1          this help
  F2             trace on/off (alternate)

[bold]Tips[/]
  • 2+ parallel file reads show one ⚡ summary in chat; use ⌃F for full text.
  • Trace changes apply to the transcript when the agent is idle.
  • trace.on is saved in ~/.config/harness-agent/settings.json
"""


class ShortcutsHelpScreen(TuiModalScreen[None]):
    DEFAULT_CSS = (
        TUI_MODAL_CHROME_CSS
        + """
    ShortcutsHelpScreen #modal {
        width: 78%;
        max-width: 100;
        max-height: 80%;
    }
    ShortcutsHelpScreen ScrollableContainer {
        height: 1fr;
        min-height: 14;
        margin-top: 1;
        border: round {ui.BORDER};
        padding: 0 1;
    }
    ShortcutsHelpScreen #shortcuts_body {
        width: 100%;
    }
    """
    )

    BINDINGS = [
        Binding("escape", "dismiss_cancel", "Close", show=True),
        Binding("question_mark", "dismiss_cancel", show=False),
        Binding("f1", "dismiss_cancel", show=False),
    ]

    def compose(self) -> ComposeResult:
        with CenterMiddle():
            with Vertical(id="modal"):
                yield Static("⌨  Keyboard shortcuts", id="modal_title")
                with ScrollableContainer():
                    yield Static(_SHORTCUTS.strip(), id="shortcuts_body", markup=True)
                yield Static(
                    f"[{ui.ACCENT_3}]esc[/] or [{ui.ACCENT_3}]?[/] close",
                    id="modal_hint",
                )

    def on_mount(self) -> None:
        enable_mouse()

    def on_unmount(self) -> None:
        disable_mouse()

    def action_dismiss_cancel(self) -> None:
        self.dismiss(None)
