"""Shared mutable state. Holds module-level globals referenced across the package.

Using `jarvis.state.<name> = ...` preserves original global-mutation semantics
without threading plumbing through every function.
"""
import json, os, threading, time
from typing import Dict, List, Optional

from .constants import (
    VERSION, PIN_FILE, ALIAS_FILE, MODEL as _INITIAL_MODEL,
    PROVIDER_ANTHROPIC, AUTH_API_KEY, MODE_DEFAULT, MODE_CODING, MODE_REVERSE_ENG, MODE_SETUP,
    THINK_EFFORTS, DEFAULT_THINK_EFFORT,
)

# auth / client
provider: str = PROVIDER_ANTHROPIC  # "anthropic", "openrouter", or "opencode" — set by make_client()
auth_mode: str = AUTH_API_KEY       # "api_key" or "oauth" — Anthropic-only; unused for openrouter/opencode
client = None                       # Anthropic client, set by make_client()


def _compute_initial_model() -> str:
    """Env `CLAUDE_MODEL` wins; else settings.json model; else legacy file; else default."""
    if os.environ.get("CLAUDE_MODEL"):
        return _INITIAL_MODEL
    # Unified settings.json (also migrates legacy last_model.json on first read).
    try:
        from .storage.settings import get_settings
        m = get_settings().get("model")
        if isinstance(m, str) and m.strip():
            return m.strip()
    except Exception:
        pass
    return _INITIAL_MODEL


# model
MODEL: str = _compute_initial_model()

# conversation
backups: List[tuple] = []    # [(path, prev_content), ...] stack for /undo
messages: List[Dict] = []
think_mode: bool = True
think_effort: str = DEFAULT_THINK_EFFORT
show_internal: bool = True
total_in: int = 0
total_out: int = 0

# Cancel processing flag — set when user presses Escape, checked at every
# checkpoint (stream start, tool execution, between turn iterations).
# Persists across all phases so Escape works even when no stream is active.
cancel_requested = threading.Event()

# prompt queue — prompts received while busy are queued and auto-processed when the current turn finishes
prompt_queue: list[str] = []
auto_approve: bool = False
session_start: float = time.time()
tool_calls_count: int = 0
last_assistant_text: str = ""
web_tool_used_this_turn: bool = False  # disables hallucination guard when True
last_clipboard_image_digest: str = ""

# live typing/streaming of assistant text (API deltas → UI). Disable with HARNESS_STREAM_REPLY=0.
stream_reply_live: bool = os.getenv("HARNESS_STREAM_REPLY", "1").strip().lower() not in (
    "0", "false", "no", "off",
)
# Set while the TUI live strip is active; cleared on commit/abort.
_assistant_stream_ui_active: bool = False

# project context file detection (AGENT.md / CLAUDE.md / JARVIS.md)
project_context_file: str = ""      # filename found, e.g. "AGENT.md"
project_context_path: str = ""      # full absolute path to the file
project_context_content: str = ""   # deprecated: project files are read on demand

# auto-update result (set by updater.py at startup when new commits are pulled)
update_result: dict | None = None

# skills
global_skills: bool = False         # if True, include skills from ~/.config/*/skills/

# MCP
global_mcp: bool = False            # if True, include MCP servers from global config files
                                    # (Jarvis, Claude Code, OpenCode, Cursor). When False
                                    # only project .mcp.json is loaded.

# user context
pinned_context: str = PIN_FILE.read_text() if PIN_FILE.exists() else ""
aliases: Dict[str, str] = (
    json.loads(ALIAS_FILE.read_text()) if ALIAS_FILE.exists() else {}
)

# persistent session
current_session_id: Optional[int] = None

# ── active mode ────────────────────────────────────────────────────────────────
# "default"  → base system prompt only
# "coding"   → base system prompt + CODING_ADDON
# Future modes can be added here and handled in repl/system.py
active_mode: str = MODE_DEFAULT

