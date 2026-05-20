"""Shared mutable state. Holds module-level globals referenced across the package.

Using `jarvis.state.<name> = ...` preserves original global-mutation semantics
without threading plumbing through every function.
"""
import json, os, threading, time
from collections import deque
from typing import Dict, List, Optional

from .constants import (
    VERSION, PIN_FILE, ALIAS_FILE, MODEL as _INITIAL_MODEL,
    PROVIDER_ANTHROPIC, AUTH_API_KEY,
    THINK_EFFORTS, DEFAULT_THINK_EFFORT,
    TOOL_UI_HISTORY_SIZE,
)

# auth / client
provider: str = PROVIDER_ANTHROPIC  # "anthropic", "openrouter", or "opencode" — set by make_client()
auth_mode: str = AUTH_API_KEY       # "api_key" or "oauth" — Anthropic-only; unused for openrouter/opencode
client = None                       # Anthropic client, set by make_client()
anthropic_model_ids: list[str] | None = None  # live ids after OAuth/API validation


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
total_tokens: int = 0

# Cancel processing flag — set when user presses Escape, checked at every
# checkpoint (stream start, tool execution, between turn iterations).
# Persists across all phases so Escape works even when no stream is active.
cancel_requested = threading.Event()

# prompt stash (FIFO) — prompts received while busy; released one-by-one when each turn finishes
prompt_queue: list[str] = []
startup_prompt: str = ""  # one-shot prompt from `jarvis "..."` CLI args
auto_approve: bool = False
session_start: float = time.time()
tool_calls_count: int = 0
last_assistant_text: str = ""
web_tool_used_this_turn: bool = False  # disables hallucination guard when True
last_clipboard_image_digest: str = ""

# Recent tool outputs for scrollable F3 viewer in the TUI.
tool_output_history: deque = deque(maxlen=TOOL_UI_HISTORY_SIZE)


def record_tool_output(name: str, args_preview: str, content: str) -> None:
    """Remember a tool result for the scrollable output viewer."""
    tool_output_history.append(
        {
            "name": name,
            "args": args_preview,
            "content": content,
            "ts": time.time(),
        }
    )

# live typing/streaming of assistant text (API deltas → UI). Disable with HARNESS_STREAM_REPLY=0.
stream_reply_live: bool = os.getenv("HARNESS_STREAM_REPLY", "1").strip().lower() not in (
    "0", "false", "no", "off",
)
# Set while the TUI live strip is active; cleared on commit/abort.
_assistant_stream_ui_active: bool = False

# project context file detection (AGENT.md / AGENTS.md / CLAUDE.md / JARVIS.md)
project_context_file: str = ""      # filename found, e.g. "AGENT.md"
project_context_path: str = ""      # full absolute path to the file
project_context_content: str = ""   # deprecated: project files are read on demand

# auto-update result (set by updater.py at startup when new commits are pulled)
update_result: dict | None = None

# skills — auto-invoked by the LLM. Modal exists only for browsing.
global_skills: bool = False         # if True, include skills from ~/.config/*/skills/

# MCP
global_mcp: bool = False            # if True, include MCP servers from global config files
                                    # (Jarvis, Claude Code, OpenCode, Cursor). When False,
                                    # only project .mcp.json is loaded.

# user context
pinned_context: str = PIN_FILE.read_text() if PIN_FILE.exists() else ""
aliases: Dict[str, str] = (
    json.loads(ALIAS_FILE.read_text()) if ALIAS_FILE.exists() else {}
)

# persistent session
current_session_id: Optional[int] = None

# ── active agent ───────────────────────────────────────────────────────────────
# Replaces the legacy mode system. An "agent" is a markdown file with YAML
# frontmatter discovered by ``jarvis.storage.agents``. When set, the agent's
# body is appended to the system prompt as an addon.
#
#   active_agent_name : the persisted name (or "" when none active).
#   active_agent      : the resolved record dict (or None until resolved).
#
# Persistence is by name only — the dict is re-resolved on startup by
# ``resolve_active_agent()`` to honor agent file edits without restart.
active_agent_name: str = ""
active_agent: Optional[dict] = None

# Whether to include agents from global directories (~/.harness/agents, etc.)
# Defaults to True so the bundled coding / reverse_eng / setup agents are
# visible on fresh install. Users can toggle with `/agent global off`.
global_agents: bool = True

