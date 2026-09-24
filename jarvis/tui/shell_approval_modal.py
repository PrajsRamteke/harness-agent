"""Modal to approve shell commands (run_bash) in the TUI — same as Rich REPL Y/n/a.

Layout::

    ▍ shell  Run this command?                              esc
    ▍ ┌──────────────────────────────────────────────────────┐
    ▍ │ $ rm -rf build/ && npm run build                     │
    ▍ └──────────────────────────────────────────────────────┘
    ▍ in ~/code/project                               3 lines
    ▍ ⚠ Careful — deletes files
    ▍
    ▍ ❯ Yes      run this command                          y
    ▍   Always   allow every command this session          a
    ▍   No       don't run it, tell the model              n
    ▍
    ▍ ↑↓ select   ↵ confirm   esc deny

The command card scrolls (wheel / pgup·pgdn) instead of truncating, so what
gets approved is always fully visible.
"""
from __future__ import annotations

import os
import re

from rich.console import Console, ConsoleOptions, RenderResult
from rich.syntax import Syntax
from rich.table import Table
from rich.text import Text
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import CenterMiddle, Vertical, VerticalScroll
from textual.message import Message
from textual.widgets import Static

from ..constants import CWD
from .modal_chrome import TUI_MODAL_CHROME_CSS, TuiModalScreen, hint_line
from .mouse_toggle import enable_mouse, disable_mouse
from . import theme as ui


_MAX_CMD_CHARS = 16000

# (result, label, detail, key, tone) — tone names a theme token.
_CHOICES: list[tuple[str, str, str, str, str]] = [
    ("y", "Yes", "run this command", "y", "OK"),
    ("a", "Always", "allow every command this session", "a", "WARN"),
    ("n", "No", "don't run it, tell the model", "n", "ERR"),
]

# Display-only hints: flag commands worth a second look before approving.
_RISKS: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"(?<![\w-])rm\s"), "deletes files"),
    (re.compile(r"\bsudo\b"), "runs as root"),
    (re.compile(r"\bgit\s+push\b[^;&|]*\s(?:--force(?:-with-lease)?|-f)\b"), "force-pushes"),
    (re.compile(r"\bgit\s+push\b"), "pushes to a remote"),
    (re.compile(
        r"\bgit\s+(?:reset\s+--hard|clean\s+-\S*f|checkout\s+--\s|restore\b|"
        r"stash\s+(?:drop|clear)|branch\s+-D)"
    ), "discards git changes"),
    (re.compile(r"\b(?:curl|wget)\b[^|;&]*\|\s*(?:sudo\s+)?(?:ba|z)?sh\b"),
     "pipes a download into a shell"),
    (re.compile(r"\bch(?:mod|own)\s+-R\b"), "changes permissions recursively"),
    (re.compile(r"\bmkfs\b|\bdd\s+if=|>\s*/dev/(?:sd|disk|nvme)"), "writes to a disk device"),
    (re.compile(r"\b(?:kill|pkill|killall)\b"), "kills processes"),
    (re.compile(r"\b(?:DROP|TRUNCATE)\s+(?:TABLE|DATABASE|SCHEMA)\b", re.IGNORECASE), "drops data"),
]


def command_risks(cmd: str) -> list[str]:
    """Short reasons ``cmd`` deserves a second look (empty when it looks routine)."""
    found = [label for pattern, label in _RISKS if pattern.search(cmd or "")]
    if "force-pushes" in found:
        found.remove("pushes to a remote")
    return found


def _short_path(path: str) -> str:
    home = os.path.expanduser("~")
    if path == home or path.startswith(home + os.sep):
        return "~" + path[len(home):]
    return path


class _LeftTruncated:
    """Single-line text that drops its *start* when it doesn't fit (paths)."""

    def __init__(self, text: str, style: str) -> None:
        self.text = text
        self.style = style

    def __rich_console__(self, console: Console, options: ConsoleOptions) -> RenderResult:
        text, width = self.text, options.max_width
        if len(text) > width:
            text = "…" + text[-(width - 1):] if width > 1 else "…"
        yield Text(text, style=self.style, no_wrap=True, overflow="crop")


