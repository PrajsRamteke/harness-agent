"""Build the system prompt (with live datetime + pinned context + OAuth identity)."""
from datetime import datetime
from typing import Union, List, Dict

from ..constants import SYSTEM, CLAUDE_CODE_IDENTITY
from ..storage.memory import as_prompt_block
from ..storage.skills import as_prompt_block as skills_prompt_block
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
    mem_block = as_prompt_block()
    if mem_block:
        body += "\n\n" + mem_block
    body += (
        "\n\nMEMORY TOOLS: You have memory_save / memory_list / memory_delete. "
        "When the user tells you something durable about themselves (name, role, "
        "preferences, recurring context), call memory_save proactively. Use "
        "memory_list when you need to recall. Do not save ephemeral task details."
    )
    sk_block = skills_prompt_block()
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
    if state.auth_mode == "oauth":
        return [
            {"type": "text", "text": CLAUDE_CODE_IDENTITY},
            {"type": "text", "text": body},
        ]
    return body
