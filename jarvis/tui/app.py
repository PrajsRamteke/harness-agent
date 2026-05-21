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
from textual.message import Message
from textual.widgets import RichLog, Static, TextArea, OptionList
from textual.widgets.option_list import Option
from textual import work

from rich.markdown import Markdown
from rich.markup import escape as _rich_escape
from rich.panel import Panel
from rich.text import Text

from .console_shim import TUIConsole, _truncate_rich_log_lines, _replace_rich_log_block, _append_rich_log_block
from .session_modal import SessionPickerScreen, resume_session_into_state
from .palette_modal import CommandPaletteScreen
from .model_modal import ModelPickerScreen
from .think_modal import ThinkPickerScreen
from .mcp_modal import MCPModalScreen
from .agent_modal import AgentPickerScreen
from .skill_modal import SkillBrowserScreen
from .memory_modal import MemoryModalScreen
from .lesson_modal import LessonModalScreen
from .settings_modal import SettingsModalScreen
from .key_modal import KeyModalScreen
from .theme_modal import ThemePickerScreen
from .oauth_connect_modal import OAuthConnectModalScreen, OAuthConnectResult
from .local_cmd_modal import LocalCmdModalScreen
from .file_ref_picker import filter_project_files, file_ref_option_label
from .tools_inspector_modal import ToolsInspectorScreen
from .shortcuts_modal import ShortcutsHelpScreen
from ..repl.tool_output_backfill import backfill_tool_output_history, inspector_has_entries
from ..repl.tool_runs import (
    _display_for_tool,
    expand_dock_tool_use,
    is_dock_tool,
    list_runs,
    show_parallel_file_panel,
)
from .provider_modal import ProviderPickerScreen
from .ask_user import AskUserController, AskQuestion, normalize_questions
from .mouse_toggle import enable_mouse, disable_mouse
from ..prompt_refs import (
    active_file_ref_at_cursor,
    replace_file_ref_at_cursor,
)
from . import theme as ui
from .. import state


# ─── slash-command sniffers (modal-opening shortcuts) ────────────────────


def _is_bare_model_command(text: str) -> bool:
    s = (text or "").strip()
    if not s.startswith("/model"):
        return False
    parts = s.split(maxsplit=1)
    return len(parts) == 1


def _is_bare_provider_command(text: str) -> bool:
    s = (text or "").strip()
    if not s.startswith("/provider"):
        return False
    parts = s.split(maxsplit=1)
    return len(parts) == 1


def _is_session_picker_command(text: str) -> bool:
    s = (text or "").strip()
    return s in ("/session", "/sessions", "/session list", "/session ls")


def _is_think_picker_command(text: str) -> bool:
    s = (text or "").strip().lower()
    return s in ("/think", "/think mode", "/think modes", "/think select")


def _is_mcp_modal_command(text: str) -> bool:
    s = (text or "").strip().lower()
    return s in ("/mcp", "/mcps")


def _is_agent_picker_command(text: str) -> bool:
    s = (text or "").strip().lower()
    return s in ("/agent", "/agents")


def _is_skill_picker_command(text: str) -> bool:
    s = (text or "").strip().lower()
    return s in ("/skill", "/skills")


def _is_memory_modal_command(text: str) -> bool:
    s = (text or "").strip().lower()
    return s in ("/memory", "/memories")


def _is_lesson_modal_command(text: str) -> bool:
    s = (text or "").strip().lower()
    return s in ("/lesson", "/lessons")


def _is_settings_modal_command(text: str) -> bool:
    s = (text or "").strip().lower()
    return s in ("/settings", "/setting")


def _is_theme_modal_command(text: str) -> bool:
    s = (text or "").strip().lower()
    return s in ("/theme", "/themes")


def _is_oauth_modal_command(text: str) -> bool:
    s = (text or "").strip().lower()
    return s in ("/login", "/signin", "/sign-in", "/logout", "/auth")


def _oauth_modal_title(text: str) -> str:
    s = (text or "").strip().lower()
    if s == "/logout":
        return "Sign out"
    if s in ("/login", "/signin", "/sign-in"):
        return "Sign in"
    return "OAuth login"


def _is_local_command(text: str) -> bool:
    s = (text or "").strip().lower()
    return s == "/local" or s.startswith("/local ")


def _is_key_command(text: str) -> bool:
    s = (text or "").strip().lower()
    return s in ("/key", "/keys", "/key reset")


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


