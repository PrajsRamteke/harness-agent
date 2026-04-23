"""Textual TUI app for Jarvis.

Layout (OpenCode-inspired):

    ┌───────────────── header (title + model/session) ─────────────────┐
    │                                                                   │
    │                          transcript (RichLog)                     │
    │                                                                   │
    ├──────────────────────── status line ──────────────────────────────┤
    │ ❯ input                                                           │
    └───────────────────────────────────────────────────────────────────┘
"""
from __future__ import annotations

import sys
import threading

from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Vertical
from textual.message import Message
from textual.widgets import Header, Footer, Input, RichLog, Static, TextArea
from textual import work

from rich.panel import Panel
from rich.text import Text

from .console_shim import TUIConsole
from .session_modal import SessionPickerScreen, resume_session_into_state
from .palette_modal import CommandPaletteScreen


class PromptArea(TextArea):
    """Multi-line prompt input.

    Enter submits. Shift+Enter / Alt+Enter / Ctrl+J insert a newline.
    Pasting multi-line text is supported natively by TextArea.
    """

    class Submitted(Message):
        def __init__(self, value: str) -> None:
            self.value = value
            super().__init__()

    async def _on_key(self, event):  # type: ignore[override]
        key = event.key
        if key == "enter":
            event.stop()
            event.prevent_default()
            self.post_message(self.Submitted(self.text))
            return
        if key in ("shift+enter", "alt+enter", "ctrl+j"):
            event.stop()
            event.prevent_default()
            self.insert("\n")
            return


def _swap_console_everywhere(tui_console):
    """Replace every `console` module attribute across loaded jarvis.* modules.

    `from ..console import console` creates per-module bindings — reassigning
    `jarvis.console.console` alone would not update them. Walk all loaded
    submodules and swap the attribute where present.
    """
    import jarvis.console as _cmod
    _cmod.console = tui_console
    for name, mod in list(sys.modules.items()):
        if not name.startswith("jarvis.") or mod is None:
            continue
        if getattr(mod, "console", None) is not None and name != "jarvis.console":
            try:
                setattr(mod, "console", tui_console)
            except Exception:
                pass