# Human-readable labels + accent colours per mode (for statusbar / badges)
MODE_LABELS: dict = {
    MODE_DEFAULT:     ("DEFAULT", "#8b949e", "dim"),
    MODE_CODING:      ("⚡ CODING", "#3fb950", "bold"),
    MODE_REVERSE_ENG: ("🔐 REVERSE ENG", "#d29922", "bold"),
    MODE_SETUP:       ("🛠  SETUP", "#58a6ff", "bold"),
}

# ── visual theme ────────────────────────────────────────────────────────────
THEMES = {
    "red": {
        "user_border": "green",
        "asst_border": "magenta",
        "think_border": "dim",
        "tool_border": "yellow",
        "project_border": "cyan",
    },
    "purple": {
        "user_border": "#3fb950",
        "asst_border": "#bc8cff",
        "think_border": "#8b949e",
        "tool_border": "#d29922",
        "project_border": "#58a6ff",
    },
}
theme: str = "red"
theme_colors: dict = THEMES["red"]


def _reload_saved_theme() -> None:
    """Restore theme from the unified settings file (migrates legacy on first read)."""
    global theme, theme_colors
    try:
        from .storage.settings import get_settings
        t = get_settings().get("theme")
        if t in THEMES:
            theme = t
            theme_colors = dict(THEMES[t])
            return
    except Exception:
        pass
    theme = "red"
    theme_colors = dict(THEMES["red"])


# ── unified persistence — all writes go through settings.json ─────────────


def save_skills_config() -> None:
    """Persist ``skills.global`` to settings.json."""
    try:
        from .storage.settings import get_settings
        get_settings().set("skills.global", global_skills)
    except Exception:
        pass


def _reload_saved_skills() -> None:
    """Restore ``skills.global`` from settings.json."""
    global global_skills
    try:
        from .storage.settings import get_settings
        v = get_settings().get("skills.global")
        if isinstance(v, bool):
            global_skills = v
    except Exception:
        pass


def save_mcp_config() -> None:
    """Persist ``mcp.global`` to settings.json."""
    try:
        from .storage.settings import get_settings
        get_settings().set("mcp.global", global_mcp)
    except Exception:
        pass


def _reload_saved_mcp() -> None:
    """Restore ``mcp.global`` from settings.json."""
    global global_mcp
    try:
        from .storage.settings import get_settings
        v = get_settings().get("mcp.global")
        if isinstance(v, bool):
            global_mcp = v
    except Exception:
        pass


# ── think_mode persistence ──────────────────────────────────────────────────


def save_think_config() -> None:
    """Persist think.mode + think.effort to settings.json."""
    try:
        from .storage.settings import get_settings
        s = get_settings()
        s.set("think.mode", think_mode)
        if think_effort in THINK_EFFORTS:
            s.set("think.effort", think_effort)
    except Exception:
        pass


def _reload_saved_think() -> None:
    """Restore think.mode + think.effort from settings.json."""
    global think_mode, think_effort
    try:
        from .storage.settings import get_settings
        s = get_settings()
        mode = s.get("think.mode")
        if isinstance(mode, bool):
            think_mode = mode
        eff = s.get("think.effort")
        if eff in THINK_EFFORTS:
            think_effort = eff
        if think_mode and think_effort == "none":
            think_effort = DEFAULT_THINK_EFFORT
    except Exception:
        pass


def apply_settings_to_state() -> None:
    """Re-apply persisted settings onto the in-process state module.

    Call this after the user edits settings.json (or via ``/settings reload``)
    so the running session picks up the new values without a restart.
    """
    global MODEL
    _reload_saved_theme()
    _reload_saved_skills()
    _reload_saved_mcp()
    _reload_saved_think()
    try:
        from .storage.settings import get_settings
        m = get_settings().get("model")
        if isinstance(m, str) and m.strip():
            MODEL = m.strip()
    except Exception:
        pass


_reload_saved_theme()
_reload_saved_skills()
_reload_saved_think()
_reload_saved_mcp()
