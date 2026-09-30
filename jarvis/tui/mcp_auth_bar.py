"""Sign-in bar — a slim row above the composer while an MCP server needs its sign-in.

    ◈ linear needs sign-in                  [ Authenticate ⌃O ] [ Copy link ] [ Paste address ]  ✕
    ⠋ waiting for the browser… (linear)     [ Open again ⌃O ]   [ Copy link ] [ Paste address ] [ Cancel ]

Pure view: ``JarvisTUI`` (``mixins/mcp_auth.py``) decides what to show and
handles the actions; the buttons here just run them.
"""
from __future__ import annotations

from rich.text import Text
from textual.app import ComposeResult
from textual.containers import Horizontal
from textual.widgets import Button, Static

from . import theme as ui

# button id → app action
_ACTIONS = {
    "mcp_auth_go": "mcp_auth_start",
    "mcp_auth_copy": "mcp_auth_copy",
    "mcp_auth_paste": "mcp_auth_paste",
    "mcp_auth_cancel": "mcp_auth_cancel",
    "mcp_auth_dismiss": "mcp_auth_dismiss",
}


class McpAuthBar(Horizontal):
    DEFAULT_CSS = """
    McpAuthBar {
        height: 1;
        width: 100%;
        margin: 1 0 0 0;
        padding: 0 1 0 0;
        background: $jv-bg-1;
        border-left: outer $jv-warn;
    }
    McpAuthBar.hidden {
        display: none;
    }
    McpAuthBar #mcp_auth_msg {
        width: 1fr;
        height: 1;
        padding: 0 1;
        color: $jv-fg;
        text-wrap: nowrap;
        text-overflow: ellipsis;
        background: transparent;
    }
    McpAuthBar Button {
        height: 1;
        min-width: 0;
        width: auto;
        margin: 0 0 0 1;
        padding: 0 1;
        border: none;
        background: $jv-bg-4;
        color: $jv-fg;
        text-style: none;
    }
    McpAuthBar Button:hover, McpAuthBar Button:focus {
        background: $jv-accent;
        color: $jv-bg-0;
        text-style: bold;
    }
    McpAuthBar Button.-primary {
        background: $jv-accent;
        color: $jv-bg-0;
        text-style: bold;
    }
    McpAuthBar Button.-primary:hover, McpAuthBar Button.-primary:focus {
        background: $jv-accent-2;
    }
    McpAuthBar Button.-quiet {
        background: transparent;
        color: $jv-fg-dim;
        min-width: 3;
    }
    McpAuthBar Button.-quiet:hover {
        background: $jv-bg-3;
        color: $jv-fg;
    }
    McpAuthBar Button.-off {
        display: none;
    }
    """

    can_focus = False

    def __init__(self, *, id: str | None = None, classes: str | None = None) -> None:
        super().__init__(id=id, classes=(classes + " hidden") if classes else "hidden")
        self._frame = 0
        self._name = ""
        self._extra = 0
        self._waiting = False
        self._timer = None
        self._msg = ""

    def compose(self) -> ComposeResult:
        yield Static("", id="mcp_auth_msg", markup=False)
        yield Button("Authenticate  ⌃O", id="mcp_auth_go", classes="-primary", compact=True)
        yield Button("Copy link", id="mcp_auth_copy", compact=True)
        yield Button("Paste address", id="mcp_auth_paste", compact=True)
        yield Button("Cancel", id="mcp_auth_cancel", compact=True)
        yield Button("✕", id="mcp_auth_dismiss", classes="-quiet", compact=True)

    # ── state ────────────────────────────────────────────────────────────

    def show_state(self, *, name: str, extra: int, waiting: bool, has_link: bool) -> None:
        """``name`` needs sign-in (``extra`` more too). ``waiting``: the browser was opened."""
        self._name, self._extra, self._waiting = name, extra, waiting
        self.remove_class("hidden")
        go = self.query_one("#mcp_auth_go", Button)
        go.label = "Open again  ⌃O" if waiting else "Authenticate  ⌃O"
        go.set_class(waiting, "-quiet")
        go.set_class(not waiting, "-primary")
        self.query_one("#mcp_auth_copy", Button).set_class(not has_link, "-off")
        self.query_one("#mcp_auth_paste", Button).set_class(not has_link, "-off")
        self.query_one("#mcp_auth_cancel", Button).set_class(not waiting, "-off")
        if waiting and self._timer is None:
            self._timer = self.set_interval(0.12, self._tick)
        elif not waiting and self._timer is not None:
            self._timer.stop()
            self._timer = None
        self._paint()

    def hide(self) -> None:
        self.add_class("hidden")
        if self._timer is not None:
            self._timer.stop()
            self._timer = None

    @property
    def shown(self) -> bool:
        return not self.has_class("hidden")

    def message_text(self) -> str:
        return self._msg if self.shown else ""

    def _tick(self) -> None:
        self._frame += 1
        self._paint()

    def _paint(self) -> None:
        t = Text(no_wrap=True, overflow="ellipsis")
        if self._waiting:
            frames = ui.SPINNER_FRAMES
            t.append(f"{frames[self._frame % len(frames)]} ", style=ui.ACCENT)
            t.append("waiting for the browser… ", style=ui.FG)
            t.append(f"({self._name})", style=ui.FG_MUTE)
        else:
            t.append("◈ ", style=ui.WARN)
            t.append(self._name, style=f"bold {ui.FG}")
            t.append(" needs sign-in", style=ui.FG_MUTE)
        if self._extra:
            t.append(f"  +{self._extra} more", style=ui.FG_DIM)
        self._msg = t.plain
        try:
            self.query_one("#mcp_auth_msg", Static).update(t)
        except Exception:
            pass

    async def on_button_pressed(self, event: Button.Pressed) -> None:
        action = _ACTIONS.get(event.button.id or "")
        if action:
            event.stop()
            await self.app.run_action(action)
