"""Build the system prompt (with live datetime + pinned context + OAuth identity).

Cache: the expensive memory+skills+body string is rebuilt only when the
memory/skills content actually changes between turns. The date/time line is
always fresh but is cheap (~50 tokens) so we splice it in separately.

Coding addon: the CODING_ADDON block (~400 tokens) is appended only when the
latest user message looks like a coding request — saving tokens on every
non-coding turn (GUI tasks, questions, searches, etc.).
"""
from datetime import datetime
from typing import Union, List, Dict

from ..constants import SYSTEM, CODING_ADDON, CLAUDE_CODE_IDENTITY
from ..storage.memory import as_prompt_block
from ..storage.skills import as_prompt_block as skills_prompt_block
from .. import state

# ── cache state ────────────────────────────────────────────────────────────────
_cached_body: str = ""
_cached_mem_key: str = ""
_cached_sk_key: str = ""
_cached_pinned: str = ""

# Keywords that signal a coding request. Checked against the last user message.
_CODE_KEYWORDS = {
    # actions
    "fix", "bug", "debug", "refactor", "implement", "build", "write", "create",
    "add", "update", "edit", "change", "modify", "delete", "remove", "rename",
    "migrate", "integrate", "deploy", "install", "configure", "setup", "test",
    "lint", "compile", "run", "start", "optimize", "review", "explain",
    # code artifacts
    "code", "coding", "function", "class", "component", "hook", "module", "file", "script",
    "api", "endpoint", "route", "schema", "model", "types", "interface", "enum",
    "reducer", "saga", "selector", "action", "store", "state", "props", "param",
    "import", "export", "package", "dependency", "repo", "codebase", "pr",
    # languages / frameworks / tools
    "typescript", "javascript", "python", "react", "native", "node", "nestjs",
    "next", "redux", "saga", "navigation", "expo", "android", "ios", "git",
    "npm", "yarn", "bun", "webpack", "babel", "jest", "eslint", "prettier",
    # error patterns
    "error", "exception", "crash", "undefined", "null", "nan", "failed",
    "warning", "deprecat", "type error", "syntax", "import error", "issue"
}


def _is_coding_request(messages: list) -> bool:
    """Return True if the latest user message looks like a coding task.

    Checks the last user message text against _CODE_KEYWORDS.
    Falls back to False (no addon) on any parse error — safe default.
    """
    try:
        # Walk backwards to find the last user message
        for msg in reversed(messages):
            if msg.get("role") != "user":
                continue
            content = msg.get("content", "")
            # Content can be a plain string or a list of blocks
            if isinstance(content, str):
                text = content.lower()
            elif isinstance(content, list):
                text = " ".join(
                    b.get("text", "").lower()
                    for b in content
                    if isinstance(b, dict) and b.get("type") == "text"
                )
            else:
                return False
            # Check for any keyword hit
            for kw in _CODE_KEYWORDS:
                if kw in text:
                    return True
            return False  # found the last user msg but no keyword matched
    except Exception:
        pass
    return False


def _build_static_body() -> str:
    """Everything except the date/time line and coding addon. Cached between turns."""
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
        "\n\nMEMORY: memory_save/memory_list/memory_delete. "
        "Proactively save durable user facts (name, role, prefs). Don't save ephemeral task details."
    )
    if sk_block:
        body += "\n\n" + sk_block
    body += (
        "\n\nSKILLS: skill_search/skill_save/skill_list/skill_delete.\n"
        "- START of non-trivial task → skill_search for past lessons.\n"
        "- END of task → skill_save if you learned something non-obvious (pattern+lesson+tags).\n"
        "- Can propose edits to own codebase for repetitive tasks — show diff, get OK first."
    )

    _cached_body = body
    _cached_mem_key = mem_block
    _cached_sk_key = sk_block
    _cached_pinned = pinned
    return body


def build_system() -> Union[str, List[Dict]]:
    """System prompt + live date/time + optional coding addon + pinned user context.

    Coding addon (~400 tokens) is only appended when the latest user message
    is detected as a coding request — saves tokens on every non-coding turn.

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

    body = _build_static_body()

    # Append coding addon only for coding turns
    if _is_coding_request(state.messages):
        body += CODING_ADDON

    body += date_line

    if state.auth_mode == "oauth":
        return [
            {"type": "text", "text": CLAUDE_CODE_IDENTITY},
            {"type": "text", "text": body},
        ]
    return body