# ─── multi-line prompt (Enter submits, Ctrl+J / Alt+Enter for newline) ───


class PromptArea(TextArea):
    """Multi-line prompt input.

    - Enter submits.
    - Ctrl+J / Alt+Enter / Ctrl+Enter / Shift+Enter / Ctrl+N insert a newline.
    - Trailing backslash before Enter inserts a newline (bash-style).
    - Ctrl+D / Ctrl+C bubble up to the App.
    - @file mentions render with theme-colored background chips.
    """

    class Submitted(Message):
        def __init__(self, value: str) -> None:
            self.value = value
            super().__init__()

    def on_mount(self) -> None:
        from .prompt_highlight import PROMPT_THEME_NAME, build_prompt_text_area_theme

        self.register_theme(build_prompt_text_area_theme())
        self.theme = PROMPT_THEME_NAME
        self.language = None
        self.refresh_file_ref_highlights()

    def refresh_file_ref_highlights(self) -> None:
        from .prompt_highlight import build_file_ref_highlights

        try:
            row, col = self.cursor_location
        except Exception:
            row, col = 0, 0
        self._highlights.clear()
        for line_no, spans in build_file_ref_highlights(
            self.text or "",
            cursor_row=row,
            cursor_col=col,
        ).items():
            self._highlights[line_no] = spans
        self._line_cache.clear()
        self.refresh()

    def on_text_area_selection_changed(self, event: TextArea.SelectionChanged) -> None:
        if event.text_area is not self:
            return
        self.refresh_file_ref_highlights()

    async def _on_key(self, event):  # type: ignore[override]
        key = event.key
        if key == "escape":
            event.stop()
            event.prevent_default()
            try:
                if getattr(self.app, "file_ref_picker_active", False):
                    self.app.close_file_ref_picker()
                    return
                self.app.action_escape_action()
            except Exception:
                pass
            return
        if key in ("up", "down", "enter", "space", "escape"):
            try:
                if self.app._ask_user.handle_key(key):
                    event.stop()
                    event.prevent_default()
                    return
            except Exception:
                pass
        if key in ("up", "down", "tab"):
            try:
                if self.app.handle_prompt_key_for_file_ref(key):
                    event.stop()
                    event.prevent_default()
                    return
            except Exception:
                pass
        if key in ("shift+enter", "alt+enter", "ctrl+j", "ctrl+enter", "ctrl+n"):
            event.stop()
            event.prevent_default()
            self.insert("\n")
            return
        if key == "enter":
            event.stop()
            event.prevent_default()
            try:
                if self.app.try_accept_file_ref():
                    return
            except Exception:
                pass
            buf = self.text or ""
            n = 0
            for ch in reversed(buf):
                if ch == "\\":
                    n += 1
                else:
                    break
            if n % 2 == 1:
                self.text = buf[:-1] + "\n"
                try:
                    last_line = self.text.count("\n")
                    self.move_cursor((last_line, 0))
                except Exception:
                    pass
                return
            self.post_message(self.Submitted(buf))
            return
        if key == "ctrl+d":
            event.stop()
            event.prevent_default()
            self.app.exit()
            return
        if key == "ctrl+c":
            event.stop()
            event.prevent_default()
            try:
                self.app.action_cancel_or_quit()
            except Exception:
                self.app.exit()
            return
        if key == "ctrl+f":
            event.stop()
            event.prevent_default()
            try:
                self.app.action_open_tools_inspector()
            except Exception:
                pass
            return
        if key == "ctrl+t":
            event.stop()
            event.prevent_default()
            try:
                self.app.action_toggle_internal()
            except Exception:
                pass
            return
        if key in ("question_mark", "f1") and not (self.text or "").strip():
            event.stop()
            event.prevent_default()
            try:
                self.app.action_show_shortcuts()
            except Exception:
                pass
            return


def _swap_console_everywhere(tui_console):
    """Replace every `console` module attribute across loaded jarvis.* modules."""
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


# ─── App ─────────────────────────────────────────────────────────────────


