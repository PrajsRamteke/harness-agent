"""Shared mutable state. Holds module-level globals referenced across the package.

Using `jarvis.state.<name> = ...` preserves original global-mutation semantics
without threading plumbing through every function.
"""
import json, os, time
from typing import Dict, List, Optional

from .constants import PIN_FILE, ALIAS_FILE, MODEL as _INITIAL_MODEL

# auth / client
provider: str = "anthropic"  # "anthropic" or "openrouter" — set by make_client()
auth_mode: str = "api_key"   # "api_key" or "oauth" — Anthropic-only; unused for openrouter
client = None                # Anthropic client, set by make_client()

# model
MODEL: str = _INITIAL_MODEL

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

# user context
pinned_context: str = PIN_FILE.read_text() if PIN_FILE.exists() else ""
aliases: Dict[str, str] = (
    json.loads(ALIAS_FILE.read_text()) if ALIAS_FILE.exists() else {}
)

# persistent session
current_session_id: Optional[int] = None
