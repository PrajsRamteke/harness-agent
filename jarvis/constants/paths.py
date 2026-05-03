"""Filesystem paths used by the agent."""
import pathlib
import sys

CWD = pathlib.Path.cwd()
CONFIG_DIR = pathlib.Path.home() / ".config" / "harness-agent"


def set_cwd(path: str | pathlib.Path) -> pathlib.Path:
    """Update Jarvis' project root across already-imported modules."""
    new_cwd = pathlib.Path(path).expanduser().resolve()
    global CWD
    CWD = new_cwd

    for name, mod in list(sys.modules.items()):
        if not name.startswith("jarvis.") or mod is None:
            continue
        if hasattr(mod, "CWD"):
            try:
                setattr(mod, "CWD", new_cwd)
            except Exception:
                pass
    return new_cwd

SESSIONS_LIST_LIMIT = 50
SESSION_TITLE_MAX_LENGTH = 80
KEY_FILE = CONFIG_DIR / "key"
OPENROUTER_KEY_FILE = CONFIG_DIR / "openrouter_key"
OPENCODE_KEY_FILE = CONFIG_DIR / "opencode_key"
OAUTH_FILE = CONFIG_DIR / "oauth.json"
AUTH_MODE_FILE = CONFIG_DIR / "auth_mode"
PROVIDER_FILE = CONFIG_DIR / "provider"
HIST_FILE = CONFIG_DIR / "history.json"
NOTES_FILE = CONFIG_DIR / "notes.md"
PIN_FILE = CONFIG_DIR / "pinned.txt"
ALIAS_FILE = CONFIG_DIR / "aliases.json"
SESSIONS_DB = CONFIG_DIR / "sessions.db"
MEMORY_FILE = CONFIG_DIR / "memory.json"
SKILLS_FILE = CONFIG_DIR / "skills.json"
LAST_MODEL_FILE = CONFIG_DIR / "last_model.json"
LAST_THEME_FILE = CONFIG_DIR / "last_theme.json"
