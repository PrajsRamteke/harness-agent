"""Textual TUI app for Jarvis.

Layout
------
    ┌─ header (brand · model · agent · #session) ─────────────────────┐
    │                                                                 │
    │                       transcript (RichLog)                      │
    │                                                                 │
    ├──────────────── status strip (spinner · stats) ─────────────────┤
    │ ❯ composer                                                      │
    ├──────────────── hint bar (key cheatsheet) ──────────────────────┤
    └─────────────────────────────────────────────────────────────────┘
"""
from __future__ import annotations

import sys
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
from .think_modal import ThinkPickerScreen
from .mcp_modal import MCPModalScreen
from .agent_modal import AgentPickerScreen
from .skill_modal import SkillBrowserScreen
from .memory_modal import MemoryModalScreen
from .lesson_modal import LessonModalScreen
from .settings_modal import SettingsModalScreen
from .theme_modal import ThemePickerScreen
from .login_modal import LoginModalScreen
from .local_cmd_modal import LocalCmdModalScreen
from .provider_modal import ProviderPickerScreen
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


def _is_login_command(text: str) -> bool:
    s = (text or "").strip().lower()
    return s in ("/login", "/signin", "/sign-in")


def _is_local_command(text: str) -> bool:
    s = (text or "").strip().lower()
    return s == "/local" or s.startswith("/local ")


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
    """

    class Submitted(Message):
        def __init__(self, value: str) -> None:
            self.value = value
            super().__init__()

    async def _on_key(self, event):  # type: ignore[override]
        key = event.key
        if key == "escape":
            event.stop()
            event.prevent_default()
            try:
                self.app.action_escape_action()
            except Exception:
                pass
            return
        if key in ("shift+enter", "alt+enter", "ctrl+j", "ctrl+enter", "ctrl+n"):
            event.stop()
            event.prevent_default()
            self.insert("\n")
            return
        if key == "enter":
            event.stop()
            event.prevent_default()
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
        Binding("ctrl+t", "toggle_internal", "Logs", show=True),
        Binding("f2", "toggle_internal", "Internals", show=True),
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

    async def _on_key(self, event):  # type: ignore[override]
        if self._key_debug:
            self._key_debug = False
            try:
                key = getattr(event, "key", "?")
                name = getattr(event, "name", "?")
                char = getattr(event, "character", None)
                aliases = list(getattr(event, "key_aliases", []) or [])
                char_repr = repr(char) if char is not None else "<none>"
                self._tui_console.print(
                    f"[{ui.OK}]🔑 keytest:[/] [bold]{key}[/]  "
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
            yield Static("", id="statusbar", markup=True, shrink=True)
            with Horizontal(id="composer"):
                yield Static(ui.ARROW, id="prompt_prefix", markup=False)
                yield PromptArea(id="prompt")
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
            state.client = make_client()
        welcome_banner()
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

        if state.project_context_file:
            log.write(
                Panel(
                    Text.from_markup(
                        f"📄 [bold {ui.ACCENT}]{state.project_context_file}[/] "
                        f"detected — [{ui.FG_MUTE}]loaded on demand via read_file[/]"
                    ),
                    title="project context",
                    title_align="left",
                    border_style=ui.ACCENT,
                    padding=(0, 1),
                )
            )

        from ..storage import skills as _skills
        _sk_count = _skills.skill_count()
        if _sk_count > 0:
            log.write(
                Panel(
                    Text.from_markup(
                        f"🧠 [bold {ui.ACCENT_2}]{_sk_count} skill"
                        f"{'s' if _sk_count != 1 else ''}[/] available — "
                        f"[{ui.FG_MUTE}]auto-invoked from headers in system prompt[/]"
                    ),
                    title="skills",
                    title_align="left",
                    border_style=ui.ACCENT_2,
                    padding=(0, 1),
                )
            )

        self.query_one("#prompt", PromptArea).focus()

    # ─── hint bar ────────────────────────────────────────────────────
    def _render_hintbar(self) -> None:
        try:
            hints = [
                f"[{ui.ACCENT_3}]↵[/] send",
                f"[{ui.ACCENT_3}]⇧↵[/]/[{ui.ACCENT_3}]⌃J[/] newline",
                f"[{ui.ACCENT_3}]/[/] commands",
                f"[{ui.ACCENT_3}]⇥[/] agent",
                f"[{ui.ACCENT_3}]esc[/] cancel",
                f"[{ui.ACCENT_3}]^C[/] copy/cancel",
                f"[{ui.ACCENT_3}]^T[/] logs",
                f"[{ui.ACCENT_3}]^D[/] quit",
                f"[{ui.ACCENT_3}]F2[/] internals",
            ]
            line = f"  [{ui.SEP}]{ui.DOT}[/]  ".join(hints)
            self.query_one("#hintbar", Static).update(Text.from_markup(line))
        except Exception:
            pass

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
        segs.append(f"💬 [{ui.FG_MUTE}]{len(state.messages)}[/]")

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

        # ── prompt queue ──────────────────────────────────────────────
        if state.prompt_queue:
            segs.append(f"[{ui.WARN}]📋 {len(state.prompt_queue)}[/]")

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
            if _is_login_command(cmd):
                self._open_login_modal()
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
        def after(model_id: str | None):
            if not model_id:
                self._tui_console.print(f"[{ui.FG_DIM}]model picker cancelled[/]")
                return
            from ..commands.control import _apply_model_selection
            _apply_model_selection(model_id)
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

    def _open_theme_modal(self):
        def after(name: object) -> None:
            if name and isinstance(name, str):
                # Theme was selected — rebuild UI with new colors.
                from . import theme as _tui_theme
                from .modal_chrome import reload_chrome_css
                import inspect

                _tui_theme.set_theme(name)
                reload_chrome_css()

                # Rebuild the app's Textual stylesheet.
                app_path = ""
                try:
                    app_path = inspect.getfile(self.__class__)
                except (TypeError, OSError):
                    pass
                read_from = (app_path, f"{self.__class__.__name__}.CSS")
                self.stylesheet.add_source(
                    _tui_theme.GLOBAL_CSS, read_from=read_from, is_default_css=False
                )
                self.stylesheet.reparse()
                self.stylesheet.update(self)
                self.refresh()

                # Re-render the welcome banner & any existing messages with new
                # theme so the full display reflects the switch.
                self._rebuild_transcript()

                self._tui_console.print(
                    f"[{_tui_theme.OK}]✓ switched to [bold]{name}[/] theme[/]"
                )
            self._set_status("ready")
        self.push_screen(ThemePickerScreen(), after)

    def _open_login_modal(self):
        def after(ok: bool | None) -> None:
            if ok:
                self._tui_console.print(
                    f"[{ui.OK}]{ui.CHECK}[/] [bold]Signed in with Anthropic[/] — "
                    f"provider: Anthropic · auth: OAuth"
                )
            else:
                self._tui_console.print(f"[{ui.FG_DIM}]login cancelled[/]")
            self._set_status("ready")
        self.push_screen(LoginModalScreen(), after)

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
            state.prompt_queue.append(text)
            idx = len(state.prompt_queue)
            self._show_queue_panel(text, idx=idx)
            self._set_status(f"queued #{idx} ({len(state.prompt_queue)} waiting)")
            return

        if head in ("/login", "/logout", "/auth", "/key", "/model", "/session", "/sessions"):
            # Route these to their proper modal/command handler
            # rather than treating them as "thinking" interactions.
            self._route_modal_slash(text)
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
            self._open_login_modal()
            return

        if head in ("/key", "/auth", "/logout"):
            # These go through handle_slash which may prompt for input
            # via TUIConsole.input() — show a neutral status.
            log = self.query_one("#transcript", RichLog)
            log.write(self._user_panel(text))
            self._busy = True
            self._turn_t0 = time.monotonic()
            self._sync_activity_phase("Processing…")
            self._start_activity_pulse()
            self._set_status("processing…")
            self._run_turn(text)
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
            self._open_login_modal()
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
        if stripped in ("/theme",):
            self._open_theme_modal()
            return

        head = stripped.split(maxsplit=1)[0]
        if head in ("/key", "/auth", "/logout", "/local"):
            # Route through _run_turn but with "processing…" label
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

    # ─── panels (shared user / queue / dequeue renderers) ────────────
    @staticmethod
    def _user_panel(text: str) -> Panel:
        return Panel(
            Markdown(text),
            title="you",
            title_align="left",
            border_style=ui.OK,
            padding=(0, 1),
        )

    def _show_queue_panel(self, text: str, idx: int = 0) -> None:
        preview = text[:100].replace("\n", " ")
        if len(text) > 100:
            preview += "…"
        tag = f"#{idx}" if idx else ""
        log = self.query_one("#transcript", RichLog)
        log.write(
            Panel(
                Text.from_markup(f"[{ui.FG_MUTE}]{preview}[/]"),
                title=f"⏳ queued {tag}",
                title_align="left",
                border_style=ui.WARN,
                padding=(0, 1),
            )
        )

    def _show_dequeue_panel(self, text: str, remaining: int) -> None:
        log = self.query_one("#transcript", RichLog)
        log.write(
            Panel(
                Text.from_markup(f"[{ui.FG}]{text}[/]"),
                title=(
                    f"⏭ from queue ({remaining} remaining)"
                    if remaining
                    else "⏭ from queue"
                ),
                title_align="left",
                border_style=ui.ACCENT,
                padding=(0, 1),
            )
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
        if _is_login_command(text):
            self._open_login_modal()
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
                f"[{ui.WARN}]🔑 keytest armed —[/] press any key to see what your "
                "terminal sent (one shot)."
            )
            return

        if self._busy:
            state.prompt_queue.append(text)
            idx = len(state.prompt_queue)
            self._show_queue_panel(text, idx=idx)
            self._set_status(f"queued #{idx} ({len(state.prompt_queue)} waiting)")
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

    def _render_internal_blocks(self, content) -> None:
        if isinstance(content, str):
            return
        log = self.query_one("#transcript", RichLog)
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

        # Project-context panel (same rendering as on_mount).
        if state.project_context_file:
            log.write(
                Panel(
                    Text.from_markup(
                        f"📄 [bold {ui.ACCENT}]{state.project_context_file}[/] "
                        f"detected — [{ui.FG_MUTE}]loaded on demand via read_file[/]"
                    ),
                    title="project context",
                    title_align="left",
                    border_style=ui.ACCENT,
                    padding=(0, 1),
                )
            )

        # Skills panel (same rendering as on_mount).
        from ..storage import skills as _skills
        _sk_count = _skills.skill_count()
        if _sk_count > 0:
            log.write(
                Panel(
                    Text.from_markup(
                        f"🧠 [bold {ui.ACCENT_2}]{_sk_count} skill"
                        f"{'s' if _sk_count != 1 else ''}[/] available — "
                        f"[{ui.FG_MUTE}]auto-invoked from headers in system prompt[/]"
                    ),
                    title="skills",
                    title_align="left",
                    border_style=ui.ACCENT_2,
                    padding=(0, 1),
                )
            )

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
            self._tui_console.print(f"[{ui.ERR}]error: {type(e).__name__}: {e}[/]")
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
            self._show_dequeue_panel(next_prompt, remaining)

            # Route modal-opening commands to their proper handler
            # instead of sending through _begin_turn → _run_turn
            # which sets misleading "thinking…" status.
            if next_prompt.startswith("/"):
                head = next_prompt.split(maxsplit=1)[0]
                if head in ("/login", "/model", "/session", "/sessions",
                            "/logout", "/auth", "/key", "/settings",
                            "/theme", "/agent", "/skills", "/memory",
                            "/lesson", "/mcp", "/think", "/local"):
                    self._handle_queued_command(next_prompt)
                    return

            self._begin_turn(next_prompt)
            return

        self._set_status("ready")
        self.query_one("#prompt", PromptArea).focus()

    def action_cycle_agent(self) -> None:
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
        mode = "shown" if state.show_internal else "hidden"
        self._set_status(f"internals {mode}")
        if state.current_session_id and state.messages and not self._busy:
            self._render_loaded_session()
        else:
            try:
                self._tui_console.print(f"[{ui.FG_DIM}]internal tool trace {mode}[/]")
            except Exception:
                pass

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
    # Resolve authentication BEFORE Textual takes the terminal.
    from ..auth.client import make_client
    if state.client is None:
        state.client = make_client()

    from .mouse_toggle import reset_mouse_fully

    app = JarvisTUI()
    try:
        app.run(mouse=False)
    finally:
        reset_mouse_fully()