class ApprovalChoice(Static):
    """One answer row; the screen owns which row is active."""

    class Picked(Message):
        def __init__(self, result: str) -> None:
            super().__init__()
            self.result = result

    def __init__(self, result: str, label: str, detail: str, key: str, tone: str) -> None:
        super().__init__(classes="choice", id=f"choice_{result}")
        self.result = result
        self._label = label
        self._detail = detail
        self._key = key
        self._tone = tone

    @property
    def tone(self) -> str:
        return getattr(ui, self._tone)

    def paint(self, active: bool) -> None:
        tone = self.tone
        grid = Table.grid(expand=True, padding=0)
        grid.add_column(width=2, no_wrap=True)
        grid.add_column(width=9, no_wrap=True)
        grid.add_column(ratio=1, no_wrap=True, overflow="ellipsis")
        grid.add_column(no_wrap=True, justify="right")
        grid.add_row(
            Text("❯ " if active else "  ", style=f"bold {tone}"),
            Text(self._label, style=f"bold {tone}" if active else ui.FG),
            Text(self._detail, style=ui.FG_MUTE if active else ui.FG_DIM),
            Text(f" {self._key} ", style=(
                f"bold {ui.BG_0} on {tone}" if active
                else f"bold {tone} on {ui.blend(ui.BG_1, tone, 0.18)}"
            )),
        )
        self.update(grid)
        if active:
            self.styles.background = ui.blend(ui.BG_1, tone, 0.16)
        else:
            self.styles.clear_rule("background")

    def on_click(self) -> None:
        self.post_message(self.Picked(self.result))