class JarvisTUI(App):
    CSS = """
    Screen {
        background: #0b0d10;
    }
    #transcript {
        background: #0b0d10;
        color: #e6e6e6;
        padding: 1 2;
        border: none;
    }
    #prompt {
        dock: bottom;
        height: auto;
        min-height: 3;
        max-height: 12;
        margin: 0 2 2 2;
        background: #0f1216;
        border: tall #2b3340;
        padding: 0 1;
    }
    #statusbar {
        dock: bottom;
        height: 1;
        background: #12151a;
        color: #7aa2f7;
        padding: 0 2;
        margin: 0 2 0 2;
    }
    Input, TextArea {
        background: #0f1216;
        color: #e6e6e6;
    }
    TextArea > .text-area--cursor-line {
        background: #0f1216;
    }
    Header {
        background: #12151a;
        color: #bb9af7;
    }
    """

    BINDINGS = [
        Binding("ctrl+d", "quit", "Quit", show=True),
        Binding("ctrl+c", "cancel_or_quit", "Cancel/Quit", show=True),
        Binding("escape", "escape_action", show=False),
    ]

    def __init__(self):
        super().__init__()
        self._busy = False
        self._cancel_flag = threading.Event()
        self._last_input_value = ""

    def compose(self) -> ComposeResult:
        yield Header(show_clock=True)
        yield RichLog(id="transcript", wrap=True, highlight=True, markup=True, auto_scroll=True)
        yield PromptArea(id="prompt")
        yield Static("", id="statusbar")
        yield Footer()

    # ─── lifecycle ─────────────────────────────────────────────────────
    def on_mount(self):
        self.title = "Jarvis"
        self.sub_title = "Claude Code-style macOS agent"

        log = self.query_one("#transcript", RichLog)
        status = self.query_one("#statusbar", Static)

        # Install the shim BEFORE touching any jarvis.repl/* code so their
        # module-local `console` names are rebound to the TUI console.
        tui_console = TUIConsole(self, log, status)
        _swap_console_everywhere(tui_console)
        self._tui_console = tui_console

        # Now it is safe to import the parts that log to `console`.
        from ..auth.client import make_client
        from ..storage.sessions import db_init, db_create_session
        from ..repl.banners import welcome_banner, header_panel
        from .. import state

        state.client = make_client()
        welcome_banner()
        header_panel()
        db_init()
        state.current_session_id = db_create_session(state.MODEL)

        self.query_one("#prompt", PromptArea).focus()

    # ─── palette (centered modal) ──────────────────────────────────────
    def on_text_area_changed(self, event: TextArea.Changed) -> None:
        """Open palette only when '/' is TYPED into an empty prompt — not when
        arriving at '/' by deleting the rest of a previously-selected command.
        """
        if event.text_area.id != "prompt":
            return
        val = event.text_area.text or ""
        prev = self._last_input_value
        self._last_input_value = val
        if val == "/" and prev == "":
            event.text_area.clear()
            self._last_input_value = ""
            self._open_palette()

    def _open_palette(self):
        def after(cmd):
            inp = self.query_one("#prompt", PromptArea)
            if cmd:
                inp.text = cmd
                inp.move_cursor((0, len(cmd)))
                self._last_input_value = cmd
            inp.focus()
        self.push_screen(CommandPaletteScreen(), after)

    def action_escape_action(self):
        """Esc when no modal is open: cancel any in-flight AI turn."""
        if self._busy:
            from ..repl.stream import cancel_current_stream
            if cancel_current_stream():
                self._set_status("cancelled")
                self._tui_console.print("[yellow]⏹ cancelled by user[/]")

    # ─── input handling ────────────────────────────────────────────────
    def on_prompt_area_submitted(self, event: "PromptArea.Submitted") -> None:
        if self._busy:
            return
        raw = event.value or ""
        text = raw.strip()
        inp = self.query_one("#prompt", PromptArea)
        inp.clear()
        self._last_input_value = ""
        if not text:
            return

        log = self.query_one("#transcript", RichLog)
        log.write(Panel(Text(text), title="you", title_align="left",
                        border_style="green", padding=(0, 1)))

        # /exit shortcut
        if text.strip() in ("/exit", "/quit"):
            self.exit()
            return

        # /session (bare) or /session list/ls → modal picker
        stripped = text.strip()
        if stripped in ("/session", "/sessions", "/session list", "/session ls"):
            self._open_session_picker()
            return

        self._busy = True
        self._set_status("thinking…")
        self._run_turn(text)

    # ─── session picker ────────────────────────────────────────────────
    def _open_session_picker(self):
        def after(sid):
            if sid is None:
                self._tui_console.print("[dim]cancelled[/]")
                return
            resume_session_into_state(sid, self._tui_console.print)
        self.push_screen(SessionPickerScreen(), after)

    @work(thread=True, exclusive=True)
    def _run_turn(self, inp: str) -> None:
        """Mirror of jarvis.main._send_and_loop, adapted for the TUI."""
        from ..commands.dispatch import handle_slash
        from ..repl.stream import call_claude_stream
        from ..repl.render import render_assistant
        from ..storage.sessions import db_append_message, db_set_title_if_empty
        from .. import state

        try:
            # alias expansion
            if inp.startswith("/"):
                head = inp.split(maxsplit=1)[0]
                if head[1:] in state.aliases:
                    rest = inp[len(head):]
                    inp = state.aliases[head[1:]] + rest

            if inp.startswith("/"):
                result, should_send, inp = handle_slash(inp)
                if result == "exit":
                    self.call_from_thread(self.exit)
                    return
                if not should_send:
                    return

            user_msg = {"role": "user", "content": inp}
            state.messages.append(user_msg)
            state.web_tool_used_this_turn = False
            if state.current_session_id:
                db_append_message(state.current_session_id, len(state.messages) - 1, user_msg)
                db_set_title_if_empty(state.current_session_id, inp)

            while True:
                resp = call_claude_stream()
                asst_msg = {"role": "assistant", "content": resp.content}
                state.messages.append(asst_msg)
                if state.current_session_id:
                    db_append_message(state.current_session_id, len(state.messages) - 1, asst_msg)
                more = render_assistant(resp)
                if resp.stop_reason == "end_turn" or not more:
                    break
                if state.current_session_id and state.messages and state.messages[-1] is not asst_msg:
                    db_append_message(state.current_session_id, len(state.messages) - 1, state.messages[-1])
        except Exception as e:  # surface errors in the transcript, don't crash the app
            self._tui_console.print(f"[red]error: {type(e).__name__}: {e}[/]")
        finally:
            self.call_from_thread(self._turn_done)

    def _turn_done(self):
        self._busy = False
        self._set_status("")
        self.query_one("#prompt", PromptArea).focus()

    def _set_status(self, msg: str):
        try:
            self.query_one("#statusbar", Static).update(msg)
        except Exception:
            pass

    def action_cancel_or_quit(self):
        if self._busy:
            from ..repl.stream import cancel_current_stream
            cancel_current_stream()
            self._set_status("cancelling…")
        else:
            self.exit()


def _escape(s: str) -> str:
    return s.replace("[", r"\[")


def run():
    JarvisTUI().run()
