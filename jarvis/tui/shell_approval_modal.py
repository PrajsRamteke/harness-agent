"""Modal to approve shell commands (run_bash) in the TUI — same as Rich REPL Y/n/a."""
from __future__ import annotations

from rich.panel import Panel
from rich.text import Text
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Vertical
from textual.screen import ModalScreen
from textual.widgets import Static

from .mouse_toggle import enable_mouse, disable_mouse


class ShellApprovalScreen(ModalScreen[str]):
    """User picks run (Y), deny (N), or always approve (A). Dismisses with y/n/a."""

    DEFAULT_CSS = """
    ShellApprovalScreen {
        align: center middle;
        background: rgba(0, 0, 0, 0.62);
    }
    ShellApprovalScreen > #sh_modal {
        width: 86%;
        max-width: 120;
        height: auto;
        background: #161b22;
        border: round #d29922;
        padding: 1 2;
    }
    ShellApprovalScreen #sh_title {
        color: #d29922;
        text-style: bold;
        padding: 0 1 1 1;
        border-bottom: hkey #21262d;
        margin-bottom: 1;
        width: 100%;
    }
    ShellApprovalScreen #sh_hint {
        color: #6e7681;
        padding: 1 1 0 1;
        border-top: hkey #21262d;
        margin-top: 1;
        width: 100%;
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
        with Vertical(id="sh_modal"):
            yield Static("⚡  Run Shell Command?", id="sh_title")
            yield Static(
                Panel(
                    Text(self._cmd, overflow="fold"),
                    title="sh",
                    border_style="dim",
                    padding=(0, 1),
                ),
            )
            yield Static(
                "[#3fb950]y / ↵[/] run     [#f85149]n / esc[/] cancel     "
                "[#d29922]a[/] always (this session)",
                id="sh_hint",
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
