"""Shared mutable state. Holds module-level globals referenced across the package.

Using `jarvis.state.<name> = ...` preserves original global-mutation semantics
without threading plumbing through every function.
"""
import json, os, threading, time
from typing import Dict, List, Optional

from .constants import (
    VERSION, PIN_FILE, ALIAS_FILE, MODEL as _INITIAL_MODEL,
    PROVIDER_ANTHROPIC, AUTH_API_KEY,
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
                                    # (Jarvis, Claude Code, OpenCode, Cursor). When False
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
global_agents: bool = False

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


_reload_saved_theme()
_reload_saved_skills()
_reload_saved_think()
_reload_saved_mcp()
_reload_saved_agent_name()
