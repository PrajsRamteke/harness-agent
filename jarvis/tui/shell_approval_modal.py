"""Modal to approve shell commands (run_bash) in the TUI — same as Rich REPL Y/n/a."""
from __future__ import annotations

import os

from rich.console import Group
from rich.syntax import Syntax
from rich.text import Text
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import CenterMiddle, Vertical
from textual.widgets import OptionList, Static
from textual.widgets.option_list import Option

from .modal_chrome import TUI_MODAL_CHROME_CSS, TuiModalScreen
from .mouse_toggle import enable_mouse, disable_mouse
from . import theme as ui


_MAX_CMD_CHARS = 4000
_MAX_CMD_LINES = 12


def _short_cwd() -> str:
    cwd = os.getcwd()
    home = os.path.expanduser("~")
    if cwd.startswith(home):
        cwd = "~" + cwd[len(home):]
    return cwd


class ShellApprovalScreen(TuiModalScreen[str]):
    """User picks run (Y), deny (N), or always approve (A). Dismisses with y/n/a."""

    DEFAULT_CSS = TUI_MODAL_CHROME_CSS + """
    ShellApprovalScreen #modal {
        width: 80;
        max-width: 95%;
        height: auto;
        border: round {ui.WARN};
    }
    ShellApprovalScreen #cmd_panel {
        background: {ui.BG_1};
        border-left: thick {ui.WARN};
        padding: 0 2;
        width: 100%;
    }
    ShellApprovalScreen #cmd_cwd {
        color: {ui.FG_DIM};
        padding: 0 1;
        margin-bottom: 1;
        width: 100%;
    }
    ShellApprovalScreen OptionList {
        height: auto;
        max-height: 5;
        width: 100%;
    }
    /* Textual 8 OptionList defaults win specificity ties against the shared
       chrome for the :focus state — restate them stronger here. */
    ShellApprovalScreen OptionList:focus {
        border: none;
        background-tint: {ui.BG_2} 0%;
    }
    ShellApprovalScreen OptionList:focus > .option-list--option-highlighted,
    ShellApprovalScreen OptionList > .option-list--option-highlighted {
        background: {ui.BG_4};
        color: #ffffff;
        text-style: none;
    }
    """

    BINDINGS = [
        Binding("y", "approve", "Run", show=False),
        Binding("Y", "approve", "Run", show=False),
        Binding("n", "deny", "Cancel", show=False),
        Binding("N", "deny", "Cancel", show=False),
        Binding("a", "always", "Always", show=False),
        Binding("A", "always", "Always", show=False),
        Binding("escape", "deny", "Cancel", show=True),
    ]

    _CHOICES: list[tuple[str, str, str, str, str]] = [
        # (result, glyph, label, description, key hint)
        ("y", ui.CHECK, "Run command", "execute it now", "y"),
        ("a", "∞", "Always allow", "don't ask again this session", "a"),
        ("n", "✗", "Don't run", "deny and tell the model", "n"),
    ]

    # Modal width adapts to the command: never narrower than the option rows,
    # never wider than 100 cells (CSS max-width caps it at 95% of the screen).
    _MIN_WIDTH = 66
    _MAX_WIDTH = 100

    def __init__(self, cmd: str) -> None:
        super().__init__()
        self._cmd = (cmd or "").strip()[:_MAX_CMD_CHARS]
        longest = max((len(l) for l in self._cmd.splitlines()), default=0)
        # +12 ≈ modal border/padding + command card border/padding
        self._width = max(self._MIN_WIDTH, min(self._MAX_WIDTH, longest + 12))

    def _cmd_renderable(self) -> Group:
        lines = self._cmd.splitlines() or [""]
        shown = "\n".join(lines[:_MAX_CMD_LINES])
        syntax = Syntax(
            shown,
            "bash",
            theme="ansi_dark",
            word_wrap=True,
            background_color=ui.BG_1,
        )
        parts: list = [syntax]
        if len(lines) > _MAX_CMD_LINES:
            parts.append(
                Text(f"… +{len(lines) - _MAX_CMD_LINES} more lines", style=ui.FG_DIM)
            )
        return Group(*parts)

    @staticmethod
    def _choice_style(result: str) -> str:
        return {"y": ui.OK, "a": ui.WARN, "n": ui.ERR}[result]

    def _choice_row(self, result: str, glyph: str, label: str, desc: str, key: str) -> Text:
        color = self._choice_style(result)
        return Text.assemble(
            (f" {glyph} ", color),
            (f"{label:<13s}", f"bold {color}"),
            ("  ", ""),
            (f"{desc:<29s}", ui.FG_MUTE),
            ("  ", ""),
            (f" {key} ", f"bold {ui.BG_0} on {color}"),
            (" ", ""),
        )

    def compose(self) -> ComposeResult:
        with CenterMiddle():
            with Vertical(id="modal"):
                yield Static(
                    Text.assemble(
                        ("⚡ ", ui.WARN),
                        ("Run shell command?", f"bold {ui.FG}"),
                    ),
                    id="modal_title",
                )
                yield Static(self._cmd_renderable(), id="cmd_panel")
                yield Static(
                    Text.assemble(("cwd ", ui.FG_DIM), (_short_cwd(), ui.FG_MUTE)),
                    id="cmd_cwd",
                )
                yield OptionList(id="approval_list")
                yield Static(
                    f"[{ui.ACCENT_3}]↑↓[/] choose   [{ui.ACCENT_3}]↵[/] confirm   "
                    f"[{ui.ACCENT_3}]esc[/] cancel   [{ui.FG_DIM}]or press a key chip[/]",
                    id="modal_hint",
                )

    def on_mount(self) -> None:
        enable_mouse()
        self.query_one("#modal").styles.width = self._width
        opts = self.query_one("#approval_list", OptionList)
        for result, glyph, label, desc, key in self._CHOICES:
            opts.add_option(Option(self._choice_row(result, glyph, label, desc, key), id=result))
        opts.highlighted = 0
        opts.focus()

    def on_unmount(self) -> None:
        disable_mouse()

    def on_option_list_option_selected(self, event: OptionList.OptionSelected) -> None:
        result = str(event.option.id or "n")
        self.dismiss(result if result in ("y", "n", "a") else "n")

    def action_approve(self) -> None:
        self.dismiss("y")

    def action_deny(self) -> None:
        self.dismiss("n")

    def action_always(self) -> None:
        self.dismiss("a")