class JarvisTUI(App):
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
        self._last_t_width = 0
        self._width_check_timer = None
        # Git branch cache — refreshed every few seconds, not every status tick.
        self._git_branch: str | None = None
        self._git_branch_checked_at: float = 0.0
        self._git_branch_ttl: float = 5.0
        self._file_ref_mention: tuple[int, int, str] | None = None
        self._file_ref_mouse_on = False
        self._file_ref_last_query: str | None = None
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

    def _prompt_has_focus(self) -> bool:
        try:
            return self.query_one("#prompt", PromptArea).has_focus
        except Exception:
            return False

    def _try_file_ref_scroll(self, direction: str) -> bool:
        """Route ↑/↓/page keys to the @file picker when it is open."""
        if not self.file_ref_picker_active or not self._prompt_has_focus():
            return False
        delta_map = {"up": -1, "down": 1, "pageup": -5, "pagedown": 5}
        delta = delta_map.get(direction)
        if delta is None:
            return False
        return self._navigate_file_ref_picker(delta)

    def _navigate_file_ref_picker(self, delta: int) -> bool:
        if not self.file_ref_picker_active:
            return False
        opts = self.query_one("#file_ref_picker", OptionList)
        if opts.option_count == 0:
            return True
        cur = opts.highlighted if opts.highlighted is not None else 0
        nxt = max(0, min(opts.option_count - 1, cur + delta))
        opts.highlighted = nxt
        try:
            opts.scroll_to_highlight()
        except Exception:
            pass
        opts.refresh()
        return True

    @property
    def file_ref_picker_active(self) -> bool:
        try:
            panel = self.query_one("#file_ref_panel", Vertical)
            return not panel.has_class("hidden")
        except Exception:
            return False

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

        from ..auth.client import make_client
        from ..storage.sessions import db_init, db_create_session
        from ..repl.banners import welcome_banner

        if state.client is None:
            state.client = make_client(interactive=False)
        welcome_banner()
        if state.client is None:
            tui_console.print(
                f"[{ui.WARN}]Not signed in[/] — use [cyan]/login[/] for OAuth "
                "(Anthropic or Codex) or [cyan]/key[/] for API keys"
            )
        db_init()
        state.current_session_id = db_create_session(state.MODEL)

        # Header / hint / status are all rendered through the new writer.
        self._render_hintbar()
        self._set_status("ready")
        self._sync_activity_phase("")

        from ..mcp.registry import auto_connect_servers
        auto_connect_servers(console_print=self._tui_console.print)

        # Poll for width changes (RichLog doesn't auto-reflow panels on resize)
        self._start_width_monitor()

        from ..project_context import detect_project_context
        detect_project_context()

        # Auto-activate coding agent when inside a coding project
        # and the user hasn't explicitly set an agent yet.
        from ..storage.agents import auto_activate_coding_agent
        auto_activate_coding_agent()

        self._write_context_strip(log)

        self.query_one("#prompt", PromptArea).focus()

        if state.startup_prompt:
            self.set_timer(0.05, self._submit_startup_prompt)

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
                f"[{ui.ACCENT_3}]↵[/] send",
                f"[{ui.ACCENT_3}]⇧↵[/]/[{ui.ACCENT_3}]⌃J[/] newline",
                f"[{ui.ACCENT_3}]/[/] commands",
                f"[{ui.ACCENT_3}]@[/] file · ↑↓ tab",
                f"[{ui.ACCENT_3}]⇥[/] agent",
                f"[{ui.ACCENT_3}]esc[/] cancel",
                f"[{ui.ACCENT_3}]^C[/] copy/cancel",
                f"[{ui.ACCENT_3}]^T[/] trace:{trace}",
                f"[{ui.ACCENT_3}]^F[/] tools",
                f"[{ui.ACCENT_3}]^D[/] quit",
                f"[{ui.ACCENT_3}]?[/] help",
            ]
            line = f"  [{ui.SEP}]{ui.DOT}[/]  ".join(hints)
            self.query_one("#hintbar", Static).update(Text.from_markup(line))
        except Exception:
            pass

    def _context_strip_markup(self) -> str | None:
        """One-line project context + skills/MCP summary for the welcome strip."""
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
        if not parts:
            return None
        sep = f" [{ui.SEP}]·[/] "
        return sep.join(parts)

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
            )
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
                self._last_t_width = current
                # During streaming skip the full rebuild — just clear cache
                if self._busy:
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
    _STATUS_SEP = f"  [{ui.SEP}]{ui.DOT}[/]  "

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
        segs.append(f"[{ui.ACCENT}]{state.MODEL}[/]")

        # ── agent ─────────────────────────────────────────────────────
        segs.append(_agent_badge_markup())

        # ── session ────────────────────────────────────────────────────
        if state.current_session_id is not None:
            segs.append(f"[{ui.FG_DIM}]#{state.current_session_id}[/]")

        # ── messages count ────────────────────────────────────────────
        segs.append(f"◈ [{ui.FG_MUTE}]{len(state.messages)}[/]")

        # ── tokens ────────────────────────────────────────────────────
        segs.append(
            f"⇅ [{ui.FG_MUTE}]{state.total_in}[/]/[{ui.FG_MUTE}]{state.total_out}[/]"
            f"=[{ui.FG_MUTE}]{state.total_tokens}[/]"
        )

        # ── think ─────────────────────────────────────────────────────
        if state.think_mode:
            segs.append(f"[{ui.OK}]think:{state.think_effort}[/]")
        else:
            segs.append(f"[{ui.FG_DIM}]think:off[/]")

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
            segs.append(f"[{ui.ACCENT_2}]⑂ ({self._git_branch})[/]")

        # ── internal trace ────────────────────────────────────────────
        if state.show_internal:
            segs.append(f"[{ui.FG_DIM}]trace:on[/]")

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
    def _stash_preview(text: str, max_len: int = 56) -> str:
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

    @staticmethod
    def _tool_run_glyph(status: str) -> str:
        return {
            "queued": "○",
            "running": "◐",
            "done": "✓",
            "error": "✗",
            "cancelled": "⊘",
        }.get(status, "·")

    def _format_tool_dock_row(self, run: dict) -> str:
        status = run.get("status") or "queued"
        glyph = self._tool_run_glyph(status)
        if status == "error":
            g_style = ui.ERR
        elif status == "done":
            g_style = ui.OK
        elif status == "running":
            g_style = ui.ACCENT
        else:
            g_style = ui.FG_DIM
        name = run.get("name") or "tool"
        label = (run.get("label") or "").strip() or "…"
        if "/" in label:
            base, _, parent = label.rpartition("/")
            path_bit = f"[{ui.FG}]{_rich_escape(base)}[/] [{ui.FG_DIM}]{_rich_escape(parent)}[/]"
        else:
            path_bit = f"[{ui.FG}]{_rich_escape(label)}[/]"
        tail = ""
        if status in ("done", "error") and run.get("chars"):
            tail = f" [{ui.FG_DIM}]{int(run['chars']):,} chars[/]"
        elif status == "running":
            elapsed = max(0.0, time.monotonic() - float(run.get("started") or 0))
            tail = f" [{ui.FG_DIM}]{elapsed:.1f}s[/]"
        return (
            f"  [{g_style}]{glyph}[/] [{ui.FG_MUTE}]{name:<14}[/] {path_bit}{tail}"
        )

    def reset_tool_activity_panel(self) -> None:
        """Next tool wave appends a fresh panel in the transcript."""
        self._tool_activity_anchor = None
        self._tool_activity_line_count = 0
        self._tool_activity_frozen = False

    def _parallel_panel_verb(self, runs: list[dict]) -> str:
        if any(r.get("name") in ("multi_edit", "edit_file", "write_file") for r in runs):
            return "editing"
        return "reading"

    def _build_tool_activity_panel(self, runs: list[dict]) -> Panel:
        n = len(runs)
        n_run = sum(1 for r in runs if r.get("status") == "running")
        n_q = sum(1 for r in runs if r.get("status") == "queued")
        verb = self._parallel_panel_verb(runs)
        title = f"⚡ {n} parallel file{'s' if n != 1 else ''}"
        if n_run or n_q:
            bits = []
            if n_run:
                bits.append(f"{n_run} {verb}")
            if n_q:
                bits.append(f"{n_q} queued")
            title += f" · {' · '.join(bits)}"
        body_lines = [self._format_tool_dock_row(r) for r in runs]
        body_lines.append(f"[{ui.FG_DIM}]^F inspect files[/]")
        return Panel(
            Text.from_markup("\n".join(body_lines)),
            title=title,
            title_align="left",
            border_style=ui.ACCENT,
            padding=(0, 1),
        )

    def _refresh_tool_dock(self) -> None:
        """Live-update a parallel-files panel inside the conversation transcript."""
        with self._tool_activity_lock:
            if self._tool_activity_frozen:
                return
            runs = list_runs()
            try:
                log = self.query_one("#transcript", RichLog)
            except Exception:
                return

            if not show_parallel_file_panel():
                if self._tool_activity_anchor is not None:
                    _truncate_rich_log_lines(log, self._tool_activity_anchor)
                    self._tool_activity_anchor = None
                    self._tool_activity_line_count = 0
                self._tool_activity_frozen = False
                return

            panel = self._build_tool_activity_panel(runs)
            anchor = self._tool_activity_anchor
            all_settled = all(
                r.get("status") in ("done", "error", "cancelled") for r in runs
            )

            if anchor is None:
                anchor = len(log.lines)
                self._tool_activity_anchor = anchor
                self._tool_activity_line_count = _append_rich_log_block(
                    log, panel, scroll_end=True
                )
            else:
                self._tool_activity_line_count = _replace_rich_log_block(
                    log, anchor, self._tool_activity_line_count, panel
                )
                log.scroll_end(animate=False)

            if all_settled:
                self._tool_activity_anchor = None
                self._tool_activity_line_count = 0
                self._tool_activity_frozen = True

    def _stash_prompt(self, text: str) -> None:
        """Queue a prompt while the agent is busy (FIFO; shown only in #queuebar)."""
        state.prompt_queue.append(text)
        self._refresh_queue_bar()
        if self._busy and self._activity_label:
            self._write_status_line(busy=True)

    # ─── activity / spinner ──────────────────────────────────────────
    def _sync_activity_phase(self, label: str) -> None:
        self._activity_label = (label or "").strip()
        self._activity_t0 = time.monotonic()
        self._activity_spinner_i = 0
        self._refresh_activity_widgets()

    def _tick_activity_spinner(self) -> None:
        if self._busy and self._activity_label:
            self._activity_spinner_i += 1
            self._refresh_activity_widgets()
        if (
            not self._tool_activity_frozen
            and show_parallel_file_panel()
            and any(r.get("status") == "running" for r in list_runs())
        ):
            self._refresh_tool_dock()

    def _refresh_activity_widgets(self) -> None:
        label = self._activity_label
        if not label or not self._busy:
            return
        self._write_status_line(busy=True)

    def _start_activity_pulse(self) -> None:
        self._stop_activity_pulse()
        self._activity_timer = self.set_interval(0.1, self._tick_activity_spinner)

    def _stop_activity_pulse(self) -> None:
        if self._activity_timer is not None:
            self._activity_timer.stop()
            self._activity_timer = None

    # ─── palette ─────────────────────────────────────────────────────
    def on_text_area_changed(self, event: TextArea.Changed) -> None:
        if event.text_area.id != "prompt":
            return
        val = event.text_area.text or ""
        prev = self._last_input_value
        self._last_input_value = val
        if val == "/" and prev == "":
            event.text_area.clear()
            self._last_input_value = ""
            self._open_palette()
            return
        self._sync_file_ref_picker()
        if isinstance(event.text_area, PromptArea):
            event.text_area.refresh_file_ref_highlights()

    def _prompt_cursor(self) -> tuple[int, int]:
        inp = self.query_one("#prompt", PromptArea)
        try:
            return inp.cursor_location
        except Exception:
            text = inp.text or ""
            lines = text.split("\n")
            return max(0, len(lines) - 1), len(lines[-1]) if lines else 0

    def _populate_file_ref_picker(self, query: str, *, keep_highlight_id: str | None = None) -> None:
        opts = self.query_one("#file_ref_picker", OptionList)
        status = self.query_one("#file_ref_hint", Static)
        paths = filter_project_files(query)
        opts.clear_options()
        if not paths:
            q = (query or "").strip()
            status.update(
                f"📎  no matches @{q or '…'} — root files/folders only until you type more"
                if not q
                else f"📎  no files match @{q} — keep typing in chat"
            )
            return
        q = (query or "").strip()
        scope = "project root" if not q else f"@{q}"
        status.update(
            f"📎  {scope} — {len(paths)} match(es) · type in chat · ↑↓ pick · tab/↵ insert · esc close"
        )
        for path in paths:
            opts.add_option(Option(file_ref_option_label(path), id=path))
        if not opts.option_count:
            return
        pick = 0
        if keep_highlight_id:
            for i in range(opts.option_count):
                opt = opts.get_option_at_index(i)
                if opt and opt.id == keep_highlight_id:
                    pick = i
                    break
        opts.highlighted = pick
        try:
            opts.scroll_to_highlight()
        except Exception:
            pass

    def _sync_file_ref_picker(self) -> None:
        inp = self.query_one("#prompt", PromptArea)
        text = inp.text or ""
        row, col = self._prompt_cursor()
        active = active_file_ref_at_cursor(text, row, col)
        panel = self.query_one("#file_ref_panel", Vertical)
        if not active:
            self.close_file_ref_picker()
            return
        self._file_ref_mention = active
        panel.remove_class("hidden")
        if not self._file_ref_mouse_on:
            enable_mouse()
            self._file_ref_mouse_on = True
        _row, _start, query = active
        opts = self.query_one("#file_ref_picker", OptionList)
        keep_id = None
        if opts.option_count and opts.highlighted is not None:
            try:
                opt = opts.get_option_at_index(opts.highlighted)
                if opt and opt.id:
                    keep_id = str(opt.id)
            except Exception:
                pass
        if self._file_ref_last_query != query:
            self._file_ref_last_query = query
            self._populate_file_ref_picker(query, keep_highlight_id=keep_id)
        elif opts.option_count == 0:
            self._populate_file_ref_picker(query)

    def close_file_ref_picker(self) -> None:
        try:
            panel = self.query_one("#file_ref_panel", Vertical)
            panel.add_class("hidden")
        except Exception:
            pass
        self._file_ref_mention = None
        self._file_ref_last_query = None
        if self._file_ref_mouse_on:
            disable_mouse()
            self._file_ref_mouse_on = False

    def _accept_file_ref(self, rel_path: str) -> None:
        inp = self.query_one("#prompt", PromptArea)
        text = inp.text or ""
        row, col = self._prompt_cursor()
        new_text, (new_row, new_col) = replace_file_ref_at_cursor(
            text, row, col, rel_path
        )
        inp.text = new_text
        try:
            inp.move_cursor((new_row, new_col))
        except Exception:
            pass
        self._last_input_value = new_text
        self.close_file_ref_picker()
        inp.refresh_file_ref_highlights()
        inp.focus()

    def try_accept_file_ref(self) -> bool:
        if not self.file_ref_picker_active:
            return False
        opts = self.query_one("#file_ref_picker", OptionList)
        if opts.option_count == 0 or opts.highlighted is None:
            return False
        opt = opts.get_option_at_index(opts.highlighted)
        if not opt or not opt.id:
            return False
        self._accept_file_ref(str(opt.id))
        return True

    def handle_prompt_key_for_file_ref(self, key: str) -> bool:
        if not self.file_ref_picker_active:
            return False
        if key == "up":
            return self._navigate_file_ref_picker(-1)
        if key == "down":
            return self._navigate_file_ref_picker(1)
        if key == "tab":
            return self.try_accept_file_ref()
        return False

    def on_text_area_selection_changed(self, event: TextArea.SelectionChanged) -> None:
        if event.text_area.id == "prompt":
            self._sync_file_ref_picker()

    def on_option_list_option_selected(self, event: OptionList.OptionSelected) -> None:
        if event.option_list.id != "file_ref_picker" or not event.option.id:
            return
        self._accept_file_ref(str(event.option.id))

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
            if _is_bare_provider_command(cmd):
                self._open_provider_picker()
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
            if _is_memory_modal_command(cmd):
                self._open_memory_modal()
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
            if _is_oauth_modal_command(cmd):
                self._open_oauth_modal(title=_oauth_modal_title(cmd))
                inp.focus()
                return
            if _is_key_command(cmd):
                self._open_key_modal()
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

        self.push_screen(CommandPaletteScreen(), after)

    def _open_model_picker(self):
        def after(option_id: str | None):
            if not option_id:
                self._tui_console.print(f"[{ui.FG_DIM}]model picker cancelled[/]")
                return
            from ..constants.providers import parse_model_option_id
            from ..commands.control import _apply_model_selection
            source, model_id = parse_model_option_id(option_id)
            _apply_model_selection(model_id, source=source)
            self._write_status_line(busy=False)
            self._set_status("ready")
        self.push_screen(ModelPickerScreen(), after)

    def _open_think_picker(self):
        def after(effort: str | None):
            if not effort:
                self._tui_console.print(f"[{ui.FG_DIM}]thinking picker cancelled[/]")
                return
            from ..commands.control import _handle_think
            _handle_think(effort)
            self._set_status("ready")
        self.push_screen(ThinkPickerScreen(), after)

    def _open_provider_picker(self):
        def after(provider: str | None):
            if not provider:
                self._tui_console.print(f"[{ui.FG_DIM}]provider picker cancelled[/]")
                return
            from ..commands.control import _handle_provider
            _handle_provider(provider)
            self._set_status("ready")
        self.push_screen(ProviderPickerScreen(), after)

    def _open_mcp_modal(self):
        def after(_: object) -> None:
            from ..mcp.scope import invalidate_mcp_prompt_cache
            invalidate_mcp_prompt_cache()
            self._set_status("ready")
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
        self.push_screen(AgentPickerScreen(), after)

    def _open_skill_browser(self):
        def after(_: object) -> None:
            self._set_status("ready")
        self.push_screen(SkillBrowserScreen(), after)

    def _open_memory_modal(self):
        def after(_: object) -> None:
            self._set_status("ready")
        self.push_screen(MemoryModalScreen(), after)

    def _open_lesson_modal(self):
        def after(_: object) -> None:
            self._set_status("ready")
        self.push_screen(LessonModalScreen(), after)

    def _open_settings_modal(self):
        def after(_: object) -> None:
            self._set_status("ready")
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
        self.push_screen(ThemePickerScreen(), after)

    def _open_oauth_modal(self, *, title: str = "OAuth login") -> None:
        def after(result: OAuthConnectResult | None) -> None:
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

        self.push_screen(OAuthConnectModalScreen(title=title), after)

    def _open_login_modal(self):
        self._open_oauth_modal(title="Sign in")

    def _open_key_modal(self) -> None:
        def after(_: object) -> None:
            self._set_status("ready")
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
            self._stash_prompt(text)
            return

        if head in ("/login", "/logout", "/auth", "/model", "/session", "/sessions"):
            # Route these to their proper modal/command handler
            # rather than treating them as "thinking" interactions.
            self._route_modal_slash(text)
            return

        if _is_key_command(text):
            self._open_key_modal()
            return

        result, should_send, new_inp = handle_slash(text)
        if result == "exit":
            self.exit()
            return
        if should_send and new_inp:
            log = self.query_one("#transcript", RichLog)
            log.write(self._user_panel(new_inp))
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

        if head == "/model":
            from ..commands.control import _apply_model_selection
            arg = text[len("/model "):] if " " in text else ""
            if arg:
                _apply_model_selection(arg)
                self._set_status("ready")
            else:
                self._open_model_picker()
            return

        if head == "/session":
            self._open_session_picker()
            return

        if head in ("/login",):
            self._open_oauth_modal(title="Sign in")
            return

        if head in ("/logout",):
            self._open_oauth_modal(title="Sign out")
            return

        if head in ("/auth",):
            self._open_oauth_modal(title="OAuth login")
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
        if stripped in ("/login", "/signin", "/sign-in"):
            self._open_oauth_modal(title="Sign in")
            return
        if stripped in ("/logout",):
            self._open_oauth_modal(title="Sign out")
            return
        if stripped in ("/auth",):
            self._open_oauth_modal(title="OAuth login")
            return
        if stripped in ("/session", "/sessions", "/session list", "/session ls"):
            self._open_session_picker()
            return
        if stripped in ("/model",):
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
        if stripped in ("/skills",):
            self._open_skill_browser()
            return
        if stripped in ("/memory",):
            self._open_memory_modal()
            return
        if stripped in ("/lesson",):
            self._open_lesson_modal()
            return
        if stripped in ("/settings", "/setting"):
            self._open_settings_modal()
            return
        if _is_key_command(stripped):
            self._open_key_modal()
            return
        if stripped in ("/theme",):
            self._open_theme_modal()
            return

        head = stripped.split(maxsplit=1)[0]
        if head in ("/local",):
            log = self.query_one("#transcript", RichLog)
            log.write(self._user_panel(stripped))
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
            return

        if _is_bare_model_command(text):
            self._open_model_picker()
            return
        if _is_bare_provider_command(text):
            self._open_provider_picker()
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
        if _is_memory_modal_command(text):
            self._open_memory_modal()
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
        if _is_oauth_modal_command(text):
            self._open_oauth_modal(title=_oauth_modal_title(text))
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

        if _is_key_command(stripped):
            self._open_key_modal()
            return

        if self._busy:
            self._stash_prompt(text)
            return

        self._begin_turn(text)

    def _begin_turn(self, inp: str) -> None:
        state.cancel_requested.clear()

        log = self.query_one("#transcript", RichLog)
        log.write(self._user_panel(inp))
        self._busy = True
        self._turn_t0 = time.monotonic()
        self._sync_activity_phase("Thinking…")
        self._start_activity_pulse()
        self._set_status("thinking…")
        self._run_turn(inp)

    # ─── session picker ──────────────────────────────────────────────
    def _open_session_picker(self):
        def after(sid):
            if sid is None:
                self._tui_console.print(f"[{ui.FG_DIM}]cancelled[/]")
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
            )
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
                        )
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
                    )
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
                    )
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
                    log.write(self._user_panel(text))
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
                        )
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
                    log.write(self._user_panel(text))
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
                        )
                    )

        # Refresh status bar, hint bar, and composer prefix with new colors.
        self._render_hintbar()
        self._set_status("ready")

    @work(thread=True, exclusive=True)
    def _run_turn(self, inp: str) -> None:
        """Mirror of jarvis.main._send_and_loop, adapted for the TUI."""
        from ..commands.dispatch import handle_slash
        from ..repl.stream import call_claude_stream
        from ..repl.render import render_assistant
        from ..storage.sessions import db_append_message, db_set_title_if_empty

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

            expanded, attached = expand_file_refs(inp)
            if attached:
                names = ", ".join(attached)
                self.call_from_thread(
                    lambda n=names: self._tui_console.print(
                        f"[{ui.FG_DIM}]▣ attached {len(attached)} file(s): {n}[/]"
                    )
                )
            inp = expanded

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

            while True:
                if state.cancel_requested.is_set():
                    raise KeyboardInterrupt()
                resp = call_claude_stream()
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
        except Exception as e:
            self._tui_console.assistant_stream_abort()
            self._tui_console.print(f"[{ui.ERR}]error: {_rich_escape(type(e).__name__)}: {_rich_escape(str(e))}[/]")
        finally:
            self._tui_console.assistant_stream_abort()
            self.call_from_thread(self._turn_done)

    def _turn_done(self):
        state.cancel_requested.clear()
        self._busy = False
        self._turn_t0 = 0.0
        self._stop_activity_pulse()
        self._sync_activity_phase("")

        if state.prompt_queue:
            next_prompt = state.prompt_queue.pop(0).strip()
            remaining = len(state.prompt_queue)
            self._refresh_queue_bar()

            # Route modal-opening commands to their proper handler
            # instead of sending through _begin_turn → _run_turn
            # which sets misleading "thinking…" status.
            if next_prompt.startswith("/"):
                head = next_prompt.split(maxsplit=1)[0]
                if head in ("/login", "/model", "/session", "/sessions",
                            "/logout", "/auth", "/settings",
                            "/theme", "/agent", "/skills", "/memory",
                            "/lesson", "/mcp", "/think", "/local"):
                    self._handle_queued_command(next_prompt)
                    return
                if _is_key_command(next_prompt):
                    self._open_key_modal()
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
        self.push_screen(ToolsInspectorScreen())

    def action_open_tool_output(self) -> None:
        self.action_open_tools_inspector()

    def action_open_file_activity(self) -> None:
        self.action_open_tools_inspector()

    def _copy_to_system_clipboard(self, text: str) -> bool:
        if not text:
            return False
        try:
            import pyperclip  # type: ignore
            pyperclip.copy(text)
            return True
        except Exception:
            pass

        import platform
        import shutil
        import subprocess

        sysname = platform.system()
        candidates: list[list[str]] = []
        if sysname == "Darwin":
            candidates.append(["pbcopy"])
        elif sysname == "Windows":
            candidates.append(["clip"])
        else:
            if shutil.which("wl-copy"):
                candidates.append(["wl-copy"])
            if shutil.which("xclip"):
                candidates.append(["xclip", "-selection", "clipboard"])
            if shutil.which("xsel"):
                candidates.append(["xsel", "--clipboard", "--input"])

        for cmd in candidates:
            try:
                subprocess.run(cmd, input=text.encode("utf-8"), check=True, timeout=2)
                return True
            except Exception:
                continue
        return False

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
            self._tui_console.print(f"[{ui.WARN}]⏹ cancelled by user[/]")
            self._turn_done()
            return

        now = time.monotonic()
        if now - self._last_ctrl_c_t < 2.0:
            self.exit()
            return
        self._last_ctrl_c_t = now
        self._set_status("press Ctrl+C again to quit (or Ctrl+D)")


def _escape(s: str) -> str:
    return s.replace("[", r"\[")


def run():
    # Auth is resolved in on_mount with interactive=False — use /login or /key modals.
    from .mouse_toggle import reset_mouse_fully

    app = JarvisTUI()
    try:
        app.run(mouse=False)
    finally:
        reset_mouse_fully()
