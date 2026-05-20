"""Modal to approve shell commands (run_bash) in the TUI — same as Rich REPL Y/n/a."""
from __future__ import annotations

from rich.panel import Panel
from rich.text import Text
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import CenterMiddle, Vertical
from textual.widgets import Static

from .modal_chrome import TUI_MODAL_CHROME_CSS, TuiModalScreen
from .mouse_toggle import enable_mouse, disable_mouse
from . import theme as ui


class ShellApprovalScreen(TuiModalScreen[str]):
    """User picks run (Y), deny (N), or always approve (A). Dismisses with y/n/a."""

    DEFAULT_CSS = TUI_MODAL_CHROME_CSS + """
    ShellApprovalScreen #modal {
        width: 86%;
        max-width: 120;
        height: auto;
        border: round {ui.WARN};
    }
    """

    BINDINGS = [
        Binding("y", "approve", "Run", show=False),
        Binding("Y", "approve", "Run", show=False),
        Binding("n", "deny", "Cancel", show=False),
        Binding("N", "deny", "Cancel", show=False),
        Binding("a", "always", "Always", show=False),
        Binding("A", "always", "Always", show=False),
        Binding("enter", "approve", "Run", show=False),
        Binding("escape", "deny", "Cancel", show=True),
    ]

    def __init__(self, cmd: str) -> None:
        super().__init__()
        self._cmd = (cmd or "").replace("\n", " ")[:4000]

    def compose(self) -> ComposeResult:
        with CenterMiddle():
            with Vertical(id="modal"):
                yield Static("⚡  Run Shell Command?", id="modal_title")
                yield Static(
                    Panel(
                        Text(self._cmd, overflow="fold"),
                        title="sh",
                        border_style=ui.FG_DIM,
                        padding=(0, 1),
                    ),
                )
                yield Static(
                    f"[{ui.OK}]y / ↵[/] run     [{ui.ERR}]n / esc[/] cancel     "
                    f"[{ui.WARN}]a[/] always (this session)",
                    id="modal_hint",
                )

    def on_mount(self) -> None:
        enable_mouse()

    def on_unmount(self) -> None:
        disable_mouse()

    def action_approve(self) -> None:
        self.dismiss("y")

    def action_deny(self) -> None:
        self.dismiss("n")

    def action_always(self) -> None:
        self.dismiss("a")
