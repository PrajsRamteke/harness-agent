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
  ↵              send message (queued if Jarvis is busy)
  ⇧↵  ⌃J  ⌃N     new line in prompt
  ↑ ↓            prompt history (first / last line of the prompt)
  esc            interrupt the running turn · close a popup
  ⌃C             interrupt · clear the prompt · press twice to quit
  !cmd           run a shell command directly (no model)

[bold]Commands & files[/]
  /              command list — type to filter, ⇥ complete, ↵ run
  ⌃P             command palette (searchable dialog)
  @              attach a file — type to search, ⇥/↵ insert
  drag-drop      drop media/docs into the prompt → [image 1], …

[bold]Agents & modes[/]
  ⇥              cycle agent (empty prompt)
  ⇧⇥             toggle plan mode (read-only research)
  /model         switch model · /think effort · /theme colors

[bold]Transcript[/]
  mouse wheel    scroll · select text to copy it
  pgup pgdn      scroll by page · ⇧↑ ⇧↓ by lines
  home end       top / bottom when the prompt is empty
  ⌃Home ⌃End     top / bottom (always) — end re-follows new output
  ⌃Y             copy last reply (clean text) · /copy code · /copy all

[bold]Panels[/]
  ⌃T  F2         trace — show thinking + tool output previews
  ⌃F  F3         tools inspector — full file reads and command output
  ⌃B             toggle the session sidebar (auto on wide terminals)
  ?  F1          this help · ⌃D quit

[bold]Jarvis the pet[/]
  click kitty    pet Jarvis · double-click for the pet card (/pet)
  /pet feed      snack time · /pet play · /pet nap · /pet off hides it

[bold]Tips[/]
  • Click a "Thought for…" block to expand long reasoning.
  • HARNESS_MOUSE=0 (or settings ui.mouse=false) restores native
    terminal selection instead of in-app select-to-copy.
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
        margin-top: 0;
        border: none;
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
                    f"[bold {ui.FG_MUTE}]esc[/] or [bold {ui.FG_MUTE}]?[/] close",
                    id="modal_hint",
                )

    def on_mount(self) -> None:
        enable_mouse()

    def on_unmount(self) -> None:
        disable_mouse()

    def action_dismiss_cancel(self) -> None:
        self.dismiss(None)
