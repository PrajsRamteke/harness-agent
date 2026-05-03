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
from textual.widgets import RichLog, Static, TextArea
from textual import work

from rich.markdown import Markdown
from rich.panel import Panel
from rich.text import Text

from .console_shim import TUIConsole
from .session_modal import SessionPickerScreen, resume_session_into_state
from .palette_modal import CommandPaletteScreen
from .model_modal import ModelPickerScreen
from .. import state


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
    /* ── GitHub Dark-inspired theme ────────────────────────── */
    Screen {
        background: #0d1117;
        layers: base overlay;
    }
    #main {
        height: 100%;
        width: 100%;
        min-width: 0;
    }

    /* ── Transcript ─────────────────────────────────────── */
    #transcript {
        background: #0d1117;
        color: #e6edf3;
        padding: 0 1;
        border: none;
        height: 1fr;
        min-height: 0;
        min-width: 0;
        overflow-y: auto;
        scrollbar-color: transparent transparent;
        scrollbar-size-vertical: 0;
    }

    /* ── Prompt input ───────────────────────────────────── */
    #prompt_row {
        height: auto;
        margin: 0 1 1 1;
        background: #161b22;
        border: tall #30363d;
        min-width: 0;
    }
    #prompt_row:focus-within {
        border: tall #58a6ff;
    }
    #prompt_prefix {
        width: auto;
        padding: 0 0 0 1;
        color: #58a6ff;
        text-style: bold;
        dock: left;
        background: #161b22;
    }
    #prompt {
        height: auto;
        min-height: 1;
        max-height: 12;
        background: #161b22;
        border: none;
        padding: 0 2 0 1;
        min-width: 0;
    }
    #prompt:focus {
        border: none;
    }

    /* ── Status bar (also shows spinner + timing when busy) ─── */
    #statusbar {
        height: auto;
        max-height: 6;
        background: #161b22;
        color: #8b949e;
        padding: 0 1;
        margin: 0 1 0 1;
        min-width: 0;
        border: tall #30363d;
        scrollbar-size-vertical: 0;
        scrollbar-color: transparent transparent;
    }

    /* ── Shared widget defaults ─────────────────────────── */
    Input, TextArea {
        background: #161b22;
        color: #e6edf3;
    }
    TextArea > .text-area--cursor-line {
        background: #161b22;
    }
    """

    BINDINGS = [
        Binding("ctrl+d", "quit", "Quit", show=True),
        Binding("ctrl+c", "cancel_or_quit", "Cancel/Quit", show=True),
        Binding("f2", "toggle_internal", "Internals", show=True),
        Binding("tab", "cycle_mode", "Mode", show=True),
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
            yield RichLog(id="statusbar", wrap=True, highlight=True, markup=True, auto_scroll=False, max_lines=6)
            with Horizontal(id="prompt_row"):
                yield Static("❯", id="prompt_prefix", markup=False)
                yield PromptArea(id="prompt")

    # ─── lifecycle ─────────────────────────────────────────────────────
    def on_mount(self):
        self.title = "Jarvis"
        self.sub_title = "The better agent"

        log = self.query_one("#transcript", RichLog)
        status = self.query_one("#statusbar", RichLog)

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

        # Auto-connect MCP servers from config
        from ..mcp.registry import auto_connect_servers
        auto_connect_servers(console_print=self._tui_console.print)

        # ── Detect project context file (AGENT.md / CLAUDE.md / JARVIS.md) ──
        import pathlib as _pl
        _proj_cwd = _pl.Path.cwd()
        for _fname in ("AGENT.md", "CLAUDE.md", "JARVIS.md"):
            _fp = _proj_cwd / _fname
            if _fp.is_file():
                state.project_context_file = _fname
                state.project_context_path = str(_fp)
                try:
                    state.project_context_content = _fp.read_text(errors="ignore")
                except Exception:
                    state.project_context_content = ""
                break

        if state.project_context_file:
            log.write(
                Panel(
                    Text.from_markup(
                        f"📄 [bold #58a6ff]{state.project_context_file}[/] found — "
                        f"[#58a6ff]loaded for project context[/] "
                        f"[dim]({len(state.project_context_content)} chars)[/]",
                    ),
                    title="Project Context",
                    title_align="left",
                    border_style=state.theme_colors["project_border"],
                    padding=(0, 1),
                )
            )
            self._set_status("ready")

        # ── Detect project-base skills (.skills/) ────────────────────────────
        from ..storage import skills as _skills
        _sk_count = _skills.skill_count()
        if _sk_count > 0:
            log.write(
                Panel(
                    Text.from_markup(
                        f"🧠 [bold #58a6ff]{_sk_count} skill{'s' if _sk_count != 1 else ''}[/] available — "
                        f"[#58a6ff]headers in system prompt[/], "
                        f"[dim]use /skill to list or skill_load() to load[/]",
                    ),
                    title="Project Skills",
                    title_align="left",
                    border_style=state.theme_colors["project_border"],
                    padding=(0, 1),
                )
            )

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
        """Prepend spinner + phase to status bar, append turn timing."""
        from datetime import datetime

        label = self._activity_label
        if not label or not self._busy:
            return

        frames = self._spinner_frames
        i = self._activity_spinner_i % len(frames)
        sp = frames[i]
        step_elapsed = max(0.0, time.monotonic() - self._activity_t0)
        turn_elapsed = max(0.0, time.monotonic() - self._turn_t0) if self._turn_t0 else 0.0

        spinner_prefix = f"[#58a6ff]{sp}[/] [b]{label}[/] [dim]{step_elapsed:.1f}s[/]"
        clock_suffix = f"[dim][{turn_elapsed:.0f}s][/]"

        try:
            # Build the same status bar info as _set_status
            from .. import state
            trace = "shown" if state.show_internal else "hidden"
            lbl, col, _s = state.MODE_LABELS.get(
                state.active_mode, (state.active_mode, "#8b949e", "dim")
            )
            mode_part = (
                f"[bold {col}]{lbl}[/]"
                if state.active_mode != "default"
                else f"[dim {col}]{lbl}[/]"
            )
            left = [f"{mode_part}", f"[#58a6ff]{state.MODEL}[/]"]
            if state.current_session_id is not None:
                left.append(f"[dim]#{state.current_session_id}[/]")

            right = []
            right.append(f"💬 [dim]{len(state.messages)}[/]")
            right.append(f"⇅ [dim]{state.total_in}[/]/[dim]{state.total_out}[/] [dim]= {state.total_in + state.total_out}[/]")
            if state.think_mode:
                right.append("[#3fb950]think:on[/]")
            else:
                right.append("[dim]think:off[/]")
            if state.project_context_file:
                right.append(f"[#bc8cff]{state.project_context_file}[/]")
            right.append(f"[dim]int:{trace}[/]")

            bar = self.query_one("#statusbar", RichLog)
            bar.clear()
            # prepend spinner · status info · clock
            parts = [spinner_prefix] + left + [""] + right + [clock_suffix]
            bar.write(Text.from_markup("  ·  ".join(parts)))
        except Exception:
            pass

    def _start_activity_pulse(self) -> None:
        self._stop_activity_pulse()
        self._activity_timer = self.set_interval(0.1, self._tick_activity_spinner)

    def _stop_activity_pulse(self) -> None:
        if self._activity_timer is not None:
            self._activity_timer.stop()
            self._activity_timer = None

    # ─── palette (centered modal) ──────────────────────────────────────
    def on_text_area_changed(self, event: TextArea.Changed) -> None:
        """Open palette instantly when '/' is typed in an empty prompt."""
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

        # Commands that need stdin (OAuth token paste, key entry) must run in
        # the worker thread so the TUIConsole can show a blocking input modal.
        if head in ("/login", "/logout", "/auth", "/key", "/model", "/session", "/sessions"):
            # Show the command in the transcript, then run in worker thread
            log = self.query_one("#transcript", RichLog)
            log.write(
                Panel(
                    Markdown(text),
                    title="you",
                    title_align="left",
                    border_style=state.theme_colors["user_border"],
                    padding=(0, 1),
                )
            )
            self._busy = True
            self._turn_t0 = time.monotonic()
            self._sync_activity_phase("Thinking…")
            self._start_activity_pulse()
            self._set_status("thinking…")
            self._run_turn(text)
            return

        result, should_send, new_inp = handle_slash(text)
        if result == "exit":
            self.exit()
            return
        if should_send and new_inp:
            log = self.query_one("#transcript", RichLog)
            log.write(
                Panel(
                    Markdown(new_inp),
                    title="you",
                    title_align="left",
                    border_style=state.theme_colors["user_border"],
                    padding=(0, 1),
                )
            )
            self._busy = True
            self._turn_t0 = time.monotonic()
            self._sync_activity_phase("Thinking…")
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
        log.write(Panel(Markdown(text), title="you", title_align="left",
                        border_style=state.theme_colors["user_border"], padding=(0, 1)))

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
        self._sync_activity_phase("Thinking…")
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
                if not state.show_internal:
                    continue
                body = data.get("thinking", "")
                if body:
                    log.write(Panel(Text(body, style="dim"), title="thinking", title_align="left",
                                    border_style=state.theme_colors["think_border"], padding=(0, 1)))
                continue
            if not state.show_internal:
                continue
            if kind == "tool_use":
                name = data.get("name", "tool")
                args = str(data.get("input", ""))[:800]
                log.write(Panel(Text(args), title=f"tool: {name}", title_align="left",
                                border_style=state.theme_colors["tool_border"], padding=(0, 1)))
            elif kind == "tool_result":
                body = data.get("content", "")
                if isinstance(body, list):
                    body = "\n".join(
                        item.get("text", "") for item in body if isinstance(item, dict)
                    )
                log.write(Panel(Text(str(body)[:2000], style="dim"), title="tool result", title_align="left",
                                border_style=state.theme_colors["think_border"], padding=(0, 1)))

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
                    log.write(Panel(Markdown(text), title="you", title_align="left",
                                    border_style=state.theme_colors["user_border"], padding=(0, 1)))
                self._render_internal_blocks(content)
            elif role == "assistant":
                self._render_internal_blocks(content)
                if text:
                    log.write(Panel(Markdown(text), title="Jarvis", title_align="left",
                                    border_style=state.theme_colors["asst_border"], padding=(0, 1)))
        self._set_status("session loaded")

    @work(thread=True, exclusive=True)
    def _run_turn(self, inp: str) -> None:
        """Mirror of jarvis.main._send_and_loop, adapted for the TUI."""
        from ..commands.dispatch import handle_slash
        from ..repl.stream import call_claude_stream
        from ..repl.render import render_assistant
        from ..repl.turn_progress import report_turn_phase
        from ..storage.sessions import db_append_message, db_set_title_if_empty
        from .. import state

        # ── Re-read project context file each turn ──────────────────────────
        if state.project_context_path:
            import pathlib as _pl
            _ctx_path = _pl.Path(state.project_context_path)
            if _ctx_path.exists():
                try:
                    state.project_context_content = _ctx_path.read_text(errors="ignore")
                except Exception:
                    pass
                if state.project_context_content:
                    report_turn_phase(
                        f"📄 {state.project_context_file} "
                        f"({len(state.project_context_content)} chars)"
                    )

        try:
            # alias expansion
            if inp.startswith("/"):
                head = inp.split(maxsplit=1)[0]
                if head[1:] in state.aliases:
                    rest = inp[len(head):]
                    inp = state.aliases[head[1:]] + rest

            if inp.startswith("!"):
                cmd = inp[1:].strip()
                if cmd:
                    from ..tools.shell import run_bash
                    prev = state.auto_approve; state.auto_approve = True
                    out = run_bash(cmd)
                    state.auto_approve = prev
                    self.call_from_thread(lambda o=out: self._tui_console.print(o))
                return

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

            # ── mode / auto-detect badge ───────────────────────────────────────
            # Two cases:
            #   1. Explicit mode (e.g. coding) → solid coloured badge
            #   2. Default mode but keyword auto-detected → dimmer "auto" badge
            from ..repl.system import _is_coding_request, _keyword_detected
            _explicit_mode = state.active_mode != "default"
            _auto_detected = (not _explicit_mode) and _keyword_detected(state.messages)

            if _explicit_mode:
                lbl, col, _s = state.MODE_LABELS.get(
                    state.active_mode, (state.active_mode, "#3fb950", "bold")
                )
                def _show_explicit_badge(label=lbl, colour=col):
                    try:
                        log = self.query_one("#transcript", RichLog)
                        badge = Text()
                        badge.append(f" {label} ", style=f"bold #0a0a0a on {colour}")
                        badge.append("  addon rules active", style=colour)
                        log.write(badge)
                    except Exception:
                        pass
                self.call_from_thread(_show_explicit_badge)

            elif _auto_detected:
                def _show_auto_badge():
                    try:
                        log = self.query_one("#transcript", RichLog)
                        badge = Text()
                        badge.append(" ⚡ CODING ", style="bold #0a0a0a on #3fb950")
                        badge.append("  auto-detected · /coding to stay in mode", style="dim #3fb950")
                        log.write(badge)
                    except Exception:
                        pass
                self.call_from_thread(_show_auto_badge)
            # ──────────────────────────────────────────────────────────────────

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
        except KeyboardInterrupt:
            self._tui_console.assistant_stream_abort()
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
            import pathlib
            from .. import state
            trace = "shown" if state.show_internal else "hidden"
            lbl, col, _s = state.MODE_LABELS.get(
                state.active_mode, (state.active_mode, "#8b949e", "dim")
            )
            mode_part = (
                f"[bold {col}]{lbl}[/]"
                if state.active_mode != "default"
                else f"[dim {col}]{lbl}[/]"
            )
            # Build a cleaner status line — grouped by category, no pipe noise
            left = []
            if msg:
                left.append(f"[#e6edf3]{msg}[/]")
            left.append(f"{mode_part}")
            left.append(f"[#58a6ff]{state.MODEL}[/]")
            if state.current_session_id is not None:
                left.append(f"[dim]#{state.current_session_id}[/]")

            right = []
            right.append(f"💬 [dim]{len(state.messages)}[/]")
            right.append(f"⇅ [dim]{state.total_in}[/]/[dim]{state.total_out}[/] [dim]= {state.total_in + state.total_out}[/]")
            if state.think_mode:
                right.append("[#3fb950]think:on[/]")
            else:
                right.append("[dim]think:off[/]")
            if state.project_context_file:
                right.append(f"[#bc8cff]{state.project_context_file}[/]")
            right.append(f"[dim]int:{trace}[/]")

            sep = "  ·  "
            all_parts = left + [""] + right
            self.query_one("#statusbar", RichLog).clear()
            self.query_one("#statusbar", RichLog).write(
                Text.from_markup(sep.join(all_parts))
            )
        except Exception:
            pass

    def action_cycle_mode(self) -> None:
        """Tab — cycle through available modes (default → coding → …)."""
        from .. import state
        from ..commands.control import _VALID_MODES

        # If prompt has text, Tab should insert a tab / do nothing special
        try:
            prompt = self.query_one("#prompt", PromptArea)
            if prompt.text.strip():
                # Let normal Tab behaviour through (indent / focus)
                return
        except Exception:
            pass

        modes = list(_VALID_MODES)
        current_idx = modes.index(state.active_mode) if state.active_mode in modes else 0
        next_mode = modes[(current_idx + 1) % len(modes)]
        state.active_mode = next_mode

        lbl, col, _s = state.MODE_LABELS.get(next_mode, (next_mode, "#58a6ff", ""))
        try:
            log = self.query_one("#transcript", RichLog)
            badge = Text()
            if next_mode == "default":
                badge.append(" DEFAULT MODE ", style="bold #e6edf3 on #30363d")
                badge.append("  standard", style="#8b949e")
            else:
                badge.append(f" {lbl} ", style=f"bold #0a0a0a on {col}")
                badge.append("  addon rules active", style=col)
            log.write(badge)
        except Exception:
            pass

        self._set_status(f"mode → {lbl}")

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
                    f"[dim]internal tool trace {mode}[/]"
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

    from .mouse_toggle import reset_mouse_fully

    app = JarvisTUI()
    try:
        app.run(mouse=False)
    finally:
        # Restore terminal mouse state even if the app crashes or is killed
        # while a modal (which enables mouse tracking) was displayed.
        reset_mouse_fully()
