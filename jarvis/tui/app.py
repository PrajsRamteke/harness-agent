"""Textual TUI app for Jarvis.

Layout
------
    ┌──────────────────────────────────────────────┬───────────────┐
    │ transcript (bottom-anchored, one widget per  │ sidebar       │
    │ message / tool call — see transcript.py)     │ (wide screens,│
    │                                              │  ⌃B toggles)  │
    ├──────────────────────────────────────────────┴───────────────┤
    │ queued prompts (FIFO, only while busy)                        │
    │ ask-user choices (LLM multiple choice)                        │
    │ ✻ Activity…  (12s · ↓ 1.2k tokens · esc to interrupt)         │
    │ /command · @file completion popup                             │
    │ ┃ ❯ composer                                                  │
    │   ● agent · model · provider            tokens · ⎇ branch · ? │
    └───────────────────────────────────────────────────────────────┘
"""
from __future__ import annotations

import os
import threading
import time

from textual import work
from textual import events
from textual.actions import SkipAction
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.screen import ModalScreen
from textual.widgets import OptionList, Static

from rich.markup import escape as _rich_escape
from rich.text import Text

from .console_shim import TUIConsole
from ..repl.tool_output_backfill import backfill_tool_output_history, inspector_has_entries
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
    _is_pet_card_command,
    _is_pet_badges_command,
)


# ─── agent / footer helpers ──────────────────────────────────────────────


def _active_agent_record() -> dict | None:
    rec = state.active_agent
    if rec is None and state.active_agent_name:
        rec = state.resolve_active_agent()
    return rec


def _agent_badge_markup() -> str:
    """Compact badge for the active agent — used in the footer."""
    rec = _active_agent_record()
    if not rec:
        return f"[{ui.FG_MUTE}]jarvis[/]"
    icon = (rec.get("icon") or "").strip()
    color = (rec.get("color") or "").strip() or ui.ACCENT
    label = f"{icon} {rec['name']}".strip() if icon else rec["name"]
    if rec.get("scope") == "global":
        return f"[bold {color}]{_rich_escape(label)}[/] [{ui.FG_DIM}](g)[/]"
    return f"[bold {color}]{_rich_escape(label)}[/]"


def _agent_color() -> str:
    rec = _active_agent_record()
    if rec:
        return (rec.get("color") or "").strip() or ui.ACCENT
    return ui.ACCENT


def _pin_status_markup() -> str:
    """Compact pinned-context indicator."""
    from ..storage import pin as pin_store

    text = pin_store.pin_text()
    if not text:
        return ""
    if not pin_store.is_enabled():
        return f"[{ui.WARN}]pin paused[/]"
    return f"[{ui.FG_DIM}]pinned[/]"


def _fmt_tokens(n: int) -> str:
    if n >= 1_000_000:
        return f"{n / 1_000_000:.1f}M"
    if n >= 1000:
        return f"{n / 1000:.1f}k"
    return str(n)


def _is_upgrade_command(text: str) -> bool:
    head = (text or "").strip().split(maxsplit=1)[0].lower() if (text or "").strip() else ""
    return head == "/upgrade"


def _mouse_preference() -> bool:
    """Mouse support (wheel scroll, click, select-to-copy). On by default;
    ``HARNESS_MOUSE=0`` or settings ``ui.mouse: false`` restores native
    terminal selection."""
    env = os.environ.get("HARNESS_MOUSE")
    if env is not None:
        return env.strip().lower() not in ("0", "false", "no", "off")
    try:
        from ..storage.settings import get_settings

        val = get_settings().get("ui.mouse")
        if val is not None:
            return bool(val)
    except Exception:
        pass
    return True


# ─── multi-line prompt (Enter submits, Ctrl+J / Alt+Enter for newline) ───
# PromptArea lives in prompt_area.py; re-exported so callers/tests that import
# it from jarvis.tui.app keep working.
from .prompt_area import PromptArea  # noqa: F401, E402
from .terminal_keys import install as _install_terminal_keys  # noqa: E402
from .console_swap import _swap_console_everywhere  # noqa: F401, E402
from .mixins.web_remote import WebRemoteMixin  # noqa: E402
from .mixins.activity import ActivityLine, ActivityMixin  # noqa: E402
from .mixins.file_ref import FileRefPickerMixin  # noqa: E402
from .mixins.pet import PetMixin  # noqa: E402
from .mixins.prompt_nav import PromptNavMixin  # noqa: E402
from .mixins.loop import LoopMixin  # noqa: E402
from .pet_widget import PetBubble, PetBuddy  # noqa: E402
from .prompt_history import PromptHistory  # noqa: E402
from .sidebar import Sidebar  # noqa: E402
from .sticky_prompt import StickyPrompt  # noqa: E402
from .footer import FooterBar  # noqa: E402
from .transcript import (  # noqa: E402
    AssistantBlock,
    ThinkingBlock,
    ToolBlock,
    Transcript,
    TurnFooter,
    UserBlock,
    WelcomeBlock,
)

_SIDEBAR_MIN_WIDTH = 150
_PLACEHOLDER = "Ask anything…   / commands · @ files · ! shell · ⇧↵ newline"
_BUSY_PLACEHOLDER = "Type a follow-up — it's queued for when Jarvis finishes · esc interrupts"
# Shown in place of the default placeholder every few turns.
_TIPS = (
    "Tip: ⇧⇥ toggles plan mode — research first, approve changes after",
    "Tip: select text with the mouse to copy it",
    "Tip: ↑ brings back your previous prompts",
    "Tip: click a tool row to see its output",
    "Tip: ⌃B toggles the session sidebar",
    "Tip: drop an image or PDF into the prompt to attach it",
    "Tip: /model switches models · /theme changes colors",
    "Tip: !git status runs a shell command without the model",
)


# ─── App ─────────────────────────────────────────────────────────────────


