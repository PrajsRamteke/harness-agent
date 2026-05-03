"""Shared mutable state. Holds module-level globals referenced across the package.

Using `jarvis.state.<name> = ...` preserves original global-mutation semantics
without threading plumbing through every function.
"""
import json, os, time
from typing import Dict, List, Optional

from .constants import (
    PIN_FILE, ALIAS_FILE, MODEL as _INITIAL_MODEL, LAST_MODEL_FILE, LAST_THEME_FILE,
    SKILLS_CONFIG_FILE,
    PROVIDER_ANTHROPIC, AUTH_API_KEY, MODE_DEFAULT, MODE_CODING,
)

# auth / client
provider: str = PROVIDER_ANTHROPIC  # "anthropic", "openrouter", or "opencode" — set by make_client()
auth_mode: str = AUTH_API_KEY       # "api_key" or "oauth" — Anthropic-only; unused for openrouter/opencode
client = None                       # Anthropic client, set by make_client()


def _compute_initial_model() -> str:
    """Env `CLAUDE_MODEL` wins; else last saved pick from a prior run; else default."""
    if os.environ.get("CLAUDE_MODEL"):
        return _INITIAL_MODEL
    if LAST_MODEL_FILE.exists():
        try:
            data = json.loads(LAST_MODEL_FILE.read_text())
            m = data.get("model")
            if isinstance(m, str) and m.strip():
                return m.strip()
        except (OSError, json.JSONDecodeError, TypeError):
            pass
    return _INITIAL_MODEL


# model
MODEL: str = _compute_initial_model()

# conversation
backups: List[tuple] = []    # [(path, prev_content), ...] stack for /undo
messages: List[Dict] = []
think_mode: bool = False
show_internal: bool = True
total_in: int = 0
total_out: int = 0
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
project_context_content: str = ""   # cached file content, re-read each turn

# skills
global_skills: bool = False         # if True, include skills from ~/.config/*/skills/

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
    MODE_DEFAULT: ("DEFAULT", "#8b949e", "dim"),
    MODE_CODING:  ("⚡ CODING", "#3fb950", "bold"),
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
    """Restore theme from last session, falling back to default."""
    global theme, theme_colors
    if LAST_THEME_FILE.exists():
        try:
            data = json.loads(LAST_THEME_FILE.read_text())
            t = data.get("theme", "")
            if t in THEMES:
                theme = t
                theme_colors = dict(THEMES[t])
                return
        except (OSError, json.JSONDecodeError, TypeError):
            pass
    theme = "red"
    theme_colors = dict(THEMES["red"])


def save_skills_config() -> None:
    """Persist global_skills toggle to disk."""
    try:
        SKILLS_CONFIG_FILE.parent.mkdir(parents=True, exist_ok=True)
        SKILLS_CONFIG_FILE.write_text(
            json.dumps({"global_skills": global_skills}, indent=2),
            encoding="utf-8",
        )
    except OSError:
        pass


def _reload_saved_skills() -> None:
    """Restore global_skills from last session."""
    global global_skills
    if SKILLS_CONFIG_FILE.exists():
        try:
            data = json.loads(SKILLS_CONFIG_FILE.read_text(encoding="utf-8"))
            if isinstance(data.get("global_skills"), bool):
                global_skills = data["global_skills"]
        except (OSError, json.JSONDecodeError, TypeError):
            pass


_reload_saved_theme()
_reload_saved_skills()