class ShellApprovalScreen(TuiModalScreen[str]):
    """User picks run (Y), deny (N), or always approve (A). Dismisses with y/n/a."""

    DEFAULT_CSS = TUI_MODAL_CHROME_CSS + """
    ShellApprovalScreen #modal {
        width: 80;
        max-width: 95%;
        height: auto;
        border: none;
        border-left: outer {ui.WARN};
    }
    ShellApprovalScreen #cmd_card {
        background: {ui.BG_2};
        padding: 1 2;
        width: 100%;
        height: auto;
        max-height: 16;
        scrollbar-background: {ui.BG_2};
        scrollbar-color: {ui.BG_4};
        scrollbar-color-hover: {ui.BORDER};
        scrollbar-color-active: {ui.ACCENT};
        scrollbar-size-vertical: 1;
    }
    ShellApprovalScreen #cmd_text {
        width: 100%;
        height: auto;
    }
    ShellApprovalScreen #cmd_meta {
        padding: 0 1;
        width: 100%;
        height: 1;
    }
    ShellApprovalScreen #cmd_risk {
        padding: 0 1;
        width: 100%;
        height: auto;
    }
    ShellApprovalScreen #choices {
        margin-top: 1;
        width: 100%;
        height: auto;
    }
    ShellApprovalScreen .choice {
        height: 1;
        width: 100%;
        padding: 0 1;
    }
    ShellApprovalScreen .choice:hover {
        background: {ui.BG_3};
    }
    """

    BINDINGS = [
        Binding("y,Y", "pick('y')", "Run", show=False),
        Binding("a,A", "pick('a')", "Always", show=False),
        Binding("n,N,escape", "pick('n')", "Cancel", show=False),
        Binding("up,k", "move(-1)", "Up", show=False),
        Binding("down,j", "move(1)", "Down", show=False),
        Binding("enter", "confirm", "Confirm", show=False),
        Binding("pageup", "scroll_cmd(-1)", show=False),
        Binding("pagedown", "scroll_cmd(1)", show=False),
    ]

    # Modal width adapts to the command: never narrower than the choice rows,
    # never wider than 100 cells (CSS max-width caps it at 95% of the screen).
    _MIN_WIDTH = 66
    _MAX_WIDTH = 100

    def __init__(self, cmd: str) -> None:
        super().__init__()
        full = (cmd or "").strip()
        self._cmd = full[:_MAX_CMD_CHARS]
        self._cut = len(full) - len(self._cmd)
        self._risks = command_risks(full)
        self._index = 0
        longest = max((len(l) for l in self._cmd.splitlines()), default=0)
        # frame border+padding (5) + card padding (4) + "$ " (2) + scrollbar (1) + slack
        self._width = max(self._MIN_WIDTH, min(self._MAX_WIDTH, longest + 14))

    @property
    def _tone(self) -> str:
        return ui.ERR if self._risks else ui.WARN

    def _title(self) -> Text:
        return Text.assemble(
            (" shell ", f"bold {ui.BG_0} on {self._tone}"),
            ("  Run this command?", f"bold {ui.FG}"),
        )

    def _cmd_renderable(self) -> Table:
        syntax = Syntax(
            self._cmd or " ",
            "bash",
            theme="ansi_dark",
            word_wrap=True,
            background_color=ui.BG_2,
        )
        grid = Table.grid(padding=0)
        grid.add_column(width=2, no_wrap=True)
        grid.add_column(ratio=1)
        grid.add_row(Text("$", style=f"bold {self._tone}"), syntax)
        if self._cut:
            grid.add_row("", Text(f"… {self._cut:,} more characters not shown", style=ui.FG_DIM))
        return grid

    def _meta(self) -> Table:
        lines = len(self._cmd.splitlines())
        grid = Table.grid(expand=True, padding=0)
        grid.add_column(width=3, no_wrap=True)
        grid.add_column(ratio=1, no_wrap=True)
        grid.add_column(no_wrap=True, justify="right")
        grid.add_row(
            Text("in ", style=ui.FG_DIM),
            _LeftTruncated(_short_path(str(CWD)), ui.FG_MUTE),
            Text(f"  {lines} lines" if lines > 1 else "", style=ui.FG_DIM),
        )
        return grid

    def _risk_line(self) -> Text:
        return Text.assemble(
            ("⚠ ", f"bold {ui.ERR}"),
            ("Careful — ", f"bold {ui.ERR}"),
            (", ".join(self._risks), ui.FG_MUTE),
        )

    def _hint(self, scrollable: bool = False) -> str:
        # The y / a / n shortcuts are on the rows themselves.
        pairs = [("↑↓", "select"), ("↵", "confirm"), ("esc", "deny")]
        if scrollable:
            pairs.insert(2, ("pgup/dn", "scroll"))
        return hint_line(*pairs)

    def compose(self) -> ComposeResult:
        with CenterMiddle():
            with Vertical(id="modal"):
                yield Static(self._title(), id="modal_title")
                with VerticalScroll(id="cmd_card", can_focus=False):
                    yield Static(self._cmd_renderable(), id="cmd_text")
                yield Static(self._meta(), id="cmd_meta")
                if self._risks:
                    yield Static(self._risk_line(), id="cmd_risk")
                with Vertical(id="choices"):
                    for choice in _CHOICES:
                        yield ApprovalChoice(*choice)
                yield Static(self._hint(), id="modal_hint")

    def on_mount(self) -> None:
        enable_mouse()
        modal = self.query_one("#modal")
        modal.styles.width = self._width
        modal.styles.border_left = ("outer", self._tone)
        self._paint_choices()
        self.call_after_refresh(self._sync_scroll_hint)

    def on_unmount(self) -> None:
        disable_mouse()

    def _sync_scroll_hint(self) -> None:
        card = self.query_one("#cmd_card", VerticalScroll)
        if card.max_scroll_y > 0:
            self.query_one("#modal_hint", Static).update(self._hint(scrollable=True))

    def _paint_choices(self) -> None:
        for i, row in enumerate(self.query(ApprovalChoice)):
            row.paint(i == self._index)

    def action_move(self, delta: int) -> None:
        self._index = (self._index + delta) % len(_CHOICES)
        self._paint_choices()

    def action_confirm(self) -> None:
        self.dismiss(_CHOICES[self._index][0])

    def action_pick(self, result: str) -> None:
        self.dismiss(result if result in ("y", "n", "a") else "n")

    def action_scroll_cmd(self, direction: int) -> None:
        card = self.query_one("#cmd_card", VerticalScroll)
        if direction < 0:
            card.scroll_page_up(animate=False)
        else:
            card.scroll_page_down(animate=False)

    def on_approval_choice_picked(self, event: ApprovalChoice.Picked) -> None:
        event.stop()
        self.action_pick(event.result)
