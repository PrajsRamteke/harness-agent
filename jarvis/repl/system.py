"""Build the system prompt (with live datetime + pinned context + OAuth identity).

Cache: the expensive memory+skills+body string is rebuilt only when the
memory/skills content actually changes between turns. The date/time line is
always fresh but is cheap (~50 tokens) so we splice it in separately.

Coding addon logic:
  Explicit mode only — if state.active_mode == "coding", inject CODING_ADDON.
  Default mode always uses the base system prompt only.
"""
from datetime import datetime
from typing import Union, List, Dict

from ..constants import (
    CODING_ADDON, OAUTH_IDENTITY,
    AUTH_OAUTH, MODE_CODING,
)
from ..constants.system_prompt import build_base_system
from ..storage.memory import as_prompt_block
from ..storage.skills import as_prompt_block as skills_prompt_block
from .. import state

# ── cache state ────────────────────────────────────────────────────────────────
_cached_body: str = ""
_cached_mem_key: str = ""
_cached_sk_key: str = ""
_cached_pinned: str = ""
_cached_cwd_branch: str = ""


def _get_git_branch(cwd: str) -> str | None:
    """Quickly detect the current git branch. Returns None if not in a repo."""
    import subprocess
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--abbrev-ref", "HEAD"],
            cwd=cwd,
            capture_output=True,
            text=True,
            timeout=2,
        )
        if result.returncode == 0:
            branch = result.stdout.strip()
            return branch if branch else None
    except (subprocess.TimeoutExpired, FileNotFoundError, Exception):
        pass
    return None

def _keyword_detected(messages: list) -> bool:
    """Compatibility shim: default mode no longer auto-detects coding turns."""
    del messages
    return False


def _is_coding_request(messages: list) -> bool:
    """Return True when the coding addon should be active for this turn.

    Used by build_system() to decide prompt injection. The add-on is explicit:
    default mode never infers coding rules from user text.
    """
    del messages
    return state.active_mode == MODE_CODING


def _build_static_body() -> str:
    """Everything except the date/time line and coding addon. Cached between turns."""
    global _cached_body, _cached_mem_key, _cached_sk_key, _cached_pinned, _cached_cwd_branch

    mem_block = as_prompt_block()
    sk_block = skills_prompt_block()
    pinned = state.pinned_context.strip()
    from pathlib import Path
    cwd = str(Path.cwd())
    branch = _get_git_branch(cwd)
    cwd_branch_key = cwd + "|" + (branch or "")

    # Use content as cache key — only rebuild when something actually changed.
    if (mem_block == _cached_mem_key
            and sk_block == _cached_sk_key
            and pinned == _cached_pinned
            and cwd_branch_key == _cached_cwd_branch
            and _cached_body):
        return _cached_body

    body = build_base_system(Path(cwd), git_branch=branch)
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
    _cached_cwd_branch = cwd_branch_key
    return body


def build_system() -> Union[str, List[Dict]]:
    """System prompt + live date/time + optional coding addon + pinned user context.

    Coding addon (~400 tokens) is only appended in explicit coding mode.

    When authenticated via OAuth, Anthropic requires the FIRST system block to be
    exactly the OAuth identity string — so we return a 2-block list in that
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

    if state.auth_mode == AUTH_OAUTH:
        return [
            {"type": "text", "text": OAUTH_IDENTITY},
            {"type": "text", "text": body},
        ]
    return body
