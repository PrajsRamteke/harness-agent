"""Build the system prompt (with live datetime + pinned context + OAuth identity)."""
from datetime import datetime
from typing import Union, List, Dict

from ..constants import SYSTEM, CLAUDE_CODE_IDENTITY
from .. import state


def build_system() -> Union[str, List[Dict]]:
    """System prompt + live date/time + any pinned user context.

    When authenticated via OAuth, Anthropic requires the FIRST system block to be
    exactly the Claude Code identity string — so we return a 2-block list in that
    case and keep our real instructions in the second block.
    """
    now = datetime.now()
    date_line = (
        f"CURRENT DATE & TIME: {now.strftime('%A, %B %d, %Y')} "
        f"at {now.strftime('%I:%M %p')} "
        f"(timezone: {datetime.now().astimezone().tzname()})\n"
        f"Never assume or guess the date — the above is the real current date injected at runtime."
    )
    body = SYSTEM + "\n\n" + date_line
    if state.pinned_context.strip():
        body += "\n\nPINNED CONTEXT (user-supplied, always remember):\n" + state.pinned_context.strip()
    if state.auth_mode == "oauth":
        return [
            {"type": "text", "text": CLAUDE_CODE_IDENTITY},
            {"type": "text", "text": body},
        ]
    return body
