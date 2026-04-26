"""Textual TUI app for Jarvis.

Layout (OpenCode-inspired):

    ┌───────────────── header (title + model/session) ─────────────────┐
    │                                                                   │
    │                          transcript (RichLog)                     │
    │                                                                   │
    ├──────────────── activity (phase + clock) ─────────────────────────┤
    ├──────────────────────── status line ──────────────────────────────┤
    │ ❯ input                                                           │
    └───────────────────────────────────────────────────────────────────┘
"""
from __future__ import annotations

import sys
import threading
import time

from textual.actions import SkipAction
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.screen import ModalScreen
from textual.message import Message
from textual.widgets import Header, RichLog, Static, TextArea
from textual import work

from rich.markdown import Markdown
from rich.panel import Panel
from rich.text import Text

from .console_shim import TUIConsole
from .session_modal import SessionPickerScreen, resume_session_into_state
from .palette_modal import CommandPaletteScreen
from .model_modal import ModelPickerScreen


def _is_bare_model_command(text: str) -> bool:
    s = (text or "").strip()
    if not s.startswith("/model"):
        return False
    parts = s.split(maxsplit=1)
    return len(parts) == 1


def _is_session_picker_command(text: str) -> bool:
    s = (text or "").strip()
    return s in ("/session", "/sessions", "/session list", "/session ls")


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
    ENABLE_COMMAND_PALETTE = False
    CSS = """
    Screen {
        background: #0b0d10;
        layers: base overlay;
    }
    #main {
        height: 100%;
        width: 100%;
    }
    #transcript {
        background: #0b0d10;
        color: #e6e6e6;
        padding: 0 1;
        border: none;
        height: 1fr;
        min-height: 0;
        overflow-y: scroll;
        scrollbar-gutter: stable;
        scrollbar-color: #2b3340;
        scrollbar-background: #0b0d10;
    }
    #prompt {
        height: auto;
        min-height: 3;
        max-height: 12;
        margin: 0 1 1 1;
        background: #0f1216;
        border: tall #2b3340;
        padding: 0 1;
    }
    #activity_row {
        height: 1;
        background: #141820;
        margin: 0 1 0 1;
        padding: 0 1;
    }
    #activity_phase {
        width: 1fr;
        min-width: 0;
        color: #c0caf5;
    }
    #activity_clock {
        width: auto;
        color: #565f89;
    }
    #statusbar {
        height: 1;
        background: #12151a;
        color: #7aa2f7;
        padding: 0 1;
        margin: 0 1 0 1;
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
        Binding("f2", "toggle_internal", "Internals", show=True),
        Binding("ctrl+t", "toggle_internal", show=False),
        Binding("escape", "escape_action", show=False),
        Binding("up", "scroll_transcript('up')", show=False, priority=True),
        Binding("down", "scroll_transcript('down')", show=False, priority=True),
        Binding("pageup", "scroll_transcript('pageup')", show=False, priority=True),
        Binding("pagedown", "scroll_transcript('pagedown')", show=False, priority=True),
        Binding("home", "scroll_transcript('home')", show=False, priority=True),
        Binding("end", "scroll_transcript('end')", show=False, priority=True),
    ]

    def action_scroll_transcript(self, direction: str) -> None:
        # App-level priority bindings run before modal widgets; skip so ↑/↓/PgUp…
        # reach OptionList in session/model/command pickers (see Textual SkipAction).
        if isinstance(self.screen, ModalScreen):
            raise SkipAction
        try:
            log = self.query_one("#transcript", RichLog)
        except Exception:
            return
        if direction == "pageup":
            log.scroll_page_up(animate=False)
        elif direction == "pagedown":
            log.scroll_page_down(animate=False)
        elif direction == "up":
            for _ in range(5):
                log.scroll_up(animate=False)
        elif direction == "down":
            for _ in range(5):
                log.scroll_down(animate=False)
        elif direction == "home":
            log.scroll_home(animate=False)
        elif direction == "end":
            log.scroll_end(animate=False)

    def __init__(self):
        super().__init__()
        self._busy = False
        self._cancel_flag = threading.Event()
        self._last_input_value = ""
        self._activity_timer = None
        self._activity_label = ""
        self._activity_t0 = 0.0
        self._turn_t0 = 0.0
        self._activity_spinner_i = 0
        self._spinner_frames = "⠋⠙⠹⠸⠼⠴⠦⠧⠏"

    def compose(self) -> ComposeResult:
        with Vertical(id="main"):
            yield RichLog(id="transcript", wrap=True, highlight=True, markup=True, auto_scroll=True)
            with Horizontal(id="activity_row"):
                yield Static("", id="activity_phase", markup=True)
                yield Static("", id="activity_clock", markup=True)
            yield Static("", id="statusbar", markup=True)
            yield PromptArea(id="prompt")

    # ─── lifecycle ─────────────────────────────────────────────────────
    def on_mount(self):
        self.title = "Jarvis"
        self.sub_title = "Better than Claude Code agent"

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

        # Auth is normally resolved *before* the TUI starts (see `run()` below)
        # so interactive prompts use real stdio. Fall back to in-TUI resolution
        # only if something bypassed that path.
        if state.client is None:
            state.client = make_client()
        welcome_banner()
        header_panel(compact=True)
        db_init()
        state.current_session_id = db_create_session(state.MODEL)
        self._set_status("ready")
        self._sync_activity_phase("")

        self.query_one("#prompt", PromptArea).focus()

    def _sync_activity_phase(self, label: str) -> None:
        self._activity_label = (label or "").strip()
        self._activity_t0 = time.monotonic()
        self._activity_spinner_i = 0
        self._refresh_activity_widgets()

    def _tick_activity_spinner(self) -> None:
        if self._busy and self._activity_label:
            self._activity_spinner_i += 1
            self._refresh_activity_widgets()

    def _refresh_activity_widgets(self) -> None:
        from datetime import datetime

        try:
            ph = self.query_one("#activity_phase", Static)
            clk = self.query_one("#activity_clock", Static)
        except Exception:
            return
        label = self._activity_label
        if not label and not self._busy:
            ph.update("")
            clk.update("")
            return
        frames = self._spinner_frames
        i = self._activity_spinner_i % len(frames)
        sp = frames[i] if (self._busy and label) else " "
        step_elapsed = max(0.0, time.monotonic() - self._activity_t0)
        wall = datetime.now().strftime("%H:%M:%S")
        ph.update(
            f"[cyan]{sp}[/] [b]{label}[/] [dim]· this step {step_elapsed:.1f}s[/]"
        )
        if self._busy and self._turn_t0:
            turn_elapsed = max(0.0, time.monotonic() - self._turn_t0)
            clk.update(
                f"[dim]{wall}  · turn {turn_elapsed:.1f}s  · step {step_elapsed:.1f}s[/]"
            )
        else:
            clk.update(f"[dim]{wall}  · step {step_elapsed:.1f}s[/]")

    def _start_activity_pulse(self) -> None:
        self._stop_activity_pulse()
        self._activity_timer = self.set_interval(0.1, self._tick_activity_spinner)

    def _stop_activity_pulse(self) -> None:
        if self._activity_timer is not None:
            self._activity_timer.stop()
            self._activity_timer = None

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
        def after(cmd: str | None):
            inp = self.query_one("#prompt", PromptArea)
            if not cmd:
                inp.focus()
                return
            if _is_session_picker_command(cmd):
                self._open_session_picker()
                inp.focus()
                return
            if _is_bare_model_command(cmd):
                self._open_model_picker()
                inp.focus()
                return
            if cmd.endswith(" "):
                inp.text = cmd
                inp.move_cursor((0, len(cmd)))
                self._last_input_value = cmd
                inp.focus()
                return
            if cmd.strip() == "/multi":
                inp.text = cmd.strip()
                inp.move_cursor((0, len(inp.text)))
                self._last_input_value = inp.text
                inp.focus()
                return
            self._dispatch_palette_slash(cmd)
            inp.focus()

        self.push_screen(CommandPaletteScreen(), after)

    def _open_model_picker(self):
        def after(model_id: str | None):
            if not model_id:
                self._tui_console.print("[dim]model picker cancelled[/]")
                return
            from ..commands.control import _apply_model_selection

            _apply_model_selection(model_id)
            self._set_status("ready")

        self.push_screen(ModelPickerScreen(), after)

    def _dispatch_palette_slash(self, inp: str):
        """Run a slash command picked from the palette (no second Enter)."""
        if self._busy:
            return
        from ..commands.dispatch import handle_slash
        from .. import state

        text = (inp or "").strip()
        if not text.startswith("/"):
            return
        if text in ("/exit", "/quit"):
            self.exit()
            return
        head = text.split(maxsplit=1)[0]
        if head[1:] in state.aliases:
            rest = text[len(head) :]
            text = state.aliases[head[1:]] + rest
        result, should_send, new_inp = handle_slash(text)
        if result == "exit":
            self.exit()
            return
        if should_send and new_inp:
            log = self.query_one("#transcript", RichLog)
            log.write(
                Panel(
                    Text(new_inp),
                    title="you",
                    title_align="left",
                    border_style="green",
                    padding=(0, 1),
                )
            )
            self._busy = True
            self._turn_t0 = time.monotonic()
            self._sync_activity_phase("Starting your request…")
            self._start_activity_pulse()
            self._set_status("thinking…")
            self._run_turn(new_inp)
            return
        self._set_status("ready")

    def action_escape_action(self):
        """Esc when no modal is open: cancel any in-flight AI turn."""
        if self._busy:
            from ..repl.stream import cancel_current_stream
            if cancel_current_stream():
                self._tui_console.print("[yellow]⏹ cancelled by user[/]")
                # Worker may still be blocked on finalize after close(); reset UI now
                # so the prompt accepts new input. _turn_done is safe if the worker
                # also calls it from finally.
                self._turn_done()

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

        # /model (no argument) → modal picker (no stdin)
        if _is_bare_model_command(text):
            self._open_model_picker()
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
        self._turn_t0 = time.monotonic()
        self._sync_activity_phase("Starting your request…")
        self._start_activity_pulse()
        self._set_status("thinking…")
        self._run_turn(text)

    # ─── session picker ────────────────────────────────────────────────
    def _open_session_picker(self):
        def after(sid):
            if sid is None:
                self._tui_console.print("[dim]cancelled[/]")
                return
            if resume_session_into_state(sid, self._tui_console.print, preview=False):
                self._render_loaded_session()
        self.push_screen(SessionPickerScreen(), after)

    def _block_dict(self, block):
        if hasattr(block, "model_dump"):
            return block.model_dump()
        return block if isinstance(block, dict) else {}

    def _content_text(self, content) -> str:
        if isinstance(content, str):
            return content
        texts = []
        for block in content:
            data = self._block_dict(block)
            if data.get("type") == "text":
                texts.append(data.get("text", ""))
            elif data.get("type") == "image":
                texts.append("[image]")
        return "\n\n".join(t for t in texts if t)

    def _render_internal_blocks(self, content) -> None:
        if isinstance(content, str):
            return
        from .. import state
        log = self.query_one("#transcript", RichLog)
        for block in content:
            data = self._block_dict(block)
            kind = data.get("type")
            if kind == "thinking":
                if not state.think_mode:
                    continue
                body = data.get("thinking", "")
                if body:
                    log.write(Panel(Text(body), title="thinking", border_style="dim", padding=(0, 1)))
                continue
            if not state.show_internal:
                continue
            if kind == "tool_use":
                name = data.get("name", "tool")
                args = str(data.get("input", ""))[:800]
                log.write(Panel(Text(args), title=f"tool: {name}", border_style="yellow", padding=(0, 1)))
            elif kind == "tool_result":
                body = data.get("content", "")
                if isinstance(body, list):
                    body = "\n".join(
                        item.get("text", "") for item in body if isinstance(item, dict)
                    )
                log.write(Panel(Text(str(body)[:2000]), title="tool result", border_style="dim", padding=(0, 1)))

    def _render_loaded_session(self) -> None:
        from .. import state
        from ..repl.banners import welcome_banner, header_panel

        log = self.query_one("#transcript", RichLog)
        log.clear()
        welcome_banner()
        header_panel(compact=True)
        self._tui_console.print(
            f"[green]▶ resumed session #{state.current_session_id} ({len(state.messages)} messages)[/]"
        )
        for msg in state.messages:
            role = msg.get("role", "")
            content = msg.get("content", "")
            text = self._content_text(content).strip()
            if role == "user":
                if text:
                    log.write(Panel(Text(text), title="you", title_align="left",
                                    border_style="green", padding=(0, 1)))
                self._render_internal_blocks(content)
            elif role == "assistant":
                self._render_internal_blocks(content)
                if text:
                    log.write(Panel(Markdown(text), title="Jarvis", title_align="left",
                                    border_style="magenta", padding=(0, 1)))
        self._set_status("session loaded")

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
            self._tui_console.assistant_stream_abort()
            self._tui_console.print(f"[red]error: {type(e).__name__}: {e}[/]")
        finally:
            self._tui_console.assistant_stream_abort()
            self.call_from_thread(self._turn_done)

    def _turn_done(self):
        self._busy = False
        self._turn_t0 = 0.0
        self._stop_activity_pulse()
        self._sync_activity_phase("")
        self._set_status("ready")
        self.query_one("#prompt", PromptArea).focus()

    def _set_status(self, msg: str):
        try:
            from .. import state
            trace = "shown" if state.show_internal else "hidden"
            parts = []
            if msg:
                parts.append(f"[b]{msg}[/]")
            parts.append(f"🤖 {state.MODEL}")
            if state.current_session_id is not None:
                parts.append(f"#{state.current_session_id}")
            parts.append(f"💬 {len(state.messages)}")
            parts.append(f"🔧 {state.tool_calls_count}")
            parts.append(f"⇅ {state.total_in}/{state.total_out}")
            parts.append(f"internals:{trace}")
            parts.append("[dim]F2 toggle[/]")
            self.query_one("#statusbar", Static).update(" | ".join(parts))
        except Exception:
            pass

    def action_toggle_internal(self):
        from .. import state
        state.show_internal = not state.show_internal
        mode = "shown" if state.show_internal else "hidden"
        self._set_status(f"internals {mode}")
        if state.current_session_id and state.messages and not self._busy:
            self._render_loaded_session()
        else:
            try:
                self._tui_console.print(
                    f"[dim]internal tool trace {mode}; thinking panels only when /think is on[/]"
                )
            except Exception:
                pass

    def action_cancel_or_quit(self):
        # If the user has a text selection in the transcript, Ctrl+C should
        # copy it (matching normal terminal expectations) instead of
        # cancelling or quitting.
        try:
            selected = self.screen.get_selected_text()
        except Exception:
            selected = None
        if selected:
            self.copy_to_clipboard(selected)
            try:
                import subprocess
                subprocess.run(["pbcopy"], input=selected.encode(), check=False)
            except Exception:
                pass
            self.screen.clear_selection()
            self._set_status("copied")
            return
        if self._busy:
            from ..repl.stream import cancel_current_stream
            self._sync_activity_phase("Cancelling…")
            if cancel_current_stream():
                self._tui_console.print("[yellow]⏹ cancelled by user[/]")
                self._turn_done()
            else:
                self._set_status("cancelling…")
        else:
            self.exit()


def _escape(s: str) -> str:
    return s.replace("[", r"\[")


def run():
    # Resolve authentication BEFORE the Textual app takes over the terminal.
    # Once the TUI is running, `console` is swapped to a TUIConsole that cannot
    # read stdin — so any interactive prompt (mode picker, API key entry, OAuth
    # fallback) would crash. Doing it here means `console.input()` / getpass()
    # see real stdio, and the user can recover from a reset key / stale auth
    # mode / OAuth failure before the UI starts.
    from ..auth.client import make_client
    from .. import state

    if state.client is None:
        state.client = make_client()

    JarvisTUI().run(mouse=False)
