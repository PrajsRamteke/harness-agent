"""Textual TUI app for Jarvis.

Layout
------
    ┌─ header (brand · model · agent · #session) ─────────────────────┐
    │                                                                 │
    │                       transcript (RichLog)                      │
    │                                                                 │
    ├──────────────── stash strip (queued prompts, FIFO) ─────────────┤
    ├──────────────── ask strip (LLM multiple-choice, ↑↓ ↵) ─────────┤
    ├──────────────── status strip (spinner · stats) ─────────────────┤
    │ ❯ composer                                                      │
    ├──────────────── hint bar (key cheatsheet) ──────────────────────┤
    └─────────────────────────────────────────────────────────────────┘
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
from textual.widgets import RichLog, Static, OptionList
from textual import work

from rich.markdown import Markdown
from rich.markup import escape as _rich_escape
from rich.panel import Panel
from rich.text import Text

from .console_shim import TUIConsole
from ..repl.tool_output_backfill import backfill_tool_output_history, inspector_has_entries
from ..repl.tool_runs import (
    _display_for_tool,
    expand_dock_tool_use,
    is_dock_tool,
    list_runs,
    show_parallel_file_panel,
)
from .ask_user import AskUserController, AskQuestion, normalize_questions
from .web_bar import WebRemoteBar, WebRemoteQR
from . import theme as ui
from .. import state


# ─── slash-command sniffers (modal-opening shortcuts) ────────────────────
# Pure command predicates live in app_commands.py; re-exported here so callers
# (and tests) that import them from jarvis.tui.app keep working.
from .app_commands import (  # noqa: F401
    _is_bare_model_command,
    _is_session_picker_command,
    _is_think_picker_command,
    _is_mcp_modal_command,
    _is_agent_picker_command,
    _is_skill_picker_command,
    _is_command_manager_command,
    _is_memory_modal_command,
    _is_pin_modal_command,
    _is_lesson_modal_command,
    _is_settings_modal_command,
    _is_theme_modal_command,
    _is_provider_hub_command,
    _is_local_command,
)


# ─── header / status segment builders ────────────────────────────────────


def _agent_badge_markup() -> str:
    """Compact badge for the active agent — used in header & status."""
    rec = state.active_agent
    if rec is None and state.active_agent_name:
        rec = state.resolve_active_agent()
    if not rec:
        return f"[{ui.FG_DIM}]default[/]"
    icon = (rec.get("icon") or "").strip()
    color = (rec.get("color") or "").strip() or ui.OK
    label = f"{icon} {rec['name']}".strip() if icon else rec["name"]
    if rec.get("scope") == "global":
        return f"[bold {color}]{label}[/] [{ui.FG_DIM}](g)[/]"
    return f"[bold {color}]{label}[/]"


def _pin_status_markup() -> str:
    """Compact pinned-context indicator for the status bar."""
    from ..storage import pin as pin_store

    text = pin_store.pin_text()
    if not text:
        return f"[{ui.FG_DIM}]pin·off[/]"
    lines, chars = pin_store.pin_stats(text)
    if not pin_store.is_enabled():
        return f"[{ui.WARN}]pin·paused[/][{ui.FG_DIM}]/{lines}L[/]"
    return (
        f"[{ui.ACCENT_2}]pin[/][{ui.FG_DIM}]·[/]"
        f"[{ui.ACCENT}]{lines}L[/][{ui.FG_DIM}]/{chars}c[/]"
    )


def _is_upgrade_command(text: str) -> bool:
    head = (text or "").strip().split(maxsplit=1)[0].lower()
    return head == "/upgrade"


# ─── multi-line prompt (Enter submits, Ctrl+J / Alt+Enter for newline) ───
# PromptArea lives in prompt_area.py; re-exported so callers/tests that import
# it from jarvis.tui.app keep working.
from .prompt_area import PromptArea  # noqa: F401, E402
from .console_swap import _swap_console_everywhere  # noqa: F401, E402
from .mixins.web_remote import WebRemoteMixin
from .mixins.activity import ActivityMixin
from .mixins.file_ref import FileRefPickerMixin


# ─── App ─────────────────────────────────────────────────────────────────


class JarvisTUI(WebRemoteMixin, ActivityMixin, FileRefPickerMixin, App):
    ENABLE_COMMAND_PALETTE = False
    CSS = ui.GLOBAL_CSS

    BINDINGS = [
        Binding("ctrl+d", "quit", "Quit", show=True),
        Binding("ctrl+c", "cancel_or_quit", "Cancel/Quit", show=True),
        Binding("ctrl+t", "toggle_internal", "Trace", show=True),
        Binding("ctrl+f", "open_tools_inspector", "Tools", show=True),
        Binding("f1", "show_shortcuts", "Help", show=False),
        Binding("question_mark", "show_shortcuts", "Help", show=False),
        Binding("f2", "toggle_internal", "Trace", show=False),
        Binding("f3", "open_tools_inspector", "Tools", show=False),
        Binding("tab", "cycle_agent", "Agent", show=True),
        Binding("escape", "escape_action", show=False),
        Binding("up", "scroll_transcript('up')", show=False, priority=True),
        Binding("down", "scroll_transcript('down')", show=False, priority=True),
        Binding("pageup", "scroll_transcript('pageup')", show=False, priority=True),
        Binding("pagedown", "scroll_transcript('pagedown')", show=False, priority=True),
        Binding("home", "scroll_transcript('home')", show=False, priority=True),
        Binding("end", "scroll_transcript('end')", show=False, priority=True),
        Binding("ctrl+shift+u", "copy_web_url", "Copy URL", show=False),
        Binding("ctrl+y", "copy_last_reply", "Copy reply", show=False),
    ]

    def action_scroll_transcript(self, direction: str) -> None:
        if isinstance(self.screen, ModalScreen):
            raise SkipAction
        if self._ask_user.active and direction in ("up", "down"):
            if self._ask_user.handle_key(direction):
                return
        if self._try_file_ref_scroll(direction):
            return
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
        self._last_input_value = ""
        self._activity_timer = None
        self._activity_label = ""
        self._activity_t0 = 0.0
        self._turn_t0 = 0.0
        self._activity_spinner_i = 0
        self._spinner_frames = ui.SPINNER_FRAMES
        self._status_msg = "ready"
        self._last_ctrl_c_t = 0.0
        self._key_debug = False
        self._web_bridge = None
        self._web_mux = None
        self._web_server = None
        self._web_urls: list[str] = []
        self._web_primary_url = ""
        self._last_t_width = 0
        self._width_check_timer = None
        # Git branch cache — refreshed every few seconds, not every status tick.
        self._git_branch: str | None = None
        self._git_branch_checked_at: float = 0.0
        self._git_branch_ttl: float = 5.0
        self._file_ref_mention: tuple[int, int, str] | None = None
        self._file_ref_mouse_on = False
        self._file_ref_last_query: str | None = None
        self._tokenizing_attachments = False
        # RichLog line index where the live parallel-files panel starts (None = frozen in transcript).
        self._tool_activity_anchor: int | None = None
        self._tool_activity_line_count: int = 0
        self._tool_activity_frozen: bool = False
        self._tool_activity_lock = threading.Lock()
        self._ask_user = AskUserController(self)

    def begin_ask_user_question(self, questions, on_done) -> None:
        """Start status-bar Q&A (called from worker thread via console shim)."""
        if questions and isinstance(questions[0], AskQuestion):
            qs = questions
        else:
            try:
                qs = normalize_questions(questions)
            except ValueError as e:
                import json
                on_done(json.dumps({"answers": [], "error": str(e)}))
                return
        self._ask_user.begin(qs, on_done)

    # @file picker focus/navigation + the file_ref_picker_active property live
    # in FileRefPickerMixin (jarvis/tui/mixins/file_ref.py).

    def _refresh_git_branch(self) -> None:
        """Re-query the current git branch (with TTL caching)."""
        now = time.monotonic()
        if (now - self._git_branch_checked_at) < self._git_branch_ttl:
            return
        self._git_branch_checked_at = now
        try:
            from ..repl.banners import _current_git_branch
            import pathlib as _pl
            self._git_branch = _current_git_branch(_pl.Path.cwd())
        except Exception:
            self._git_branch = None

    async def _on_key(self, event):  # type: ignore[override]
        key = getattr(event, "key", "")
        if self._ask_user.active and self._ask_user.handle_key(key):
            event.stop()
            event.prevent_default()
            return
        if self._key_debug:
            self._key_debug = False
            try:
                key = getattr(event, "key", "?")
                name = getattr(event, "name", "?")
                char = getattr(event, "character", None)
                aliases = list(getattr(event, "key_aliases", []) or [])
                char_repr = repr(char) if char is not None else "<none>"
                self._tui_console.print(
                    f"[{ui.OK}]⬟ keytest:[/] [bold]{key}[/]  "
                    f"[{ui.FG_DIM}](name={name}, char={char_repr}, aliases={aliases})[/]"
                )
                if key == "enter" and name == "enter":
                    self._tui_console.print(
                        f"[{ui.FG_DIM}]→ your terminal sent bare Enter — it doesn't "
                        f"distinguish Shift+Enter from Enter. Use Ctrl+N / Ctrl+J / "
                        f"Alt+Enter / \\\\+Enter, or enable the Kitty keyboard "
                        f"protocol in your terminal.[/]"
                    )
            except Exception:
                pass
            event.stop()
            event.prevent_default()
            return

    # ─── compose ─────────────────────────────────────────────────────
    def compose(self) -> ComposeResult:
        yield WebRemoteQR(id="web_qr_overlay")
        with Vertical(id="main"):
            yield RichLog(
                id="transcript",
                wrap=True,
                highlight=True,
                markup=True,
                auto_scroll=True,
            )
            yield Static("", id="queuebar", markup=True, shrink=False, classes="hidden")
            yield Static("", id="askbar", markup=True, shrink=False, classes="hidden")
            yield Static("", id="statusbar", markup=True, shrink=True)
            with Vertical(id="composer_block"):
                with Vertical(id="file_ref_panel", classes="hidden"):
                    yield Static(
                        "📎  type @ in chat to search files",
                        id="file_ref_hint",
                        markup=False,
                    )
                    yield OptionList(id="file_ref_picker")
                with Horizontal(id="composer"):
                    yield Static(ui.ARROW, id="prompt_prefix", markup=False)
                    yield PromptArea(id="prompt", highlight_cursor_line=False)
            yield Static("", id="hintbar", markup=True, shrink=True)
            yield WebRemoteBar(id="webar")

    # ─── lifecycle ───────────────────────────────────────────────────
    def on_mount(self):
        from ..constants import VERSION
        self.title = f"Jarvis v{VERSION}"
        self.sub_title = "The better agent"

        log = self.query_one("#transcript", RichLog)
        status = self.query_one("#statusbar", Static)

        # Swap console BEFORE importing repl/* so their module-local
        # `console` names are rebound to the TUI console.
        tui_console = TUIConsole(self, log, status)
        _swap_console_everywhere(tui_console)
        self._tui_console = tui_console

        if state.web_enabled:
            self._start_web_remote(tui_console)

        from ..auth.client import make_client
        from ..storage.sessions import db_init, db_create_session

        if state.client is None:
            state.client = make_client(interactive=False)
        # Welcome art is width-responsive but the transcript has no size until
        # the first layout pass — defer the intro so the art renders full-size
        # (rendering it here picks the no-art fallback for width 24).
        self.call_after_refresh(self._render_welcome_intro)
        db_init()
        state.current_session_id = db_create_session(state.MODEL)

        # Header / hint / status are all rendered through the new writer.
        self._render_hintbar()
        self._set_status("ready")
        self._sync_activity_phase("")

        # Poll for width changes (RichLog doesn't auto-reflow panels on resize)
        self._start_width_monitor()

        from ..project_context import detect_project_context
        detect_project_context()

        # Auto-activate coding agent when inside a coding project
        # and the user hasn't explicitly set an agent yet.
        from ..storage.agents import auto_activate_coding_agent
        auto_activate_coding_agent()

        self.query_one("#prompt", PromptArea).focus()

        if state.startup_prompt:
            self.set_timer(0.05, self._submit_startup_prompt)

    def _render_welcome_intro(self) -> None:
        """Welcome art + card + sign-in line + context strip.

        Runs via ``call_after_refresh`` so the transcript width is known and
        ``welcome_banner()`` can pick the full-size art. Background workers
        that print to the transcript (MCP auto-connect, updater) are started
        from here — not ``on_mount`` — so their output can never land above
        the welcome art.

        When the full block-letter art fits, it enters with a one-second
        shine sweep (see ``intro_anim.py``); the rest of the intro and the
        background workers start only after the sweep settles, so the
        transcript stays exclusively ours while frames are redrawn.
        """
        from ..repl.banners import welcome_banner, welcome_art_for_width, _console_width

        art = welcome_art_for_width(_console_width())
        # Animate only the block art, and never when a CLI prompt is about
        # to be auto-submitted into the transcript.
        if art and "█" in art and not state.startup_prompt:
            self._animate_welcome_art(art)
            return

        welcome_banner()
        self._finish_welcome_intro()

    def _finish_welcome_intro(self) -> None:
        """Everything after the banner: sign-in hint, context strip, workers."""
        if state.client is None:
            self._tui_console.print(
                f"[{ui.WARN}]Not signed in[/] — use [cyan]/login[/] for OAuth "
                "(Anthropic or Codex) or [cyan]/key[/] for API keys"
            )
        self._write_context_strip()

        self._auto_connect_mcp_background()
        self._check_for_updates_background()

    # ─── welcome art shine animation ──────────────────────────────────
    def _animate_welcome_art(self, art: str) -> None:
        """Sweep a bright band across the art, then settle into the banner."""
        from .intro_anim import shine_frame, sweep_centers

        log = self.query_one("#transcript", RichLog)
        self._intro_art = art
        self._intro_centers = sweep_centers(art)
        self._intro_idx = 0
        log.write(shine_frame(art, self._intro_centers[0], ui.ACCENT_3))
        self._intro_expected_lines = len(log.lines)
        self._intro_timer = self.set_interval(1 / 22, self._intro_anim_tick)

    def _intro_anim_tick(self) -> None:
        from ..repl.banners import welcome_banner
        from .intro_anim import shine_frame

        log = self.query_one("#transcript", RichLog)

        # Someone else wrote to the transcript mid-sweep (e.g. a very fast
        # user) — stop redrawing immediately and finish the intro without
        # clearing, so nothing they see gets eaten.
        if len(log.lines) != self._intro_expected_lines:
            self._intro_timer.stop()
            welcome_banner(skip_art=True)
            self._finish_welcome_intro()
            return

        self._intro_idx += 1
        if self._intro_idx >= len(self._intro_centers):
            # Sweep done — settle into the exact static banner.
            self._intro_timer.stop()
            log.clear()
            welcome_banner()
            self._finish_welcome_intro()
            return

        log.clear()
        log.write(shine_frame(self._intro_art, self._intro_centers[self._intro_idx], ui.ACCENT_3))
        self._intro_expected_lines = len(log.lines)

    # Web-remote behaviour (_start_web_remote, _handle_web_*, _sync_web_*, etc.)
    # lives in WebRemoteMixin (jarvis/tui/mixins/web_remote.py).

    @work(thread=True)
    def _check_for_updates_background(self) -> None:
        """Pull latest commits after the prompt is shown; re-exec when updated."""
        from ..updater import maybe_update_and_reexec

        maybe_update_and_reexec()

    @work(thread=True)
    def _auto_connect_mcp_background(self) -> None:
        """Connect configured MCP servers without blocking the first prompt."""
        from ..mcp.registry import auto_connect_servers

        def _print(msg: str) -> None:
            self.call_from_thread(lambda m=msg: self._tui_console.print(m))

        auto_connect_servers(console_print=_print)

    def _submit_startup_prompt(self) -> None:
        """Send a prompt passed on the CLI: jarvis \"your question here\"."""
        text = state.startup_prompt.strip()
        state.startup_prompt = ""
        if not text or self._busy:
            return
        inp = self.query_one("#prompt", PromptArea)
        inp.text = text
        inp.refresh_file_ref_highlights()
        self._last_input_value = text
        self.on_prompt_area_submitted(PromptArea.Submitted(text))
    def _render_hintbar(self) -> None:
        try:
            trace = "on" if state.show_internal else "off"
            hints = [
                f"[{ui.ACCENT_3}]↵[/] [{ui.FG_DIM}]send[/]",
                f"[{ui.ACCENT_3}]⇧↵[/] [{ui.FG_DIM}]nl[/]",
                f"[{ui.ACCENT_3}]/[/] [{ui.FG_DIM}]cmd[/]",
                f"[{ui.ACCENT_3}]@[/] [{ui.FG_DIM}]file · ↑↓ tab[/]",
                f"[{ui.ACCENT_3}]⇥[/] [{ui.FG_DIM}]agent[/]",
                f"[{ui.ACCENT_3}]esc[/] [{ui.FG_DIM}]cancel[/]",
                f"[{ui.ACCENT_3}]^Y[/] [{ui.FG_DIM}]reply[/]",
                f"[{ui.ACCENT_3}]^T[/] [{ui.FG_DIM}]trace:{trace}[/]",
                f"[{ui.ACCENT_3}]^F[/] [{ui.FG_DIM}]tools[/]",
                f"[{ui.ACCENT_3}]^D[/] [{ui.FG_DIM}]quit[/]",
                f"[{ui.ACCENT_3}]?[/] [{ui.FG_DIM}]help[/]",
            ]
            sep = f" [{ui.FG_DIM}]{ui.DOT}[/] "
            line = sep.join(hints)
            self.query_one("#hintbar", Static).update(Text.from_markup(line))
        except Exception:
            pass

    def _context_strip_markup(self) -> str | None:
        """Project context + skills/MCP summary (+ web URL when enabled)."""
        parts: list[str] = []
        if state.project_context_file:
            parts.append(
                f"≡ [bold {ui.ACCENT}]{state.project_context_file}[/] "
                f"[{ui.FG_MUTE}]on demand[/]"
            )
        from ..storage import skills as _skills
        from ..mcp.config import get_config as _get_mcp_config

        sk_count = _skills.skill_count()
        mcp_count = len(_get_mcp_config().list_servers())
        sk_mcp: list[str] = []
        if sk_count > 0:
            sk_mcp.append(
                f"✦ [bold {ui.ACCENT_2}]{sk_count} skill"
                f"{'s' if sk_count != 1 else ''}[/]"
            )
        if mcp_count > 0:
            sk_mcp.append(
                f"⚙ [bold {ui.ACCENT}]{mcp_count} MCP"
                f"{'s' if mcp_count != 1 else ''}[/]"
            )
        if sk_mcp:
            parts.append(
                f"{' & '.join(sk_mcp)} [{ui.FG_MUTE}]auto-invoked when needed[/]"
            )
        from ..storage import pin as pin_store

        pin_body = pin_store.pin_text()
        if pin_body:
            if pin_store.is_enabled():
                parts.append(f"📌 [bold {ui.ACCENT_2}]pinned[/]")
            else:
                parts.append(f"📌 [bold {ui.WARN}]paused[/]")
        if not parts and not self._web_primary_url:
            return None
        sep = f" [{ui.SEP}]·[/] "
        body = sep.join(parts) if parts else ""
        if self._web_primary_url:
            esc = _rich_escape(self._web_primary_url)
            web_line = (
                f"🌐 [{ui.FG_DIM}]remote[/]  [link={esc}]{esc}[/link]  "
                f"[{ui.FG_DIM}]· scan QR top-right · ⌃⇧U copy[/]"
            )
            body = f"{body}\n{web_line}" if body else web_line
        return body

    def _write_context_strip(self, log: RichLog | None = None) -> None:
        """Render the combined context / skills / MCP welcome line."""
        markup = self._context_strip_markup()
        if not markup:
            return
        if log is None:
            log = self.query_one("#transcript", RichLog)
        log.write(
            Panel(
                Text.from_markup(markup),
                title="context",
                title_align="left",
                border_style=ui.ACCENT,
                padding=(0, 1),
            ),
            expand=True,
        )

    # ─── width monitor (reflow on resize) ─────────────────────────────
    def _start_width_monitor(self) -> None:
        """Start polling for transcript width changes to force panel reflow."""
        # Capture initial width to avoid a spurious first rebuild
        try:
            log = self.query_one("#transcript", RichLog)
            self._last_t_width = log.region.width
        except Exception:
            pass
        self._check_width()

    def _check_width(self) -> None:
        """If transcript width changed, rebuild all panels at the new width."""
        try:
            log = self.query_one("#transcript", RichLog)
            try:
                current = log.region.width
            except AttributeError:
                current = 0
            if current and current != self._last_t_width:
                first_measure = self._last_t_width == 0
                self._last_t_width = current
                # Width was unknown at mount (layout hadn't run) — record it
                # without rebuilding, or the first tick stomps the transcript.
                if first_measure:
                    pass
                # During streaming skip the full rebuild — just clear cache
                elif self._busy:
                    log._line_cache.clear()
                    if hasattr(log, '_render_cache'):
                        log._render_cache = {}
                    log.refresh()
                else:
                    self._rebuild_transcript()
        except Exception:
            pass
        self._width_check_timer = self.set_timer(0.3, self._check_width)

    # ─── status bar (single line with everything) ────────────────────
    _STATUS_SEP = f"  [{ui.FG_DIM}]{ui.DOT}[/]  "

    def _build_status_segments(self, *, busy: bool) -> list[str]:
        """Build all segments for the status bar — brand, activity, model,
        agent, session, stats, project context, internals — in one list."""
        segs: list[str] = []

        # ── activity / status ─────────────────────────────────────────
        if busy and self._activity_label:
            i = self._activity_spinner_i % len(self._spinner_frames)
            sp = self._spinner_frames[i]
            step = max(0.0, time.monotonic() - self._activity_t0)
            segs.append(
                f"[{ui.ACCENT}]{sp}[/] [b {ui.FG}]{self._activity_label}[/] "
                f"[{ui.FG_DIM}]{step:.1f}s[/]"
            )
        elif self._status_msg:
            segs.append(f"[{ui.FG}]{self._status_msg}[/]")

        # ── model ─────────────────────────────────────────────────────
        segs.append(f"[b {ui.ACCENT}]{state.MODEL}[/]")

        # ── agent ─────────────────────────────────────────────────────
        segs.append(_agent_badge_markup())

        # ── session ────────────────────────────────────────────────────
        if state.current_session_id is not None:
            segs.append(f"[{ui.FG_DIM}]#{state.current_session_id}[/]")

        # ── messages count ────────────────────────────────────────────
        segs.append(f"[{ui.FG_MUTE}]{len(state.messages)} msgs[/]")

        # ── tokens ────────────────────────────────────────────────────
        segs.append(
            f"[{ui.FG_MUTE}]{state.total_in}[/]→"
            f"[{ui.FG_MUTE}]{state.total_out}[/]"
            f"=[{ui.FG_MUTE}]{state.total_tokens}t[/]"
        )

        # ── plan mode ─────────────────────────────────────────────────
        if state.plan_mode:
            segs.append(f"[b {ui.WARN}]⊘ plan[/]")

        # ── think ─────────────────────────────────────────────────────
        if state.think_mode:
            segs.append(f"[{ui.OK}]think:{state.think_effort}[/]")
        else:
            segs.append(f"[{ui.FG_DIM}]think:off[/]")

        # ── pinned context ────────────────────────────────────────────
        segs.append(_pin_status_markup())

        # ── project context file ──────────────────────────────────────
        if state.project_context_file:
            _ctx_colors = {
                "CLAUDE.md": "rgb(189,147,249)",
                "AGENT.md":  ui.ACCENT,
                "AGENTS.md": ui.ACCENT,
                "JARVIS.md": ui.ACCENT_2,
            }
            _color = _ctx_colors.get(state.project_context_file, ui.FG_MUTE)
            segs.append(f"[{_color}]{state.project_context_file}[/]")

        # ── git branch (cached, refreshed every few seconds) ──────────
        self._refresh_git_branch()
        if self._git_branch:
            segs.append(f"[{ui.ACCENT_2}]⤳ {self._git_branch}[/]")

        # ── internal trace ────────────────────────────────────────────
        if state.show_internal:
            segs.append(f"[{ui.WARN}]trace[/]")

        # ── turn timer ────────────────────────────────────────────────
        if busy and self._turn_t0:
            turn = max(0.0, time.monotonic() - self._turn_t0)
            segs.append(f"[{ui.FG_DIM}]{turn:.0f}s[/]")

        return segs

    def _write_status_line(self, *, busy: bool) -> None:
        try:
            segs = self._build_status_segments(busy=busy)
            line = self._STATUS_SEP.join(segs)
            self.query_one("#statusbar", Static).update(Text.from_markup(line))
        except Exception:
            pass

    def _set_status(self, msg: str):
        self._status_msg = msg or ""
        self._write_status_line(busy=self._busy and bool(self._activity_label))

    # ─── prompt stash (FIFO queue above status bar) ──────────────────
    @staticmethod
    def _stash_preview(msg, max_len: int = 56) -> str:
        text = msg[0] if isinstance(msg, tuple) else msg
        preview = (text or "").replace("\n", " ").strip()
        if len(preview) > max_len:
            preview = preview[: max_len - 1] + "…"
        return _rich_escape(preview)

    def _refresh_queue_bar(self) -> None:
        """Render queued prompts in the bar above the status strip (not transcript)."""
        try:
            bar = self.query_one("#queuebar", Static)
        except Exception:
            return
        q = state.prompt_queue
        if not q:
            bar.add_class("hidden")
            bar.update("")
            return
        bar.remove_class("hidden")
        n = len(q)
        word = "message" if n == 1 else "messages"
        header = (
            f"[{ui.WARN}]☰ queued[/] "
            f"[{ui.FG_DIM}]({n} {word} — sends when current turn finishes)[/]"
        )
        rows: list[str] = []
        for i, msg in enumerate(q, 1):
            tag = "next" if i == 1 else "wait"
            rows.append(
                f"  [{ui.WARN}]{tag}[/] [{ui.FG_DIM}]#{i}[/] "
                f"[{ui.FG}]{self._stash_preview(msg, 96)}[/]"
            )
        bar.update(Text.from_markup(header + "\n" + "\n".join(rows)))
        self._sync_web_queue()

    # Parallel-file "tool dock" panel methods (_tool_run_glyph,
    # _format_tool_dock_row, reset_tool_activity_panel, _parallel_panel_verb,
    # _build_tool_activity_panel, _refresh_tool_dock) live in ActivityMixin
    # (jarvis/tui/mixins/activity.py).

    def _stash_prompt(self, text: str) -> None:
        """Queue a prompt while the agent is busy (FIFO; shown only in #queuebar)."""
        from ..prompt_attachments import snapshot_registry

        state.prompt_queue.append((text, snapshot_registry()))
        self._refresh_queue_bar()
        if self._busy and self._activity_label:
            self._write_status_line(busy=True)

    # Activity-spinner methods (_sync_activity_phase, _tick_activity_spinner,
    # _refresh_activity_widgets, _start_activity_pulse, _stop_activity_pulse)
    # live in ActivityMixin (jarvis/tui/mixins/activity.py).

    # ─── palette ─────────────────────────────────────────────────────
    def _run_attachment_tokenize(self) -> None:
        if self._tokenizing_attachments:
            return
        try:
            inp = self.query_one("#prompt", PromptArea)
        except Exception:
            return
        val = inp.text or ""
        from ..prompt_attachments import extract_droppable_paths, tokenize_dropped_paths

        if not extract_droppable_paths(val):
            return

        row, col = self._prompt_cursor()
        tokenized, new_row, new_col = tokenize_dropped_paths(
            val,
            cursor_row=row,
            cursor_col=col,
        )
        if tokenized == val:
            return
        self._tokenizing_attachments = True
        try:
            inp.text = tokenized
            if new_row is not None and new_col is not None:
                inp.move_cursor((new_row, new_col))
            self._last_input_value = tokenized
            inp.refresh_file_ref_highlights()
        finally:
            self._tokenizing_attachments = False

    # The @file picker core (on_text_area_changed, _prompt_cursor,
    # _populate/_sync/close/_accept/try_accept_file_ref, key + option handlers)
    # lives in FileRefPickerMixin (jarvis/tui/mixins/file_ref.py).

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
            if _is_provider_hub_command(cmd):
                self._open_provider_hub()
                inp.focus()
                return
            if _is_think_picker_command(cmd):
                self._open_think_picker()
                inp.focus()
                return
            if _is_mcp_modal_command(cmd):
                self._open_mcp_modal()
                inp.focus()
                return
            if _is_agent_picker_command(cmd):
                self._open_agent_picker()
                inp.focus()
                return
            if _is_skill_picker_command(cmd):
                self._open_skill_browser()
                inp.focus()
                return
            if _is_command_manager_command(cmd):
                self._open_command_manager()
                return
            if _is_memory_modal_command(cmd):
                self._open_memory_modal()
                inp.focus()
                return
            if _is_pin_modal_command(cmd):
                self._open_pin_modal()
                inp.focus()
                return
            if _is_lesson_modal_command(cmd):
                self._open_lesson_modal()
                inp.focus()
                return
            if _is_settings_modal_command(cmd):
                self._open_settings_modal()
                inp.focus()
                return
            if _is_theme_modal_command(cmd):
                self._open_theme_modal()
                inp.focus()
                return
            if _is_local_command(cmd):
                rest = cmd[len("/local "):] if cmd.startswith("/local ") else ""
                self._open_local_cmd_modal(initial=rest)
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

        from .palette_modal import CommandPaletteScreen

        self.push_screen(CommandPaletteScreen(), after)

    def _open_model_picker(self):
        def after(option_id: str | None):
            if not option_id:
                self._tui_console.print(f"[{ui.FG_DIM}]model picker cancelled[/]")
                return
            from ..constants.providers import parse_model_option_id
            source, model_id = parse_model_option_id(option_id)
            self._apply_model_selection_worker(model_id, source=source)
        from .model_modal import ModelPickerScreen

        self.push_screen(ModelPickerScreen(), after)

    def _open_think_picker(self):
        def after(effort: str | None):
            if not effort:
                self._tui_console.print(f"[{ui.FG_DIM}]thinking picker cancelled[/]")
                return
            from ..commands.control import _handle_think
            _handle_think(effort)
            self._set_status("ready")
        from .think_modal import ThinkPickerScreen

        self.push_screen(ThinkPickerScreen(), after)

    def _open_provider_picker(self):
        def after(provider: str | None):
            if not provider:
                self._tui_console.print(f"[{ui.FG_DIM}]provider picker cancelled[/]")
                return
            self._switch_provider_worker(provider)
        from .provider_modal import ProviderPickerScreen

        self.push_screen(ProviderPickerScreen(), after)

    @work(thread=True)
    def _switch_provider_worker(self, provider: str, *, skip_key_prompt: bool = False) -> None:
        """Run provider switch off the main thread so key prompts can open modals."""
        from ..commands.control import _handle_provider

        try:
            _handle_provider(provider, skip_key_prompt=skip_key_prompt)
        except RuntimeError as e:
            self.call_from_thread(
                lambda err=e: self._tui_console.print(f"[{ui.ERR}]provider switch failed: {err}[/]")
            )
        finally:
            self.call_from_thread(self._provider_action_done)

    @work(thread=True)
    def _apply_model_selection_worker(self, model_id: str, *, source: str = "") -> None:
        """Run model selection off the main thread (may prompt for API keys)."""
        from ..commands.control import _apply_model_selection

        try:
            _apply_model_selection(model_id, source=source)
        except RuntimeError as e:
            self.call_from_thread(
                lambda err=e: self._tui_console.print(f"[{ui.ERR}]model switch failed: {err}[/]")
            )
        finally:
            self.call_from_thread(self._provider_action_done)

    def _provider_action_done(self) -> None:
        self._write_status_line(busy=False)
        self._set_status("ready")

    def _open_mcp_modal(self):
        def after(_: object) -> None:
            from ..mcp.scope import invalidate_mcp_prompt_cache
            invalidate_mcp_prompt_cache()
            self._set_status("ready")
        from .mcp_modal import MCPModalScreen

        self.push_screen(MCPModalScreen(), after)

    def _open_agent_picker(self):
        def after(result: object) -> None:
            if result is None:
                self._tui_console.print(f"[{ui.FG_DIM}]agent picker cancelled[/]")
                return
            if result == "off":
                state.set_active_agent(None)
                self._set_status("ready")
                return
            if isinstance(result, dict):
                state.set_active_agent(result)
                self._set_status("ready")
                return
            self._set_status("ready")
        from .agent_modal import AgentPickerScreen

        self.push_screen(AgentPickerScreen(), after)

    def _open_skill_browser(self):
        def after(_: object) -> None:
            self._set_status("ready")
        from .skill_modal import SkillBrowserScreen

        self.push_screen(SkillBrowserScreen(), after)

    def _open_command_manager(self):
        """Open the custom-command manager modal.

        A string dismiss value (an `/<name> ` invocation or a full template)
        lands in the prompt box so the user can edit it before sending.
        """
        def after(result: object) -> None:
            inp = self.query_one("#prompt", PromptArea)
            if isinstance(result, str) and result:
                inp.text = result
                lines = result.split("\n")
                inp.move_cursor((len(lines) - 1, len(lines[-1])))
                self._last_input_value = result
            inp.focus()
            self._set_status("ready")
        from .command_modal import CommandManagerScreen

        self.push_screen(CommandManagerScreen(), after)

    def _open_memory_modal(self):
        def after(_: object) -> None:
            self._set_status("ready")
        from .memory_modal import MemoryModalScreen

        self.push_screen(MemoryModalScreen(), after)

    def _open_pin_modal(self):
        def after(_: object) -> None:
            self._write_status_line(busy=self._busy and bool(self._activity_label))
            self._write_context_strip()
            self._set_status("ready")
        from .pin_modal import PinModalScreen

        self.push_screen(PinModalScreen(), after)

    def _open_lesson_modal(self):
        def after(_: object) -> None:
            self._set_status("ready")
        from .lesson_modal import LessonModalScreen

        self.push_screen(LessonModalScreen(), after)

    def _open_settings_modal(self):
        def after(_: object) -> None:
            self._set_status("ready")
        from .settings_modal import SettingsModalScreen

        self.push_screen(SettingsModalScreen(), after)

    def _apply_theme_runtime(self, name: str, *, rebuild_transcript: bool) -> None:
        """Apply a TUI theme immediately without requiring a restart."""
        from . import theme as _tui_theme
        from .modal_chrome import reload_chrome_css
        import inspect

        _tui_theme.set_theme(name)
        reload_chrome_css()
        if name in state.THEMES:
            state.theme = name
            state.theme_colors = dict(state.THEMES[name])

        try:
            prompt = self.query_one("#prompt", PromptArea)
            from .prompt_highlight import PROMPT_THEME_NAME, build_prompt_text_area_theme

            prompt.register_theme(build_prompt_text_area_theme())
            prompt._set_theme(PROMPT_THEME_NAME)
            prompt.refresh_file_ref_highlights()
        except Exception:
            pass

        # Rebuild the app stylesheet with both global and modal chrome CSS so
        # the currently open /theme modal previews the color change instantly.
        app_path = ""
        try:
            app_path = inspect.getfile(self.__class__)
        except (TypeError, OSError):
            pass
        read_from = (app_path, f"{self.__class__.__name__}.CSS")
        self.stylesheet.add_source(
            _tui_theme.GLOBAL_CSS + _tui_theme.MODAL_CSS,
            read_from=read_from,
            is_default_css=False,
        )
        self.stylesheet.reparse()
        self.stylesheet.update(self)
        self.refresh(layout=True)
        try:
            self.screen.refresh(layout=True)
        except Exception:
            pass

        if rebuild_transcript:
            self._rebuild_transcript()
        else:
            self._render_hintbar()
            self._write_status_line(busy=self._busy and bool(self._activity_label))

    def _open_theme_modal(self):
        def after(name: object) -> None:
            if name and isinstance(name, str):
                self._apply_theme_runtime(name, rebuild_transcript=True)
                self._tui_console.print(f"[{ui.OK}]✓ switched to [bold]{name}[/] theme[/]")
            self._set_status("ready")
        from .theme_modal import ThemePickerScreen

        self.push_screen(ThemePickerScreen(), after)

    def _open_oauth_modal(self, *, title: str = "OAuth login") -> None:
        def after(result) -> None:
            if result is None:
                self._tui_console.print(f"[{ui.FG_DIM}]oauth login closed[/]")
            elif result.action == "connected" and result.spec_id == "anthropic":
                self._tui_console.print(
                    f"[{ui.OK}]{ui.CHECK}[/] [bold]Signed in with Anthropic OAuth[/]"
                )
                if result.model_ids:
                    from ..auth.anthropic_models import format_anthropic_model_lines
                    self._tui_console.print(f"[{ui.FG_DIM}]Available models:[/]")
                    for line in format_anthropic_model_lines(result.model_ids):
                        self._tui_console.print(f"  [{ui.ACCENT}]{line}[/]")
            elif result.action == "connected" and result.spec_id == "openai_codex":
                self._tui_console.print(
                    f"[{ui.OK}]{ui.CHECK}[/] [bold]Signed in with OpenAI Codex OAuth[/]"
                )
                if result.model_ids:
                    self._tui_console.print(f"[{ui.FG_DIM}]Available models:[/]")
                    for mid in result.model_ids:
                        self._tui_console.print(f"  [{ui.ACCENT}]{mid}[/]")
            elif result.message:
                color = ui.OK if result.message.startswith("✓") else ui.WARN
                self._tui_console.print(f"[{color}]{result.message}[/]")
            self._write_status_line(busy=False)
            self._set_status("ready")

        from .oauth_connect_modal import OAuthConnectModalScreen

        self.push_screen(OAuthConnectModalScreen(title=title), after)

    def _open_login_modal(self):
        self._open_oauth_modal(title="Sign in")

    def _open_provider_hub(self) -> None:
        """Open the unified provider setup hub.

        Dismisses with one of: "oauth", "api", or None.
        The hub stays simple — it just returns a result; we handle the
        sub-screen navigation here to keep the screen stack clean.
        """
        def after(mode: object) -> None:
            if mode == "oauth":
                self.push_screen(
                    OAuthConnectModalScreen(title="Auth · OAuth"),
                    lambda _r: self._set_status("ready"),
                )
            elif mode == "api":
                self.push_screen(KeyModalScreen(), lambda _r: self._set_status("ready"))
            else:
                self._set_status("ready")

        from .provider_hub import ProviderHubScreen
        from .oauth_connect_modal import OAuthConnectModalScreen
        from .key_modal import KeyModalScreen

        self.push_screen(ProviderHubScreen(), after)

    def _open_key_modal(self) -> None:
        def after(_: object) -> None:
            self._set_status("ready")
        from .key_modal import KeyModalScreen

        self.push_screen(KeyModalScreen(), after)

    def _open_local_cmd_modal(self, initial: str = "") -> None:
        def after(cmd: str | None) -> None:
            inp = self.query_one("#prompt", PromptArea)
            if not cmd:
                inp.focus()
                self._set_status("ready")
                return
            # Extract bare command: "/cd <dir>" → "/cd"
            base = cmd.split(None, 1)[0]
            has_args = len(cmd.split(None, 1)) > 1
            if has_args:
                # Commands with args — put in prompt with trailing space
                inp.text = base + " "
                inp.move_cursor((0, len(inp.text)))
                self._last_input_value = inp.text
            else:
                # No-arg commands — dispatch immediately
                inp.clear()
                self._last_input_value = ""
                self._dispatch_palette_slash(base)
            inp.focus()
            self._set_status("ready")

        from .local_cmd_modal import LocalCmdModalScreen

        self.push_screen(LocalCmdModalScreen(initial=initial), after)

    def _dispatch_palette_slash(self, inp: str):
        from ..commands.dispatch import handle_slash

        text = (inp or "").strip()
        if not text.startswith("/"):
            return
        if text in ("/exit", "/quit"):
            self.exit()
            return
        head = text.split(maxsplit=1)[0]
        if head[1:] in state.aliases:
            rest = text[len(head):]
            text = state.aliases[head[1:]] + rest

        if self._busy:
            from ..prompt_attachments import reset_registry
            self._stash_prompt(text)
            reset_registry()
            return

        if head in ("/model", "/mode", "/session", "/sessions"):
            # Route these to their proper modal/command handler
            # rather than treating them as "thinking" interactions.
            self._route_modal_slash(text)
            return

        if _is_upgrade_command(text):
            self._begin_turn(text)
            return

        if _is_provider_hub_command(text):
            self._open_provider_hub()
            return

        result, should_send, new_inp = handle_slash(text)
        if result == "exit":
            self.exit()
            return
        if should_send and new_inp:
            log = self.query_one("#transcript", RichLog)
            log.write(self._user_panel(new_inp), expand=True)
            self._busy = True
            self._turn_t0 = time.monotonic()
            self._sync_activity_phase("Thinking…")
            self._start_activity_pulse()
            self._set_status("thinking…")
            self._run_turn(new_inp)
            return
        self._set_status("ready")

    # ─── route modal / non-thinking slash commands ───────────────────
    def _route_modal_slash(self, inp: str) -> None:
        """Route slash commands that should open modals rather than be
        dispatched as 'thinking' API interactions.

        Handles: /login, /logout, /auth, /key, /model, /session
        """
        text = (inp or "").strip()
        head = text.split(maxsplit=1)[0]

        if head in ("/model", "/mode"):
            arg = text.split(maxsplit=1)[1] if " " in text else ""
            if arg:
                self._apply_model_selection_worker(arg)
            else:
                self._open_model_picker()
            return

        if head == "/session":
            self._open_session_picker()
            return

        if head == "/provider":
            self._open_provider_hub()
            return

        self._set_status("ready")

    def _handle_queued_command(self, cmd: str) -> None:
        """Handle a queued slash command that should open a modal or
        be processed without showing 'thinking…' status.

        This is the queue-aware counterpart of the per-command
        intercepts in on_prompt_area_submitted.
        """
        stripped = cmd.strip()

        # Map head -> handler
        if stripped == "/provider" or stripped.startswith("/provider "):
            self._open_provider_hub()
            return
        if stripped in ("/session", "/sessions", "/session list", "/session ls"):
            self._open_session_picker()
            return
        if stripped in ("/model", "/mode"):
            self._open_model_picker()
            return
        if stripped in ("/think",):
            self._open_think_picker()
            return
        if stripped in ("/mcp",):
            self._open_mcp_modal()
            return
        if stripped in ("/agent",):
            self._open_agent_picker()
            return
        if stripped in ("/skill", "/skills"):
            self._open_skill_browser()
            return
        if stripped in ("/command", "/commands"):
            self._open_command_manager()
            return
        if stripped in ("/memory",):
            self._open_memory_modal()
            return
        if stripped in ("/pin",):
            self._open_pin_modal()
            return
        if stripped in ("/lesson",):
            self._open_lesson_modal()
            return
        if stripped in ("/settings", "/setting"):
            self._open_settings_modal()
            return
        if _is_provider_hub_command(stripped):
            self._open_provider_hub()
            return
        if stripped in ("/theme",):
            self._open_theme_modal()
            return

        head = stripped.split(maxsplit=1)[0]
        if head in ("/local",):
            log = self.query_one("#transcript", RichLog)
            log.write(self._user_panel(stripped), expand=True)
            self._busy = True
            self._turn_t0 = time.monotonic()
            self._sync_activity_phase("Processing…")
            self._start_activity_pulse()
            self._set_status("processing…")
            self._run_turn(stripped)
            return

        if head in ("/think",):
            self._open_think_picker()
            return

        # Default: treat as a normal turn (will show "thinking…")
        self._begin_turn(stripped)

    # ─── panels ──────────────────────────────────────────────────────
    @staticmethod
    def _user_panel(text: str) -> Panel:
        return Panel(
            Markdown(text),
            title="you",
            title_align="left",
            border_style=ui.OK,
            padding=(0, 1),
        )

    def action_escape_action(self):
        from ..repl.stream import cancel_current_stream
        cancel_current_stream()
        if hasattr(self._tui_console, "cancel_pending_prompts"):
            self._tui_console.cancel_pending_prompts()
        self._sync_activity_phase("Cancelling…")
        if self._busy:
            self._tui_console.print(f"[{ui.WARN}]⏹ cancelled by user[/]")
            self._turn_done()

    # ─── input handling ──────────────────────────────────────────────
    def on_prompt_area_submitted(self, event: "PromptArea.Submitted") -> None:
        raw = event.value or ""
        text = raw.strip()
        inp = self.query_one("#prompt", PromptArea)
        inp.clear()
        self._last_input_value = ""
        self.close_file_ref_picker()
        if not text:
            from ..prompt_attachments import reset_registry
            reset_registry()
            return

        if _is_bare_model_command(text):
            self._open_model_picker()
            return
        if _is_provider_hub_command(text):
            self._open_provider_hub()
            return
        if _is_think_picker_command(text):
            self._open_think_picker()
            return
        if _is_mcp_modal_command(text):
            self._open_mcp_modal()
            return
        if _is_agent_picker_command(text):
            self._open_agent_picker()
            return
        if _is_skill_picker_command(text):
            self._open_skill_browser()
            return
        if _is_command_manager_command(text):
            self._open_command_manager()
            return
        if _is_memory_modal_command(text):
            self._open_memory_modal()
            return
        if _is_pin_modal_command(text):
            self._open_pin_modal()
            return
        if _is_lesson_modal_command(text):
            self._open_lesson_modal()
            return
        if _is_settings_modal_command(text):
            self._open_settings_modal()
            return
        if _is_theme_modal_command(text):
            self._open_theme_modal()
            return

        if _is_local_command(text):
            rest = text[len("/local "):] if text.startswith("/local ") else ""
            self._open_local_cmd_modal(initial=rest)
            return

        stripped = text.strip()
        if stripped in ("/session", "/sessions", "/session list", "/session ls"):
            self._open_session_picker()
            return

        if stripped in ("/exit", "/quit"):
            self.exit()
            return

        if stripped == "/keytest":
            self._key_debug = True
            self._tui_console.print(
                f"[{ui.WARN}]⬟ keytest armed —[/] press any key to see what your "
                "terminal sent (one shot)."
            )
            return

        if _is_provider_hub_command(stripped):
            self._open_provider_hub()
            return

        if self._busy:
            from ..prompt_attachments import reset_registry
            self._stash_prompt(text)
            reset_registry()
            return

        self._begin_turn(text)

    def _begin_turn(self, inp: str) -> None:
        state.cancel_requested.clear()

        log = self.query_one("#transcript", RichLog)
        upgrading = _is_upgrade_command(inp)
        if upgrading:
            arg = inp.strip().split(maxsplit=1)[1].strip().lower() if " " in inp.strip() else ""
            checking = arg == "check"
            phase = "checking…" if checking else "upgrading…"
            self._tui_console.start_command_progress(inp.strip(), phase=phase)
        else:
            log.write(self._user_panel(inp), expand=True)
        if self._web_bridge is not None:
            self._web_bridge.emit(
                "message",
                {"role": "you", "text": inp, "title": "you"},
            )
        self._busy = True
        self._turn_t0 = time.monotonic()
        if upgrading:
            self._sync_activity_phase("Checking…" if checking else "Upgrading…")
            self._set_status("checking…" if checking else "upgrading…")
        else:
            self._sync_activity_phase("Thinking…")
            self._set_status("thinking…")
        self._start_activity_pulse()
        self._sync_web_busy()
        self._run_turn(inp)

    # ─── session picker ──────────────────────────────────────────────
    def _open_session_picker(self):
        def after(sid):
            if sid is None:
                self._tui_console.print(f"[{ui.FG_DIM}]cancelled[/]")
                return
            from .session_modal import resume_session_into_state

            if resume_session_into_state(sid, self._tui_console.print, preview=False):
                self._render_loaded_session()
        from .session_modal import SessionPickerScreen

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

    def _compact_file_blocks(self, content) -> tuple[list[dict], int]:
        """File tool_use blocks and count of tool_result blocks in this message."""
        if isinstance(content, str):
            return [], 0
        uses: list[dict] = []
        n_results = 0
        for block in content:
            data = self._block_dict(block)
            kind = data.get("type")
            if kind == "tool_use" and is_dock_tool(data.get("name", "")):
                uses.extend(expand_dock_tool_use(data))
            elif kind == "tool_result":
                n_results += 1
        return uses, n_results

    def _write_compact_file_summary_panel(self, file_uses: list[dict]) -> None:
        """One summary panel for session replay (replaces N tool: / tool result panels)."""
        if len(file_uses) < 2:
            return
        log = self.query_one("#transcript", RichLog)
        rows: list[str] = []
        for data in file_uses:
            name = data.get("name", "tool")
            label = data.get("_dock_label")
            if not label:
                label, _ = _display_for_tool(name, data.get("input"))
            if "/" in label:
                base, _, parent = label.rpartition("/")
                path_bit = f"[{ui.FG}]{_rich_escape(base)}[/] [{ui.FG_DIM}]{_rich_escape(parent)}[/]"
            else:
                path_bit = f"[{ui.FG}]{_rich_escape(label)}[/]"
            rows.append(f"  [{ui.OK}]✓[/] [{ui.FG_MUTE}]{name:<14}[/] {path_bit}")
        rows.append(f"[{ui.FG_DIM}]^F inspect files[/]")
        log.write(
            Panel(
                Text.from_markup("\n".join(rows)),
                title=f"⚡ {len(file_uses)} parallel files",
                title_align="left",
                border_style=ui.ACCENT,
                padding=(0, 1),
            ),
            expand=True,
        )

    def _render_internal_blocks(self, content) -> None:
        if isinstance(content, str):
            return
        log = self.query_one("#transcript", RichLog)
        file_uses, n_tool_results = self._compact_file_blocks(content)
        hide_file_tool_uses = len(file_uses) >= 2
        hide_tool_results = n_tool_results >= 2

        if hide_file_tool_uses and state.show_internal:
            self._write_compact_file_summary_panel(file_uses)

        for block in content:
            data = self._block_dict(block)
            kind = data.get("type")
            if kind == "thinking":
                if not state.show_internal:
                    continue
                body = data.get("thinking", "")
                if body:
                    log.write(
                        Panel(
                            Text(body, style=f"{ui.FG_DIM}"),
                            title="thinking",
                            title_align="left",
                            border_style=ui.SEP,
                            padding=(0, 1),
                        ),
                        expand=True,
                    )
                continue
            if not state.show_internal:
                continue
            if kind == "tool_use":
                name = data.get("name", "tool")
                if hide_file_tool_uses and is_dock_tool(name):
                    continue
                args = str(data.get("input", ""))[:800]
                log.write(
                    Panel(
                        Text(args),
                        title=f"tool: {name}",
                        title_align="left",
                        border_style=ui.WARN,
                        padding=(0, 1),
                    ),
                    expand=True,
                )
            elif kind == "tool_result":
                if hide_tool_results:
                    continue
                body = data.get("content", "")
                if isinstance(body, list):
                    body = "\n".join(
                        item.get("text", "") for item in body if isinstance(item, dict)
                    )
                log.write(
                    Panel(
                        Text(str(body)[:2000], style=f"{ui.FG_DIM}"),
                        title="tool result",
                        title_align="left",
                        border_style=ui.SEP,
                        padding=(0, 1),
                    ),
                    expand=True,
                )

    def _render_loaded_session(self) -> None:
        from ..repl.banners import welcome_banner

        log = self.query_one("#transcript", RichLog)
        log.clear()
        welcome_banner()
        self._tui_console.print(
            f"[{ui.OK}]▶ resumed session #{state.current_session_id} "
            f"({len(state.messages)} messages)[/]"
        )
        for msg in state.messages:
            role = msg.get("role", "")
            content = msg.get("content", "")
            text = self._content_text(content).strip()
            if role == "user":
                if text:
                    log.write(self._user_panel(text), expand=True)
                self._render_internal_blocks(content)
            elif role == "assistant":
                self._render_internal_blocks(content)
                if text:
                    log.write(
                        Panel(
                            Markdown(text),
                            title="jarvis",
                            title_align="left",
                            border_style=ui.ACCENT_2,
                            padding=(0, 1),
                        ),
                        expand=True,
                    )
        backfill_tool_output_history()
        self._set_status("session loaded")

    def _rebuild_transcript(self) -> None:
        """Clear and re-render the full transcript with current theme colors.

        Called after a mid-session theme switch so the welcome art, message
        panels, project-context panels, and status/hint bars all reflect the
        new palette.
        """
        from ..repl.banners import welcome_banner

        log = self.query_one("#transcript", RichLog)
        log.clear()

        # Welcome art + panel — uses live theme colors via banners._theme_colors()
        welcome_banner()

        self._write_context_strip(log)

        # Re-play existing messages (border styles pick up current ui.* tokens).
        for msg in state.messages:
            role = msg.get("role", "")
            content = msg.get("content", "")
            text = self._content_text(content).strip()
            if role == "user":
                if text:
                    log.write(self._user_panel(text), expand=True)
                self._render_internal_blocks(content)
            elif role == "assistant":
                self._render_internal_blocks(content)
                if text:
                    log.write(
                        Panel(
                            Markdown(text),
                            title="jarvis",
                            title_align="left",
                            border_style=ui.ACCENT_2,
                            padding=(0, 1),
                        ),
                        expand=True,
                    )

        # Refresh status bar, hint bar, and composer prefix with new colors.
        # Keep whatever status message was showing — a resize-triggered
        # rebuild must not stomp feedback like "✓ copied …".
        self._render_hintbar()
        self._set_status(self._status_msg or "ready")

    @work(thread=True, exclusive=True)
    def _run_turn(self, inp: str) -> None:
        """Mirror of jarvis.main._send_and_loop, adapted for the TUI."""
        from ..commands.dispatch import handle_slash
        from ..repl.stream import call_claude_stream
        from ..repl.render import (
            render_assistant,
            classify_empty_turn,
            empty_turn_message,
        )
        from ..storage.sessions import db_append_message, db_set_title_if_empty

        # Stale anchors/flags from a cancelled previous turn must never
        # survive into this one — a later abort would truncate at them and
        # wipe everything written since.
        self._tui_console.reset_stream_ui()

        try:
            if inp.startswith("/"):
                head = inp.split(maxsplit=1)[0]
                if head[1:] in state.aliases:
                    rest = inp[len(head):]
                    inp = state.aliases[head[1:]] + rest

            if inp.startswith("!"):
                cmd = inp[1:].strip()
                if cmd:
                    from ..tools.shell import run_bash
                    prev = state.auto_approve
                    state.auto_approve = True
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

            from ..prompt_refs import expand_file_refs
            from ..prompt_attachments import expand_attachment_tokens, reset_registry

            expanded, attached = expand_file_refs(inp)
            if attached:
                names = ", ".join(attached)
                self.call_from_thread(
                    lambda n=names: self._tui_console.print(
                        f"[{ui.FG_DIM}]▣ attached {len(attached)} file(s): {n}[/]"
                    )
                )
            inp = expanded

            expanded, dropped = expand_attachment_tokens(inp)
            if dropped:
                labels = ", ".join(dropped)
                self.call_from_thread(
                    lambda n=labels: self._tui_console.print(
                        f"[{ui.FG_DIM}]▣ dropped {len(dropped)} file(s): {n}[/]"
                    )
                )
            inp = expanded
            reset_registry()

            if state.client is None:
                from ..auth.client import make_client
                state.client = make_client(interactive=False)
            if state.client is None:
                self.call_from_thread(
                    lambda: self._tui_console.print(
                        f"[{ui.WARN}]Not signed in[/] — run [cyan]/login[/] "
                        "(OAuth) or [cyan]/key[/] (API keys) first"
                    )
                )
                return

            user_msg = {"role": "user", "content": inp}
            state.messages.append(user_msg)
            state.web_tool_used_this_turn = False
            if state.current_session_id:
                db_append_message(state.current_session_id, len(state.messages) - 1, user_msg)
                db_set_title_if_empty(state.current_session_id, inp)

            empty_retries = 0
            while True:
                if state.cancel_requested.is_set():
                    raise KeyboardInterrupt()
                resp = call_claude_stream()
                # A turn with no visible output (no text, no tool calls) would
                # otherwise break the loop and leave a blank screen — the "it
                # just stops / no API response" symptom. Retry once for a
                # transient empty, then surface a clear message.
                decision = classify_empty_turn(resp, empty_retries)
                if decision in ("retry", "stop"):
                    self._tui_console.assistant_stream_abort()
                    if decision == "retry":
                        empty_retries += 1
                        self._tui_console.print(
                            f"[{ui.FG_DIM}]⚠ empty response — retrying…[/]"
                        )
                        continue
                    self._tui_console.print(
                        f"[{ui.WARN}]{_rich_escape(empty_turn_message(resp))}[/]"
                    )
                    break
                empty_retries = 0
                asst_msg = {"role": "assistant", "content": resp.content}
                state.messages.append(asst_msg)
                if state.current_session_id:
                    db_append_message(state.current_session_id, len(state.messages) - 1, asst_msg)
                more = render_assistant(resp)
                if resp.stop_reason == "end_turn" or not more:
                    break
                if state.cancel_requested.is_set():
                    raise KeyboardInterrupt()
                if state.current_session_id and state.messages and state.messages[-1] is not asst_msg:
                    db_append_message(state.current_session_id, len(state.messages) - 1, state.messages[-1])
        except KeyboardInterrupt:
            self._tui_console.assistant_stream_abort()
        except SystemExit:
            self._tui_console.assistant_stream_abort()
        except Exception as e:
            from ..console import HarnessAPIError
            if isinstance(e, HarnessAPIError):
                self._tui_console.assistant_stream_abort()
                return
            self._tui_console.assistant_stream_abort()
            self._tui_console.print(f"[{ui.ERR}]error: {_rich_escape(type(e).__name__)}: {_rich_escape(str(e))}[/]")
        finally:
            self._tui_console.assistant_stream_abort()
            self.call_from_thread(self._turn_done)

    def _turn_done(self):
        state.cancel_requested.clear()
        finish_cmd = getattr(self._tui_console, "finish_command_progress", None)
        if callable(finish_cmd):
            finish_cmd()
        self._busy = False
        self._turn_t0 = 0.0
        self._stop_activity_pulse()
        self._sync_activity_phase("")
        self._sync_web_busy()

        if state.prompt_queue:
            item = state.prompt_queue.pop(0)
            if isinstance(item, tuple):
                next_prompt = item[0]
                if len(item) > 1 and isinstance(item[1], tuple):
                    attachments, llm_paths = item[1]
                else:
                    attachments, llm_paths = item[1], None
                from ..prompt_attachments import restore_registry
                restore_registry(attachments, llm_paths)
            else:
                next_prompt = item
            next_prompt = next_prompt.strip()
            remaining = len(state.prompt_queue)
            self._refresh_queue_bar()

            # Route modal-opening commands to their proper handler
            # instead of sending through _begin_turn → _run_turn
            # which sets misleading "thinking…" status.
            if next_prompt.startswith("/"):
                head = next_prompt.split(maxsplit=1)[0]
                if head in ("/model", "/mode", "/session", "/sessions",
                            "/settings",
                            "/theme", "/agent", "/skills", "/memory",
                            "/lesson", "/mcp", "/think", "/local"):
                    self._handle_queued_command(next_prompt)
                    return
                if _is_provider_hub_command(next_prompt):
                    self._open_provider_hub()
                    return

            self._begin_turn(next_prompt)
            return

        self._set_status("ready")
        self.query_one("#prompt", PromptArea).focus()

    def action_cycle_agent(self) -> None:
        if self.file_ref_picker_active:
            self.try_accept_file_ref()
            return
        from ..storage import agents as ag

        try:
            prompt = self.query_one("#prompt", PromptArea)
            if prompt.text.strip():
                return
        except Exception:
            pass

        agents = ag.discover_agents()
        cycle: list[tuple[str, dict | None]] = [("", None)]
        for a in sorted(agents, key=lambda x: x["name"]):
            cycle.append((a["name"], a))

        cur = state.active_agent_name or ""
        idx = 0
        for i, (nm, _rec) in enumerate(cycle):
            if nm == cur:
                idx = i
                break
        _next_name, next_rec = cycle[(idx + 1) % len(cycle)]
        state.set_active_agent(next_rec)

        self._set_status("ready")

    def action_toggle_internal(self):
        state.show_internal = not state.show_internal
        state.save_trace_config()
        on = state.show_internal
        mode = "on" if on else "off"
        self._render_hintbar()

        if self._busy:
            self._set_status(f"trace {mode} — applies after this turn")
            try:
                self._tui_console.print(
                    f"[{ui.FG_DIM}]trace {mode} — transcript updates when this turn finishes[/]"
                )
            except Exception:
                pass
            return

        shown = "shown" if on else "hidden"
        self._set_status(f"trace {shown}")
        if state.current_session_id and state.messages:
            self._render_loaded_session()
        else:
            try:
                self._tui_console.print(f"[{ui.FG_DIM}]internal tool trace {shown}[/]")
            except Exception:
                pass

    def action_show_shortcuts(self) -> None:
        from .shortcuts_modal import ShortcutsHelpScreen

        self.push_screen(ShortcutsHelpScreen())

    def action_open_tools_inspector(self) -> None:
        """^F — browse file reads and all tool output in one modal."""
        if not inspector_has_entries():
            try:
                self._tui_console.print(
                    f"[{ui.FG_DIM}]no tool output yet — run a tool, then ^F[/]"
                )
            except Exception:
                pass
            return
        from .tools_inspector_modal import ToolsInspectorScreen

        self.push_screen(ToolsInspectorScreen())

    def action_open_tool_output(self) -> None:
        self.action_open_tools_inspector()

    def action_open_file_activity(self) -> None:
        self.action_open_tools_inspector()

    def _copy_to_system_clipboard(self, text: str) -> bool:
        from ..utils.clipboard import copy_text_to_clipboard

        return copy_text_to_clipboard(text)

    def action_copy_last_reply(self) -> None:
        """⌃Y — copy the last assistant reply as clean text (no borders/padding)."""
        from ..utils.clipboard import normalize_copy_text

        text = normalize_copy_text(state.last_assistant_text or "")
        if not text:
            self._set_status("nothing to copy yet")
            return
        try:
            self.copy_to_clipboard(text)  # OSC52 — works over SSH
        except Exception:
            pass
        sys_ok = self._copy_to_system_clipboard(text)
        n = len(text)
        if sys_ok:
            self._set_status(f"✓ copied last reply ({n} chars) — /copy code · /copy all")
        else:
            self._set_status(f"copied to terminal clipboard ({n} chars)")

    def action_cancel_or_quit(self):
        try:
            selected = self.screen.get_selected_text()
        except Exception:
            selected = None
        if selected:
            try:
                self.copy_to_clipboard(selected)
            except Exception:
                pass
            sys_ok = self._copy_to_system_clipboard(selected)
            try:
                self.screen.clear_selection()
            except Exception:
                pass
            self._set_status("copied" if sys_ok else "copied (terminal)")
            return
        if self._busy:
            from ..repl.stream import cancel_current_stream
            self._sync_activity_phase("Cancelling…")
            cancel_current_stream()
            if hasattr(self._tui_console, "cancel_pending_prompts"):
                self._tui_console.cancel_pending_prompts()
            self._tui_console.print(f"[{ui.WARN}]⏹ cancelled by user[/]")
            self._turn_done()
            return

        now = time.monotonic()
        if now - self._last_ctrl_c_t < 2.0:
            from ..repl.stream import cancel_current_stream
            cancel_current_stream()
            if hasattr(self._tui_console, "cancel_pending_prompts"):
                self._tui_console.cancel_pending_prompts()
            self.exit()
            return
        self._last_ctrl_c_t = now
        self._set_status("press Ctrl+C again to quit (or Ctrl+D)")


def run():
    # Auth is resolved in on_mount with interactive=False — use /login or /key modals.
    from .mouse_toggle import reset_mouse_fully

    app = JarvisTUI()
    try:
        app.run(mouse=False)
    except KeyboardInterrupt:
        pass
    finally:
        try:
            from ..repl.stream import cancel_current_stream
            cancel_current_stream()
        except Exception:
            pass
        reset_mouse_fully()