# ── visual theme ────────────────────────────────────────────────────────────
# These are kept in sync with jarvis/tui/theme.py PALETTES.
THEMES = {
    "red": {
        "user_border": "#3fb950",
        "asst_border": "#f97583",
        "think_border": "#8b949e",
        "tool_border": "#d29922",
        "project_border": "#ff7b72",
    },
    "blue": {
        "user_border": "#3fb950",
        "asst_border": "#56d4dd",
        "think_border": "#8b949e",
        "tool_border": "#d29922",
        "project_border": "#58a6ff",
    },
    "purple": {
        "user_border": "#3fb950",
        "asst_border": "#bc8cff",
        "think_border": "#8b949e",
        "tool_border": "#d29922",
        "project_border": "#58a6ff",
    },
    "green": {
        "user_border": "#3fb950",
        "asst_border": "#56d4dd",
        "think_border": "#8b949e",
        "tool_border": "#d29922",
        "project_border": "#56d364",
    },
    "orange": {
        "user_border": "#3fb950",
        "asst_border": "#f0883e",
        "think_border": "#8b949e",
        "tool_border": "#d29922",
        "project_border": "#ffa657",
    },
    "yellow": {
        "user_border": "#3fb950",
        "asst_border": "#e3b341",
        "think_border": "#8b949e",
        "tool_border": "#d29922",
        "project_border": "#f0d272",
    },
    "rose": {
        "user_border": "#3fb950",
        "asst_border": "#f7527a",
        "think_border": "#8b949e",
        "tool_border": "#d29922",
        "project_border": "#ffb3c6",
    },
    "slate": {
        "user_border": "#3fb950",
        "asst_border": "#8b949e",
        "think_border": "#6b7684",
        "tool_border": "#d29922",
        "project_border": "#b1bac4",
    },
    "ocean": {
        "user_border": "#22c55e",
        "asst_border": "#60a5fa",
        "think_border": "#64748b",
        "tool_border": "#eab308",
        "project_border": "#3b82f6",
    },
    "cyberpunk": {
        "user_border": "#22d65e",
        "asst_border": "#d946ef",
        "think_border": "#7c6a9e",
        "tool_border": "#facc15",
        "project_border": "#22d3ee",
    },
    "monochrome": {
        "user_border": "#bbbbbb",
        "asst_border": "#e0e0e0",
        "think_border": "#606060",
        "tool_border": "#999999",
        "project_border": "#ffffff",
    },
    "forest": {
        "user_border": "#4caf50",
        "asst_border": "#5a8f4a",
        "think_border": "#5c6b50",
        "tool_border": "#cd9b1d",
        "project_border": "#7cb342",
    },
    "dracula": {
        "user_border": "#50fa7b",
        "asst_border": "#bd93f9",
        "think_border": "#6c6f85",
        "tool_border": "#f1fa8c",
        "project_border": "#ff79c6",
    },
    "sunset": {
        "user_border": "#56d364",
        "asst_border": "#e07a3a",
        "think_border": "#8a604a",
        "tool_border": "#e3b341",
        "project_border": "#f59e4c",
    },
    "dark": {
        "user_border": "#4ec9b0",
        "asst_border": "#569cd6",
        "think_border": "#606060",
        "tool_border": "#ce9178",
        "project_border": "#569cd6",
    },
}
theme: str = "red"
theme_colors: dict = THEMES["red"]


def _reload_saved_theme() -> None:
    """Restore theme from the unified settings file and sync the TUI theme module."""
    global theme, theme_colors
    try:
        from .storage.settings import get_settings
        t = get_settings().get("theme")
        if t in THEMES:
            theme = t
            theme_colors = dict(THEMES[t])
            # Sync the TUI theme module so Python constants update.
            try:
                from .tui.theme import set_theme as _set_tui_theme
                _set_tui_theme(t)
            except Exception:
                pass
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


# ── agent persistence ──────────────────────────────────────────────────────


def save_agent_config() -> None:
    """Persist ``agent.active`` (name) and ``agent.global`` to settings.json."""
    try:
        from .storage.settings import get_settings
        s = get_settings()
        s.set("agent.active", active_agent_name)
        s.set("agent.global", global_agents)
    except Exception:
        pass


def _reload_saved_agent_name() -> None:
    """Read agent.active and agent.global from settings.json (name only)."""
    global active_agent_name, global_agents
    try:
        from .storage.settings import get_settings
        s = get_settings()
        nm = s.get("agent.active")
        if isinstance(nm, str):
            active_agent_name = nm.strip()
        g = s.get("agent.global")
        if isinstance(g, bool):
            global_agents = g
    except Exception:
        pass


def resolve_active_agent() -> Optional[dict]:
    """Resolve ``active_agent_name`` to a record from ``storage.agents``.

    Idempotent — safe to call repeatedly. Returns the resolved record (also
    written to ``state.active_agent``) or None if the name is empty / cannot
    be resolved. Imported lazily to avoid a circular import at module load.
    """
    global active_agent
    if not active_agent_name:
        active_agent = None
        return None
    try:
        from .storage.agents import find_agent
        rec = find_agent(active_agent_name)
    except Exception:
        rec = None
    active_agent = rec
    return rec


def set_active_agent(record: Optional[dict]) -> None:
    """Set the live agent record (and its persisted name)."""
    global active_agent, active_agent_name
    active_agent = record
    active_agent_name = record["name"] if record else ""
    save_agent_config()


# ── think_mode persistence ──────────────────────────────────────────────────


def save_trace_config() -> None:
    """Persist trace.on (show_internal) to settings.json."""
    try:
        from .storage.settings import get_settings
        get_settings().set("trace.on", show_internal)
    except Exception:
        pass


def _reload_saved_trace() -> None:
    """Restore trace.on → show_internal from settings.json."""
    global show_internal
    try:
        from .storage.settings import get_settings
        v = get_settings().get("trace.on")
        if isinstance(v, bool):
            show_internal = v
    except Exception:
        pass


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
    prev_mcp = global_mcp
    _reload_saved_skills()
    _reload_saved_mcp()
    _reload_saved_think()
    _reload_saved_trace()
    _reload_saved_agent_name()
    try:
        from .storage.settings import get_settings
        m = get_settings().get("model")
        if isinstance(m, str) and m.strip():
            MODEL = m.strip()
    except Exception:
        pass
    # Resolve the agent name → record (safe to call here; storage import is
    # local inside resolve_active_agent).
    try:
        resolve_active_agent()
    except Exception:
        pass
    if global_mcp != prev_mcp:
        try:
            from .mcp.scope import apply_mcp_scope_change
            apply_mcp_scope_change(connect_all=global_mcp)
        except Exception:
            pass


_reload_saved_theme()
_reload_saved_skills()
_reload_saved_think()
_reload_saved_trace()
_reload_saved_mcp()
_reload_saved_agent_name()
