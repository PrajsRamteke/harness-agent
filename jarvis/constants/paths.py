"""Filesystem paths used by the agent."""
import pathlib

CWD = pathlib.Path.cwd()
CONFIG_DIR = pathlib.Path.home() / ".config" / "claude-agent"
KEY_FILE = CONFIG_DIR / "key"
OAUTH_FILE = CONFIG_DIR / "oauth.json"
AUTH_MODE_FILE = CONFIG_DIR / "auth_mode"
HIST_FILE = CONFIG_DIR / "history.json"
NOTES_FILE = CONFIG_DIR / "notes.md"
PIN_FILE = CONFIG_DIR / "pinned.txt"
ALIAS_FILE = CONFIG_DIR / "aliases.json"
SESSIONS_DB = CONFIG_DIR / "sessions.db"
MEMORY_FILE = CONFIG_DIR / "memory.json"
SKILLS_FILE = CONFIG_DIR / "skills.json"