class JarvisTUI(WebRemoteMixin, ActivityMixin, PetMixin, PromptNavMixin, LoopMixin,
                FileRefPickerMixin, App):
    ENABLE_COMMAND_PALETTE = False
    CSS = ui.GLOBAL_CSS

    BINDINGS = [
        Binding("ctrl+d", "quit", "Quit", show=False),
        Binding("ctrl+c", "cancel_or_quit", "Cancel/Quit", show=False),
        Binding("ctrl+t", "toggle_internal", "Trace", show=False),
        Binding("ctrl+f", "open_tools_inspector", "Tools", show=False),
        Binding("ctrl+p", "open_palette", "Commands", show=False),
        Binding("ctrl+b", "toggle_sidebar", "Sidebar", show=False),
        Binding("f1", "show_shortcuts", "Help", show=False),
        Binding("f2", "toggle_internal", "Trace", show=False),
        Binding("f3", "open_tools_inspector", "Tools", show=False),
        Binding("tab", "cycle_agent", "Agent", show=False, priority=True),
        Binding("shift+tab", "toggle_plan", "Plan", show=False, priority=True),
        Binding("escape", "escape_action", show=False),
        Binding("pageup", "scroll_transcript('pageup')", show=False, priority=True),
        Binding("pagedown", "scroll_transcript('pagedown')", show=False, priority=True),
        Binding("shift+up", "scroll_transcript('up')", show=False, priority=True),
        Binding("shift+down", "scroll_transcript('down')", show=False, priority=True),
        Binding("ctrl+home", "scroll_transcript('home')", show=False, priority=True),
        Binding("ctrl+end", "scroll_transcript('end')", show=False, priority=True),
        Binding("alt+up", "step_prompt(-1)", show=False, priority=True),
        Binding("alt+down", "step_prompt(1)", show=False, priority=True),
        Binding("ctrl+shift+u", "copy_web_url", "Copy URL", show=False),
        Binding("ctrl+y", "copy_last_reply", "Copy reply", show=False),
    ]

    def __init__(self):
        super().__init__()
        _install_terminal_keys()  # Shift+Enter via ESC+CR (VS Code / Cursor) → newline
        self._busy = False
        self._last_input_value = ""
        self._activity_timer = None
        self._activity_label = ""
        self._activity_t0 = 0.0
        self._turn_t0 = 0.0
        self._activity_spinner_i = 0
        self._frame = 0
        self._spinner_frames = ui.SPINNER_FRAMES
        self._status_msg = "ready"
        self._status_timer = None
        self._last_ctrl_c_t = 0.0
        self._key_debug = False
        self._web_bridge = None
        self._web_mux = None
        self._web_server = None
        self._web_urls: list[str] = []
        self._web_primary_url = ""
        # Git branch cache — refreshed every few seconds, not every repaint.
        self._git_branch: str | None = None
        self._git_branch_checked_at: float = 0.0
        self._git_branch_ttl: float = 5.0
        self._file_ref_mention: tuple[int, int, str] | None = None
        self._file_ref_mouse_on = False
        self._file_ref_last_query: str | None = None
        self._slash_last_query: str | None = None
        self._tokenizing_attachments = False
        self._tool_activity_lock = threading.Lock()
        self._ask_user = AskUserController(self)
        self._pet_init()
        self._loop_init()
        self._history = PromptHistory()
        self._turn_is_llm = False
        self._turn_cancelled = False
        self._turn_id = 0
        self._turn_threads: dict[int, int] = {}
        self._trace_shown: bool | None = None
        self._turns_done = 0
        self._last_esc_t = 0.0
        self._app_focused = True
        self._sidebar_pref: bool | None = None  # None = auto by width
        self._mouse_enabled = _mouse_preference()
        from .mouse_toggle import set_app_mouse

        set_app_mouse(self._mouse_enabled)
        # Every palette as a Textual theme: built-in widgets + $jv-* variables.
        for name in ui.PALETTES:
            self.register_theme(ui.textual_theme(name))
        self.theme = ui.textual_theme_name(ui.active_theme())

    # ─── ask-user (called from worker thread via console shim) ──────────
    def begin_ask_user_question(self, questions, on_done) -> None:
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

    def _refresh_git_branch(self, *, sync: bool = False) -> None:
        """Refresh the cached branch (TTL). Runs ``git`` off the UI thread
        unless ``sync`` — a subprocess per repaint would stutter scrolling."""
        now = time.monotonic()
        if (now - self._git_branch_checked_at) < self._git_branch_ttl:
            return
        self._git_branch_checked_at = now

        def _probe() -> None:
            try:
                from ..repl.banners import _current_git_branch
                import pathlib as _pl
                self._git_branch = _current_git_branch(_pl.Path.cwd())
            except Exception:
                self._git_branch = None

        if sync:
            _probe()
        else:
            threading.Thread(target=_probe, name="git-branch", daemon=True).start()

    async def _on_key(self, event):  # type: ignore[override]
        key = getattr(event, "key", "")
        if self._ask_user.active and self._ask_user.handle_key(key):
            event.stop()
            event.prevent_default()
            return
        if self._key_debug:
            self._key_debug = False
            try:
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
        yield PetBubble(id="pet_bubble")
        with Horizontal(id="main"):
            with Vertical(id="body"):
                yield Transcript(id="transcript")
                yield StickyPrompt(id="sticky_prompt")
                yield from self._compose_dock()
            yield Sidebar(id="sidebar", classes="hidden")
        yield WebRemoteBar(id="webar")

    def _compose_dock(self) -> ComposeResult:
        with Vertical(id="dock"):
            yield Static("", id="queuebar", markup=True, shrink=False, classes="hidden")
            yield Static("", id="askbar", markup=True, shrink=False, classes="hidden")
            yield ActivityLine(id="activity", classes="-idle")
            with Vertical(id="popup", classes="hidden"):
                yield Static("", id="popup_hint")
                yield OptionList(id="popup_list")
            with Horizontal(id="composer"):
                yield Static(ui.ARROW, id="prompt_prefix", markup=False)
                yield PromptArea(
                    id="prompt",
                    highlight_cursor_line=False,
                    placeholder=_PLACEHOLDER,
                    soft_wrap=True,
                )
                yield PetBuddy(id="pet")
            with Horizontal(id="footer"):
                yield FooterBar(id="footer_left", classes="-left")
                yield FooterBar(id="footer_right")

    # ─── lifecycle ───────────────────────────────────────────────────
    def on_mount(self):
        from ..constants import VERSION
        self.title = f"Jarvis v{VERSION}"
        self.sub_title = "The better agent"

        transcript = self.query_one("#transcript", Transcript)
        transcript.set_class(not state.show_internal, "-trace-off")
        self._trace_shown = bool(state.show_internal)
        status = self.query_one("#activity", ActivityLine)

        # Swap console BEFORE importing repl/* so their module-local
        # `console` names are rebound to the TUI console.
        tui_console = TUIConsole(self, transcript, status)
        _swap_console_everywhere(tui_console)
        self._tui_console = tui_console

        if state.web_enabled:
            self._start_web_remote(tui_console)

        from ..auth.client import make_client
        from ..storage.sessions import db_init, db_create_session

        if state.client is None:
            state.client = make_client(interactive=False)
        db_init()
        state.current_session_id = db_create_session(state.MODEL)

        from ..project_context import detect_project_context
        detect_project_context()

        # Auto-activate coding agent when inside a coding project
        # and the user hasn't explicitly set an agent yet.
        from ..storage.agents import auto_activate_coding_agent
        auto_activate_coding_agent()

        self._render_footer()
        self._set_status("ready")
        self._sync_activity_phase("")
        self._sync_agent_color()
        self._apply_sidebar_visibility()
        self._pet_apply_visibility()
        self._load_sticky_pref()
        from ..tools import background as _bg

        _bg.add_finish_hook(self._on_bg_job_finished)

        self.query_one("#prompt", PromptArea).focus()
        self.call_after_refresh(self._render_welcome_intro)
        self.set_interval(2.0, self._slow_refresh)

        if state.startup_prompt:
            self.set_timer(0.05, self._submit_startup_prompt)

    def on_unmount(self) -> None:
        from ..tools import background as _bg

        _bg.remove_finish_hook(self._on_bg_job_finished)

    def _on_bg_job_finished(self, job) -> None:
        """Watcher thread: a run_bg job exited — say so in the transcript."""
        if not self.is_running:
            return
        from ..tools.background import fmt_secs

        if job.killed:
            mark, color, what = "✕", ui.FG_DIM, "stopped"
        elif job.code == 0:
            mark, color, what = "✓", ui.OK, "finished"
        else:
            mark, color, what = "✗", ui.ERR, f"failed · exit {job.code}"
        cmd = " ".join(job.cmd.split())
        cmd = cmd if len(cmd) <= 70 else cmd[:69] + "…"
        try:
            self._tui_console.print(
                f"[{color}]{mark}[/] [{ui.FG_MUTE}]background job #{job.id} {what}[/] "
                f"[{ui.FG_DIM}]· {fmt_secs(job.elapsed)} · {_rich_escape(cmd)}[/]"
            )
        except Exception:
            pass

    def on_resize(self, event: events.Resize) -> None:
        self._apply_sidebar_visibility()
        self._pet_apply_visibility()
        self._pet_hide_bubble()
        self.call_after_refresh(self._sync_sticky_prompt)

    def _sync_trace(self) -> None:
        """Follow trace changes made outside ⌃T (/verbose, web settings)."""
        if bool(state.show_internal) != getattr(self, "_trace_shown", None):
            self._rebuild_transcript()

    def _slow_refresh(self) -> None:
        """Footer + sidebar refresh (tokens, branch, MCP health)."""
        self._sync_trace()
        self._render_footer()
        self._pet_slow_tick()
        self._load_sticky_pref()
        self._sync_sticky_prompt()
        try:
            sb = self.query_one("#sidebar", Sidebar)
            if not sb.has_class("hidden"):
                sb.refresh_body()
        except Exception:
            pass

    # ─── welcome ─────────────────────────────────────────────────────
    def _welcome_info(self) -> dict:
        import pathlib
        from ..constants import VERSION
        from ..constants.providers import PROVIDER_LABELS

        cwd = pathlib.Path.cwd()
        try:
            cwd_s = "~/" + str(cwd.relative_to(pathlib.Path.home()))
        except ValueError:
            cwd_s = str(cwd)
        self._git_branch_checked_at = 0.0
        self._refresh_git_branch(sync=True)
        ctx: list[str] = []
        if state.project_context_file:
            ctx.append(str(state.project_context_file))
        try:
            from ..storage import skills as _skills

            n = _skills.skill_count()
            if n:
                ctx.append(f"{n} skill{'s' if n != 1 else ''}")
        except Exception:
            pass
        try:
            from ..mcp.config import get_config as _get_mcp_config

            n = len(_get_mcp_config().list_servers())
            if n:
                ctx.append(f"{n} MCP server{'s' if n != 1 else ''}")
        except Exception:
            pass
        try:
            from ..storage import pin as pin_store

            if pin_store.pin_text():
                ctx.append("pinned context" if pin_store.is_enabled() else "pin paused")
        except Exception:
            pass
        warning = ""
        if state.client is None:
            warning = (
                f"[{ui.WARN}]Not signed in[/] [{ui.FG_MUTE}]— [bold]/login[/] for OAuth or "
                f"[bold]/key[/] for API keys[/]"
            )
        return {
            "version": VERSION,
            "model": state.MODEL,
            "provider": PROVIDER_LABELS.get(state.provider, state.provider or ""),
            "cwd": cwd_s,
            "branch": self._git_branch,
            "context": ctx,
            "warning": warning,
        }

    def _mount_welcome(self) -> None:
        # Always called on an empty (or just-cleared) transcript.
        welcome = WelcomeBlock(self._welcome_info())
        self.query_one("#transcript", Transcript).mount(welcome)
        self.call_after_refresh(welcome.start_shine)
        if state.update_result:
            info = state.update_result
            count = info.get("count", 0)
            noun = "commit" if count == 1 else "commits"
            lines = [f"[{ui.OK}]✓ updated — {count} new {noun} pulled[/]"]
            for commit in (info.get("commits") or [])[:5]:
                lines.append(f"[{ui.FG_DIM}]  · {_rich_escape(commit)}[/]")
            self._tui_console.print("\n".join(lines))
        if self._web_primary_url:
            esc = _rich_escape(self._web_primary_url)
            self._tui_console.print(
                f"[{ui.FG_DIM}]🌐 remote[/]  [link={esc}]{esc}[/link]  "
                f"[{ui.FG_DIM}]· scan QR top-right · ⌃⇧U copy[/]"
            )

    def _render_welcome_intro(self) -> None:
        """Welcome block, then start background workers (so their output
        always lands below the welcome)."""
        self._mount_welcome()
        self._finish_welcome_intro()

    def _finish_welcome_intro(self) -> None:
        self._auto_connect_mcp_background()
        self._check_for_updates_background()
        self._warm_model_catalogs_background()
        self._pet_mount()

    # Kept for callers that refreshed the old context strip.
    def _write_context_strip(self, log=None) -> None:
        self._slow_refresh()

    # Web-remote behaviour (_start_web_remote, _handle_web_*, _sync_web_*, etc.)
    # lives in WebRemoteMixin (jarvis/tui/mixins/web_remote.py).

    @work(thread=True)
    def _check_for_updates_background(self) -> None:
        """Pull latest commits after the prompt is shown; re-exec when updated."""
        from ..updater import maybe_update_and_reexec

        maybe_update_and_reexec()

    @work(thread=True)
    def _warm_model_catalogs_background(self) -> None:
        """Pre-fetch the live free-model catalogs so /model opens instantly."""
        from ..constants.providers import (
            model_catalogs_are_fresh,
            refresh_model_catalogs,
        )

        try:
            if not model_catalogs_are_fresh():
                refresh_model_catalogs()
        except Exception:
            pass

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

    # ─── footer ──────────────────────────────────────────────────────
    def _footer_segments(self) -> tuple[list[tuple], list[tuple]]:
        """``(priority, markup, action)``; lower priority number = kept longer.

        Clickable: agent → agents, model → models, provider → setup hub,
        think → effort, tokens → sidebar, pin → pins, ``?`` → shortcuts.
        """
        from ..constants.providers import PROVIDER_LABELS

        left: list[tuple[int, str]] = [
            (0, _agent_badge_markup(), "open_agents"),
            (0, f"[{ui.FG}]{_rich_escape(state.MODEL.rsplit('/', 1)[-1])}[/]", "open_models"),
        ]
        prov = PROVIDER_LABELS.get(state.provider, state.provider or "")
        if prov:
            left.append((5, f"[{ui.FG_DIM}]{_rich_escape(prov)}[/]", "open_providers"))
        if state.plan_mode:
            left.append((1, f"[b {ui.WARN}]⏸ plan mode[/]", "toggle_plan"))
        if state.auto_approve:
            left.append((2, f"[{ui.WARN}]auto-approve[/]", None))

        right: list[tuple[int, str]] = []
        try:
            from .sidebar import context_window

            window = context_window(state.MODEL)
            used = int(state.total_in or 0) + int(state.total_out or 0)
            if window and used / window >= 0.7:
                pct = used / window * 100
                color = ui.ERR if pct >= 90 else ui.WARN
                right.append((1, f"[{color}]◔ context {pct:.0f}%[/]", "toggle_sidebar"))
        except Exception:
            pass
        if state.think_mode:
            right.append((4, f"[{ui.ACCENT_3}]think {state.think_effort}[/]", "open_think"))
        total = int(state.total_tokens or 0)
        if total:
            right.append((3, f"[{ui.FG_MUTE}]{_fmt_tokens(total)}[/] [{ui.FG_DIM}]tokens[/]", "toggle_sidebar"))
        try:
            from ..constants import PRICING

            price = PRICING.get(state.MODEL)
            if price and (price[0] or price[1]) and total:
                from ..repl.stats import estimated_cost

                cost = estimated_cost()
                if cost >= 0.01:
                    right.append((6, f"[{ui.FG_DIM}]${cost:.2f}[/]", None))
        except Exception:
            pass
        self._refresh_git_branch()
        if self._git_branch:
            right.append((7, f"[{ui.FG_DIM}]⎇ {_rich_escape(self._git_branch)}[/]", None))
        pin = _pin_status_markup()
        if pin:
            right.append((9, pin, "open_pins"))
        if state.show_internal:
            right.append((10, f"[{ui.FG_DIM}]trace[/]", "toggle_internal"))
        right.append((8, f"[{ui.FG_DIM}]? help[/]", "show_shortcuts"))
        return left, right

    @staticmethod
    def _cells(markup: str) -> int:
        try:
            return Text.from_markup(markup).cell_len
        except Exception:
            return len(markup)

    def _render_footer(self) -> None:
        try:
            footer = self.query_one("#footer")
            left_w = self.query_one("#footer_left", FooterBar)
            right_w = self.query_one("#footer_right", FooterBar)
        except Exception:
            return
        left, right = self._footer_segments()
        width = max(20, (footer.size.width or self.size.width or 100) - 4)

        def total(lft, rgt) -> int:
            lw = sum(self._cells(seg[1]) for seg in lft) + 3 * max(0, len(lft) - 1)
            rw = sum(self._cells(seg[1]) for seg in rgt) + 3 * max(0, len(rgt) - 1)
            return lw + rw + 2

        while total(left, right) > width:
            candidates = [(seg[0], "l", i) for i, seg in enumerate(left) if seg[0] > 0]
            candidates += [(seg[0], "r", i) for i, seg in enumerate(right)]
            if not candidates:
                break
            _p, side, idx = max(candidates)
            (left if side == "l" else right).pop(idx)
        left_w.set_segments([(m, a) for _p, m, a in left])
        right_w.set_segments([(m, a) for _p, m, a in right])

    # Footer click targets.
    def action_open_models(self) -> None:
        if not isinstance(self.screen, ModalScreen):
            self._open_model_picker()

    def action_open_agents(self) -> None:
        if not isinstance(self.screen, ModalScreen):
            self._open_agent_picker()

    def action_open_think(self) -> None:
        if not isinstance(self.screen, ModalScreen):
            self._open_think_picker()

    def action_open_providers(self) -> None:
        if not isinstance(self.screen, ModalScreen):
            self._open_provider_hub()

    def action_open_pins(self) -> None:
        if not isinstance(self.screen, ModalScreen):
            self._open_pin_modal()

    # Back-compat names used across the app / mixins / web remote.
    def _render_hintbar(self) -> None:
        self._render_footer()

    def _write_status_line(self, *, busy: bool) -> None:
        self._render_footer()
        self._refresh_activity_widgets()

    def _set_status(self, msg: str):
        self._status_msg = msg or ""
        if self._status_timer is not None:
            self._status_timer.stop()
            self._status_timer = None
        if self._status_msg and self._status_msg != "ready" and not self._busy:
            self._status_timer = self.set_timer(6.0, self._clear_status)
        self._render_footer()
        self._refresh_activity_widgets()
        self._sync_agent_color()

    def _clear_status(self) -> None:
        self._status_timer = None
        if not self._busy:
            self._status_msg = "ready"
            self._refresh_activity_widgets()

    def _sync_agent_color(self) -> None:
        """Composer bar follows the active agent's color (opencode)."""
        try:
            comp = self.query_one("#composer")
            color = _agent_color()
            comp.styles.border_left = ("outer", color)
            self.query_one("#prompt_prefix", Static).styles.color = color
        except Exception:
            pass

    def _set_placeholder(self, text: str) -> None:
        try:
            self.query_one("#prompt", PromptArea).placeholder = text
        except Exception:
            pass

    def on_app_focus(self, event: events.AppFocus) -> None:
        self._app_focused = True

    def on_app_blur(self, event: events.AppBlur) -> None:
        self._app_focused = False

    # ─── sidebar ─────────────────────────────────────────────────────
    def _apply_sidebar_visibility(self) -> None:
        try:
            sb = self.query_one("#sidebar", Sidebar)
        except Exception:
            return
        show = self._sidebar_pref
        if show is None:
            show = self.size.width >= _SIDEBAR_MIN_WIDTH
        sb.set_class(not show, "hidden")
        if show:
            sb.refresh_body()
        self._pet_apply_visibility()  # pen in the sidebar ↔ kitty in the input box

    def action_toggle_sidebar(self) -> None:
        try:
            sb = self.query_one("#sidebar", Sidebar)
        except Exception:
            return
        self._sidebar_pref = sb.has_class("hidden")
        self._apply_sidebar_visibility()

    # ─── prompt stash (FIFO queue above the composer) ────────────────
    @staticmethod
    def _stash_preview(msg, max_len: int = 56) -> str:
        text = msg[0] if isinstance(msg, tuple) else msg
        preview = (text or "").replace("\n", " ").strip()
        if len(preview) > max_len:
            preview = preview[: max_len - 1] + "…"
        return _rich_escape(preview)

    def _refresh_queue_bar(self) -> None:
        try:
            bar = self.query_one("#queuebar", Static)
        except Exception:
            return
        q = state.prompt_queue
        if not q:
            bar.add_class("hidden")
            bar.update("")
            self._sync_web_queue()
            return
        bar.remove_class("hidden")
        rows: list[str] = []
        for i, msg in enumerate(q[:5], 1):
            tag = "next" if i == 1 else f"#{i}"
            rows.append(
                f"[{ui.FG_DIM}]↳ {tag:<4}[/] [{ui.FG_MUTE}]{self._stash_preview(msg, 96)}[/]"
            )
        if len(q) > 5:
            rows.append(f"[{ui.FG_DIM}]  … +{len(q) - 5} more queued[/]")
        bar.update(Text.from_markup("\n".join(rows)))
        self._sync_web_queue()

    def _stash_prompt(self, text: str) -> None:
        """Queue a prompt while the agent is busy (FIFO; shown in #queuebar)."""
        from ..prompt_attachments import snapshot_registry

        state.prompt_queue.append((text, snapshot_registry()))
        self._refresh_queue_bar()

    # ─── attachments ─────────────────────────────────────────────────
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

    # ─── palette (⌃P) ────────────────────────────────────────────────
    def action_open_palette(self) -> None:
        if isinstance(self.screen, ModalScreen):
            return
        self._open_palette()

    def _open_palette(self):
        def after(cmd: str | None):
            inp = self.query_one("#prompt", PromptArea)
            if not cmd:
                inp.focus()
                return
            if cmd.endswith(" ") or cmd.strip() == "/multi":
                text = cmd if cmd.endswith(" ") else cmd.strip()
                inp.text = text
                inp.move_cursor((0, len(text)))
                self._last_input_value = text
                inp.focus()
                return
            self._route_command(cmd.strip())
            inp.focus()

        from .palette_modal import CommandPaletteScreen

        self.push_screen(CommandPaletteScreen(), after)

    def _route_command(self, text: str) -> bool:
        """Open the matching modal for a bare command; else dispatch it."""
        if self._open_modal_for(text):
            return True
        self._dispatch_palette_slash(text)
        return True

    def _open_modal_for(self, text: str) -> bool:
        """Modal-opening commands. Returns True when one was opened."""
        stripped = (text or "").strip()
        if not stripped.startswith("/"):
            return False
        if _is_session_picker_command(stripped):
            self._open_session_picker()
        elif _is_bare_model_command(stripped):
            self._route_modal_slash(stripped)  # bare → picker; with arg → switch
        elif _is_provider_hub_command(stripped):
            self._open_provider_hub()
        elif _is_think_picker_command(stripped):
            self._open_think_picker()
        elif _is_mcp_modal_command(stripped):
            self._open_mcp_modal()
        elif _is_agent_picker_command(stripped):
            self._open_agent_picker()
        elif _is_skill_picker_command(stripped):
            self._open_skill_browser()
        elif _is_command_manager_command(stripped):
            self._open_command_manager()
        elif _is_memory_modal_command(stripped):
            self._open_memory_modal()
        elif _is_pin_modal_command(stripped):
            self._open_pin_modal()
        elif _is_lesson_modal_command(stripped):
            self._open_lesson_modal()
        elif _is_settings_modal_command(stripped):
            self._open_settings_modal()
        elif _is_theme_modal_command(stripped):
            self._open_theme_modal()
        elif _is_local_command(stripped):
            rest = stripped[len("/local "):] if stripped.startswith("/local ") else ""
            self._open_local_cmd_modal(initial=rest)
        elif stripped == "/sidebar":
            self.action_toggle_sidebar()
        elif _is_pet_badges_command(stripped):
            self._open_pet_badges()
        elif _is_pet_card_command(stripped):
            self._open_pet_card()
        else:
            return False
        return True

    # ─── modals ──────────────────────────────────────────────────────
    def _open_model_picker(self):
        def after(option_id: str | None):
            if not option_id:
                return
            from ..constants.providers import parse_model_option_id
            source, model_id = parse_model_option_id(option_id)
            self._apply_model_selection_worker(model_id, source=source)
        from .model_modal import ModelPickerScreen

        self.push_screen(ModelPickerScreen(), after)

    def _open_think_picker(self):
        def after(effort: str | None):
            if not effort:
                return
            from ..commands.control import _handle_think
            _handle_think(effort)
            self._set_status("ready")
        from .think_modal import ThinkPickerScreen

        self.push_screen(ThinkPickerScreen(), after)

    def _open_provider_picker(self):
        def after(provider: str | None):
            if not provider:
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
    def _refresh_model_catalog_worker(self) -> None:
        """/model refresh — re-read the live free-model catalogs off-thread."""
        from ..commands.control import refresh_model_catalog

        try:
            refresh_model_catalog()
        except Exception as e:
            self.call_from_thread(
                lambda err=e: self._tui_console.print(f"[{ui.ERR}]catalog refresh failed: {err}[/]")
            )
        finally:
            self.call_from_thread(self._provider_action_done)

    @work(thread=True)
    def _apply_model_selection_worker(self, model_id: str, *, source: str = "") -> None:
        """Run model selection off the main thread (may prompt for API keys)."""
        from ..commands.control import _apply_model_selection, resolve_model_arg

        try:
            if not source:
                hit = resolve_model_arg(model_id)
                if hit:
                    model_id, source = hit
            _apply_model_selection(model_id, source=source)
        except RuntimeError as e:
            self.call_from_thread(
                lambda err=e: self._tui_console.print(f"[{ui.ERR}]model switch failed: {err}[/]")
            )
        finally:
            self.call_from_thread(self._provider_action_done)

    def _provider_action_done(self) -> None:
        self._set_status("ready")
        self._slow_refresh()

    def _open_mcp_modal(self):
        def after(_: object) -> None:
            from ..mcp.scope import invalidate_mcp_prompt_cache
            invalidate_mcp_prompt_cache()
            self._set_status("ready")
            self._slow_refresh()
        from .mcp_modal import MCPModalScreen

        self.push_screen(MCPModalScreen(), after)

    def _open_agent_picker(self):
        def after(result: object) -> None:
            if result is None:
                return
            if result == "off":
                state.set_active_agent(None)
            elif isinstance(result, dict):
                state.set_active_agent(result)
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
            self._set_status("ready")
            self._slow_refresh()
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

        app_path = ""
        try:
            app_path = inspect.getfile(self.__class__)
        except (TypeError, OSError):
            pass
        read_from = (app_path, f"{self.__class__.__name__}.CSS")
        # Only the app chrome: dialog chrome is TuiModalScreen's default CSS
        # ($jv-* variables). Re-adding it here as app CSS would outrank each
        # dialog's own sizing rules.
        self.stylesheet.add_source(
            _tui_theme.GLOBAL_CSS,
            read_from=read_from,
            is_default_css=False,
        )
        tname = _tui_theme.textual_theme_name(name)
        try:
            self.register_theme(_tui_theme.textual_theme(name))
        except Exception:
            pass
        if self.theme != tname:
            self.theme = tname  # re-resolves every $variable + reparses CSS
        else:
            self.stylesheet.reparse()
            self.stylesheet.update(self)
        self.refresh(layout=True)
        try:
            self.screen.refresh(layout=True)
        except Exception:
            pass
        self._sync_agent_color()
        self._rebuild_transcript(layout=False)

    def _open_theme_modal(self):
        def after(name: object) -> None:
            if name and isinstance(name, str):
                self._apply_theme_runtime(name, rebuild_transcript=True)
                self.notify(f"Theme: {name}", timeout=2.5)
            self._set_status("ready")
        from .theme_modal import ThemePickerScreen

        self.push_screen(ThemePickerScreen(), after)

    def _open_oauth_modal(self, *, title: str = "OAuth login") -> None:
        def after(result) -> None:
            if result is None:
                pass
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
            self._set_status("ready")

        from .oauth_connect_modal import OAuthConnectModalScreen

        self.push_screen(OAuthConnectModalScreen(title=title), after)

    def _open_login_modal(self):
        self._open_oauth_modal(title="Sign in")

    def _open_provider_hub(self) -> None:
        """Open the unified provider setup hub (dismisses "oauth" / "api" / None)."""
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
            base = cmd.split(None, 1)[0]
            has_args = len(cmd.split(None, 1)) > 1
            if has_args:
                inp.text = base + " "
                inp.move_cursor((0, len(inp.text)))
                self._last_input_value = inp.text
            else:
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
            self._begin_turn(new_inp)
            return
        self._set_status("ready")
        self._slow_refresh()  # also picks up /verbose

    # ─── route modal / non-thinking slash commands ───────────────────
    def _route_modal_slash(self, inp: str) -> None:
        """/model [arg|refresh], /session, /provider — modal or worker."""
        text = (inp or "").strip()
        head = text.split(maxsplit=1)[0]

        if head in ("/model", "/mode"):
            arg = text.split(maxsplit=1)[1] if " " in text else ""
            if arg.strip().lower() in ("refresh", "reload", "sync"):
                self._refresh_model_catalog_worker()
            elif arg:
                self._apply_model_selection_worker(arg)
            else:
                self._open_model_picker()
            return

        if head in ("/session", "/sessions"):
            self._open_session_picker()
            return

        if head == "/provider":
            self._open_provider_hub()
            return

        self._set_status("ready")

    def _handle_queued_command(self, cmd: str) -> None:
        """Queue-aware counterpart of the modal intercepts in on_prompt_area_submitted."""
        stripped = cmd.strip()
        if self._open_modal_for(stripped):
            return
        if stripped.split(maxsplit=1)[0] in ("/model", "/mode"):
            self._route_modal_slash(stripped)
            return
        self._begin_turn(stripped)

    # ─── blocks ──────────────────────────────────────────────────────
    @staticmethod
    def _user_panel(text: str) -> UserBlock:
        """The user's message block (kept under the old name for callers)."""
        return UserBlock(text)

    def _transcript(self) -> Transcript:
        return self.query_one("#transcript", Transcript)

    def action_escape_action(self):
        if self.file_ref_picker_active:
            self.close_file_ref_picker()
            return
        if self._busy:
            self._cancel_turn()
            return
        try:
            prompt = self.query_one("#prompt", PromptArea)
        except Exception:
            return
        if not prompt.text:
            return
        now = time.monotonic()
        if now - self._last_esc_t < 1.5:
            self._last_esc_t = 0.0
            prompt.clear()
            self._last_input_value = ""
            self._sync_composer_mode()
            self._set_status("ready")
        else:
            self._last_esc_t = now
            self._set_status("esc again to clear the prompt")

    def _cancel_turn(self) -> None:
        """Stop the running turn now; the worker unwinds in the background."""
        if not self._busy:
            return
        from ..repl.stream import cancel_current_stream

        # Mark the worker itself cancelled: the global flag is cleared when
        # the next turn starts, but this thread must keep stopping.
        state.cancel_thread(self._turn_threads.get(self._turn_id))
        cancel_current_stream()
        if hasattr(self._tui_console, "cancel_pending_prompts"):
            self._tui_console.cancel_pending_prompts()
        self._turn_cancelled = True
        self._tui_console.cancel_running_tools()
        # Finalize the partial reply now, on the UI thread, before any
        # queued turn can start streaming into a fresh block.
        self._tui_console.assistant_stream_abort()
        self._turn_done()

    # ─── input handling ──────────────────────────────────────────────
    def on_prompt_area_submitted(self, event: "PromptArea.Submitted") -> None:
        raw = event.value or ""
        text = raw.strip()
        inp = self.query_one("#prompt", PromptArea)
        inp.clear()
        self._last_input_value = ""
        self.close_file_ref_picker()
        self._sync_composer_mode()
        if not text:
            from ..prompt_attachments import reset_registry
            reset_registry()
            return

        from .paste_chips import expand_chips, reset as reset_chips

        display = text
        text = expand_chips(text)
        reset_chips()
        self._history.add(text)

        if self._open_modal_for(text):
            return

        stripped = text.strip()
        if stripped in ("/exit", "/quit"):
            self.exit()
            return

        if stripped == "/loop" or stripped.startswith("/loop "):
            self._loop_command(stripped[len("/loop"):])
            return

        if stripped == "/keytest":
            self._key_debug = True
            self._tui_console.print(
                f"[{ui.WARN}]⬟ keytest armed —[/] press any key to see what your "
                "terminal sent (one shot)."
            )
            return

        if self._busy:
            from ..prompt_attachments import reset_registry
            self._stash_prompt(text)
            reset_registry()
            return

        self._begin_turn(text, display=display)

    def _pop_queued_for_edit(self) -> bool:
        """Move the newest queued prompt back into the composer (↑ while busy)."""
        if not state.prompt_queue:
            return False
        item = state.prompt_queue.pop()
        text = item[0] if isinstance(item, tuple) else str(item)
        prompt = self.query_one("#prompt", PromptArea)
        self._popup_suppressed_for = text
        prompt.text = text
        lines = text.split("\n")
        prompt.move_cursor((len(lines) - 1, len(lines[-1])))
        self._refresh_queue_bar()
        self._set_status("editing a queued message — ↵ queues it again")
        return True

    def _begin_turn(self, inp: str, *, echo: bool = True, display: str | None = None,
                    badge: str = "") -> None:
        state.cancel_requested.clear()
        self._turn_cancelled = False
        self._turn_is_llm = False
        self._turn_id += 1
        self._reply_before_turn = state.last_assistant_text

        transcript = self._transcript()
        upgrading = _is_upgrade_command(inp)
        checking = False
        if upgrading:
            arg = inp.strip().split(maxsplit=1)[1].strip().lower() if " " in inp.strip() else ""
            checking = arg == "check"
            self._tui_console.start_command_progress(
                inp.strip(), phase="checking…" if checking else "upgrading…"
            )
        elif echo:
            transcript.add(UserBlock(display or inp, shell=inp.startswith("!"), flash=True,
                                     badge=badge))
        transcript.follow()
        if self._web_bridge is not None:
            self._web_bridge.emit(
                "message",
                {"role": "you", "text": inp, "title": "you"},
            )
        self._busy = True
        self._turn_t0 = time.monotonic()
        self._pet_turn_started()
        if upgrading:
            self._sync_activity_phase("Checking" if checking else "Upgrading")
            self._set_status("checking…" if checking else "upgrading…")
        elif inp.startswith("!"):
            self._sync_activity_phase("Running shell command")
            self._set_status("processing…")
        else:
            self._sync_activity_phase("Thinking")
            self._set_status("thinking…")
        self._start_activity_pulse()
        self._set_placeholder(_BUSY_PLACEHOLDER)
        self._sync_web_busy()
        self._run_turn(inp, self._turn_id)

    # ─── session picker ──────────────────────────────────────────────
    def _open_session_picker(self):
        def after(sid):
            if sid is None:
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

    def _replay_messages(self) -> None:
        """Rebuild the conversation as blocks (user / thinking / text / tools)."""
        blocks: list = []
        results: dict[str, tuple[str, bool]] = {}
        for msg in state.messages:
            content = msg.get("content")
            if msg.get("role") != "user" or isinstance(content, str):
                continue
            for block in content or []:
                data = self._block_dict(block)
                if data.get("type") != "tool_result":
                    continue
                body = data.get("content", "")
                if isinstance(body, list):
                    body = "\n".join(
                        item.get("text", "") for item in body if isinstance(item, dict)
                    )
                results[str(data.get("tool_use_id"))] = (str(body), bool(data.get("is_error")))

        for msg in state.messages:
            role = msg.get("role", "")
            content = msg.get("content", "")
            if role == "user":
                text = self._content_text(content).strip()
                if text:
                    blocks.append(UserBlock(text))
                continue
            if role != "assistant":
                continue
            if isinstance(content, str):
                if content.strip():
                    blocks.append(AssistantBlock(content.strip()))
                continue
            for block in content or []:
                data = self._block_dict(block)
                kind = data.get("type")
                if kind == "thinking":
                    body = (data.get("thinking") or "").strip()
                    if body:
                        blocks.append(ThinkingBlock(body))
                elif kind == "text":
                    body = (data.get("text") or "").strip()
                    if body:
                        blocks.append(AssistantBlock(body))
                elif kind == "tool_use":
                    blk = ToolBlock(str(data.get("id")), data.get("name", "tool"), data.get("input"))
                    out, is_err = results.get(str(data.get("id")), ("", False))
                    blk.finish(out, error=is_err or None)
                    blocks.append(blk)
        self._transcript().add_many(blocks)

    def _render_loaded_session(self) -> None:
        transcript = self._transcript()
        transcript.clear()
        self._tui_console.forget_tools()
        self._mount_welcome()
        self._tui_console.print(
            f"[{ui.OK}]▶[/] [{ui.FG_MUTE}]resumed session #{state.current_session_id} "
            f"({len(state.messages)} messages)[/]"
        )
        self._replay_messages()
        backfill_tool_output_history()
        transcript.follow()
        self._set_status("session loaded")
        self._slow_refresh()

    def _rebuild_transcript(self, *, layout: bool = True) -> None:
        """Repaint the conversation with the current theme / trace setting.

        Blocks render from their own data, so this is a cheap restyle —
        nothing is replayed and no output printed this session is lost.
        Theme changes keep line counts (``layout=False``: only visible
        blocks re-render); trace changes add/remove lines.
        """
        try:
            transcript = self._transcript()
        except Exception:
            return
        transcript.set_class(not state.show_internal, "-trace-off")
        self._trace_shown = bool(state.show_internal)
        transcript.restyle(layout=layout)
        for w in self.query(WelcomeBlock):
            w.refresh(layout=True)
        self._render_footer()
        self._refresh_activity_widgets()

    @work(thread=True, exclusive=True)
    def _run_turn(self, inp: str, turn_id: int | None = None) -> None:
        """Mirror of jarvis.main._send_and_loop, adapted for the TUI."""
        from ..commands.dispatch import handle_slash
        from ..repl.stream import call_claude_stream
        from ..repl.render import (
            render_assistant,
            classify_empty_turn,
            empty_turn_message,
        )
        from ..storage.sessions import db_append_message, db_set_title_if_empty

        me = threading.get_ident()
        if turn_id is not None:
            self._turn_threads[turn_id] = me

        def stale() -> bool:
            """A newer turn started (Esc → next prompt) — stop touching state."""
            return turn_id is not None and turn_id != self._turn_id

        # Stale stream state from a cancelled previous turn must never
        # survive into this one.
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
                    try:
                        out = run_bash(cmd)
                    finally:
                        state.auto_approve = prev
                    self._tui_console.print(self._shell_output_text(out))
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
                self._tui_console.print(
                    f"[{ui.FG_DIM}]▣ attached {len(attached)} file(s): {_rich_escape(names)}[/]"
                )
            inp = expanded

            expanded, dropped = expand_attachment_tokens(inp)
            if dropped:
                labels = ", ".join(dropped)
                self._tui_console.print(
                    f"[{ui.FG_DIM}]▣ dropped {len(dropped)} file(s): {_rich_escape(labels)}[/]"
                )
            inp = expanded
            reset_registry()

            if state.client is None:
                from ..auth.client import make_client
                state.client = make_client(interactive=False)
            if state.client is None:
                self._tui_console.print(
                    f"[{ui.WARN}]Not signed in[/] — run [bold]/login[/] "
                    "(OAuth) or [bold]/key[/] (API keys) first"
                )
                return

            self._turn_is_llm = True
            user_msg = {"role": "user", "content": inp}
            state.messages.append(user_msg)
            state.web_tool_used_this_turn = False
            if state.current_session_id:
                db_append_message(state.current_session_id, len(state.messages) - 1, user_msg)
                db_set_title_if_empty(state.current_session_id, inp)

            empty_retries = 0
            while True:
                if stale() or state.turn_cancelled():
                    raise KeyboardInterrupt()
                resp = call_claude_stream()
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
                if stale() or state.turn_cancelled():
                    raise KeyboardInterrupt()
                if state.current_session_id and state.messages and state.messages[-1] is not asst_msg:
                    db_append_message(state.current_session_id, len(state.messages) - 1, state.messages[-1])
        except KeyboardInterrupt:
            if not stale():
                self._turn_cancelled = True
            self._tui_console.assistant_stream_abort()
        except SystemExit:
            self._tui_console.assistant_stream_abort()
        except Exception as e:
            from ..console import HarnessAPIError
            self._tui_console.assistant_stream_abort()
            if not isinstance(e, HarnessAPIError) and not stale():
                self._tui_console.print(
                    f"[{ui.ERR}]✗ {_rich_escape(type(e).__name__)}: {_rich_escape(str(e))}[/]"
                )
        finally:
            self._tui_console.assistant_stream_abort()
            state.release_thread(me)
            if turn_id is not None and self._turn_threads.get(turn_id) == me:
                self._turn_threads.pop(turn_id, None)
            try:
                self.call_from_thread(self._turn_done, turn_id)
            except Exception:
                pass

    @staticmethod
    def _shell_output_text(out: str) -> Text:
        """``!cmd`` output: drop the ``$ cmd`` / ``exit=`` header, keep the body."""
        lines = (out or "").splitlines()
        code = 0
        if lines and lines[0].startswith("$ "):
            lines = lines[1:]
        if lines and lines[0].startswith("exit="):
            try:
                code = int(lines[0][5:].strip())
            except ValueError:
                code = 0
            lines = lines[1:]
        body = Text("\n".join(lines).rstrip() or "(no output)", style=ui.FG)
        if code:
            body.append(f"\nexit {code}", style=ui.ERR)
        return body

    def _turn_done(self, turn_id: int | None = None):
        # A cancelled turn is finished right away by Esc; its worker calls
        # back later — ignore that (it could otherwise end the NEXT turn).
        if turn_id is not None and turn_id != self._turn_id:
            return
        if not self._busy and not self._turn_t0:
            return
        state.cancel_requested.clear()
        finish_cmd = getattr(self._tui_console, "finish_command_progress", None)
        if callable(finish_cmd):
            finish_cmd()
        seconds = max(0.0, time.monotonic() - self._turn_t0) if self._turn_t0 else 0.0
        if self._turn_cancelled:
            self._tui_console.cancel_running_tools()
            if not self._turn_is_llm:  # LLM turns say it in their TurnFooter
                self._tui_console.print(f"[{ui.WARN}]⏹ interrupted[/] [{ui.FG_DIM}]· what should Jarvis do instead?[/]")
        if self._turn_is_llm:
            rec = _active_agent_record()
            agent = rec["name"] if rec else "jarvis"
            # Agent/model are in the status bar; repeat them only on a change.
            prev = getattr(self, "_last_footer_source", None)
            self._last_footer_source = (agent, state.MODEL)
            self._transcript().add(TurnFooter(
                agent=agent,
                color=_agent_color(),
                model=state.MODEL,
                seconds=seconds,
                interrupted=self._turn_cancelled,
                reply=(state.last_assistant_text or "")
                if state.last_assistant_text != getattr(self, "_reply_before_turn", None) else "",
                show_agent=prev is not None and prev[0] != agent,
                show_model=prev is not None and prev[1] != state.MODEL,
            ))
        if self._turn_is_llm:
            self._turns_done += 1
            if seconds >= 15 and not self._app_focused:
                self.bell()  # long turn finished while you were in another window
        self._pet_turn_finished(seconds, interrupted=self._turn_cancelled, llm=self._turn_is_llm)
        self._turn_is_llm = False
        self._busy = False
        self._turn_t0 = 0.0
        self._stop_activity_pulse()
        tip = _TIPS[(self._turns_done // 3) % len(_TIPS)] if self._turns_done and self._turns_done % 3 == 0 else None
        self._set_placeholder(tip or _PLACEHOLDER)
        self._sync_activity_phase("")
        self._sync_web_busy()
        self._tui_console.forget_tools()
        self._slow_refresh()
        self._loop_after_turn(cancelled=self._turn_cancelled)

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
            self._refresh_queue_bar()

            if next_prompt.startswith("/"):
                head = next_prompt.split(maxsplit=1)[0]
                if head in ("/model", "/mode", "/session", "/sessions",
                            "/settings", "/theme", "/agent", "/skills", "/memory",
                            "/lesson", "/mcp", "/think", "/local", "/pin", "/command",
                            "/commands", "/skill", "/sidebar"):
                    self._handle_queued_command(next_prompt)
                    return
                if _is_provider_hub_command(next_prompt):
                    self._open_provider_hub()
                    return

            self._begin_turn(next_prompt)
            return

        self._set_status("ready")
        try:
            self.query_one("#prompt", PromptArea).focus()
        except Exception:
            pass

    # ─── actions ─────────────────────────────────────────────────────
    def action_scroll_transcript(self, direction: str) -> None:
        if isinstance(self.screen, ModalScreen):
            raise SkipAction
        if self._ask_user.active and direction in ("up", "down"):
            if self._ask_user.handle_key(direction):
                return
        if self._try_file_ref_scroll(direction):
            return
        try:
            t = self._transcript()
        except Exception:
            return
        if direction == "pageup":
            t.scroll_page_up(animate=True, duration=0.18, easing="out_cubic")
        elif direction == "pagedown":
            t.scroll_page_down(animate=True, duration=0.18, easing="out_cubic")
        elif direction == "up":
            t.scroll_relative(y=-3, animate=False)
        elif direction == "down":
            t.scroll_relative(y=3, animate=False)
        elif direction == "home":
            t.scroll_home(animate=False)
        elif direction == "end":
            t.follow()

    def action_cycle_agent(self) -> None:
        if isinstance(self.screen, ModalScreen):
            raise SkipAction
        if self.file_ref_picker_active:
            self.try_accept_file_ref(run=False)
            return
        try:
            prompt = self.query_one("#prompt", PromptArea)
            if prompt.text.strip():
                return
        except Exception:
            pass
        from ..storage import agents as ag

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

    def action_toggle_plan(self) -> None:
        if isinstance(self.screen, ModalScreen):
            raise SkipAction
        from ..commands.dispatch import handle_slash

        handle_slash("/plan")
        self._set_status("ready")

    def action_toggle_internal(self):
        state.show_internal = not state.show_internal
        state.save_trace_config()
        self._rebuild_transcript()
        self.notify(
            "Trace on — thinking + tool output shown" if state.show_internal
            else "Trace off — compact tool rows only",
            timeout=2.5,
        )

    def action_show_shortcuts(self) -> None:
        from .shortcuts_modal import ShortcutsHelpScreen

        self.push_screen(ShortcutsHelpScreen())

    def action_open_tools_inspector(self) -> None:
        """^F — browse file reads and all tool output in one modal."""
        if not inspector_has_entries():
            self.notify("No tool output yet — run a tool, then ^F", timeout=2.5)
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

    def _copy_text(self, text: str) -> bool:
        try:
            self.copy_to_clipboard(text)  # OSC52 — works over SSH
        except Exception:
            pass
        return self._copy_to_system_clipboard(text)

    def on_text_selected(self, event: events.TextSelected) -> None:
        """Select-to-copy in the transcript (opencode-style)."""
        try:
            text = self.screen.get_selected_text()
        except Exception:
            text = None
        if not text or not text.strip():
            return
        self._copy_text(text)
        n = len(text)
        self.notify(f"Copied {n:,} char{'s' if n != 1 else ''}", timeout=1.8)

    def action_copy_last_reply(self) -> None:
        """⌃Y — copy the last assistant reply as clean text."""
        from ..utils.clipboard import normalize_copy_text

        text = normalize_copy_text(state.last_assistant_text or "")
        if not text:
            self._set_status("nothing to copy yet")
            return
        sys_ok = self._copy_text(text)
        n = len(text)
        if sys_ok:
            self._set_status(f"✓ copied last reply ({n} chars) — /copy code · /copy all")
        else:
            self._set_status(f"copied to terminal clipboard ({n} chars)")

    def action_cancel_or_quit(self):
        if self._busy:
            self._cancel_turn()
            return
        try:
            selected = self.screen.get_selected_text()
        except Exception:
            selected = None
        if selected:
            sys_ok = self._copy_text(selected)
            try:
                self.screen.clear_selection()
            except Exception:
                pass
            self._set_status("copied" if sys_ok else "copied (terminal)")
            return
        try:
            prompt = self.query_one("#prompt", PromptArea)
            if prompt.text:
                prompt.clear()
                self._last_input_value = ""
                self.close_file_ref_picker()
                self._sync_composer_mode()
                return
        except Exception:
            pass

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
        app.run(mouse=app._mouse_enabled)
    except KeyboardInterrupt:
        pass
    finally:
        try:
            from ..repl.stream import cancel_current_stream
            cancel_current_stream()
        except Exception:
            pass
        reset_mouse_fully()
