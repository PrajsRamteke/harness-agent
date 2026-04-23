"""Build the system prompt (with live datetime + pinned context + OAuth identity).

Cache: the expensive memory+skills+body string is rebuilt only when the
memory/skills content actually changes between turns. The date/time line is
always fresh but is cheap (~50 tokens) so we splice it in separately.
"""
from datetime import datetime
from typing import Union, List, Dict

from ..constants import SYSTEM, CLAUDE_CODE_IDENTITY
from ..storage.memory import as_prompt_block
from ..storage.skills import as_prompt_block as skills_prompt_block
from .. import state

# ── cache state ────────────────────────────────────────────────────────────────
_cached_body: str = ""
_cached_mem_key: str = ""
_cached_sk_key: str = ""
_cached_pinned: str = ""


def _build_static_body() -> str:
    """Everything except the date/time line. Cached between turns."""
    global _cached_body, _cached_mem_key, _cached_sk_key, _cached_pinned

    mem_block = as_prompt_block()
    sk_block = skills_prompt_block()
    pinned = state.pinned_context.strip()

    # Use content as cache key — only rebuild when something actually changed.
    if (mem_block == _cached_mem_key
            and sk_block == _cached_sk_key
            and pinned == _cached_pinned
            and _cached_body):
        return _cached_body

    body = SYSTEM
    if pinned:
        body += "\n\nPINNED CONTEXT (user-supplied, always remember):\n" + pinned
    if mem_block:
        body += "\n\n" + mem_block
    body += (
        "\n\nMEMORY TOOLS: You have memory_save / memory_list / memory_delete. "
        "When the user tells you something durable about themselves (name, role, "
        "preferences, recurring context), call memory_save proactively. Use "
        "memory_list when you need to recall. Do not save ephemeral task details."
    )
    if sk_block:
        body += "\n\n" + sk_block
    body += (
        "\n\nSKILL TOOLS (self-learning, separate from user memory): skill_search, "
        "skill_save, skill_list, skill_delete.\n"
        "- At the START of any non-trivial task, call skill_search with keywords "
        "from the user's request. If a past lesson applies, use it — this saves "
        "tokens and tool calls.\n"
        "- At the END of a task, if you discovered something non-obvious (a "
        "working command, a gotcha, a shortcut, a reusable pattern), call "
        "skill_save with a short task pattern + the actionable lesson + tags. "
        "Save generalizable know-how, NOT one-off specifics.\n"
        "- You are running inside your own source tree. If a task is genuinely "
        "repetitive across sessions and would be better as real code (a new tool, "
        "command, or helper) rather than a text lesson, you MAY propose edits to "
        "your own codebase via edit_file/write_file — but always show the diff, "
        "explain why, and get the user's OK before applying. Never break existing "
        "behavior; prefer additive changes."
    )

    _cached_body = body
    _cached_mem_key = mem_block
    _cached_sk_key = sk_block
    _cached_pinned = pinned
    return body


def build_system() -> Union[str, List[Dict]]:
    """System prompt + live date/time + any pinned user context.

    When authenticated via OAuth, Anthropic requires the FIRST system block to be
    exactly the Claude Code identity string — so we return a 2-block list in that
    case and keep our real instructions in the second block.
    """
    now = datetime.now()
    date_line = (
        f"\n\nCURRENT DATE & TIME: {now.strftime('%A, %B %d, %Y')} "
        f"at {now.strftime('%I:%M %p')} "
        f"(timezone: {datetime.now().astimezone().tzname()})\n"
        f"Never assume or guess the date — the above is the real current date injected at runtime."
    )

    body = _build_static_body() + date_line

    if state.auth_mode == "oauth":
        return [
            {"type": "text", "text": CLAUDE_CODE_IDENTITY},
            {"type": "text", "text": body},
        ]
    return body
