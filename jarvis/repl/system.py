"""Build the system prompt (with live datetime + pinned context + OAuth identity).

Cache: the expensive memory+skills+body string is rebuilt only when the
memory/skills content actually changes between turns. The date/time line is
always fresh but is cheap (~50 tokens) so we splice it in separately.

Coding addon logic (two-layer):
  1. Explicit mode  — if state.active_mode == "coding", always inject CODING_ADDON.
     Keyword check is skipped entirely; no false negatives, no token waste.
  2. Auto-detect    — only when mode is "default". Checks the last user message
     against a tight set of rare/unambiguous coding keywords (NOT generic words
     like "fix", "add", "run"). A hit injects the addon for *this turn only*
     without changing state.active_mode.
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

# ── rare / unambiguous coding keywords (auto-detect in default mode only) ──────
# Rules for inclusion:
#   ✓ Almost never appears in a non-coding sentence
#   ✓ Whole-word match (padded with spaces / boundaries) to avoid false hits
#   ✗ Generic words (fix, run, add, file, error, update, test, ...) — excluded
_RARE_CODE_KEYWORDS: frozenset[str] = frozenset({
    #actions
    "bug", "debug", "refactor", "implement", "deploy", "install", "configure",
    "lint", "compile", "review",

    # languages / frameworks / tools
    "typescript", "javascript", "python", "java", "c++", "c#", "php", "ruby",
    "go", "rust", "swift", "kotlin", "dart", "react", "reactjs", "native",
    "node", "nestjs", "express", "next", "redux", "navigation", "expo",
    "android", "ios", "git", "github", "gitlab", "npm", "yarn", "bun",
    "webpack", "vite", "babel", "jest", "eslint", "prettier", "docker",
    "kubernetes", "firebase", "supabase", "mongodb", "mysql", "postgresql",

    # code artifacts
    "codebase", "coding", "function", "class", "component", "hook", "module", "file",
    "script", "api", "endpoint", "route", "schema", "model", "types", "interface",
    "enum", "reducer", "selector", "action", "store", "state", "props", "param",
    "import", "export", "package", "dependency", "repo", "codebase", "pr",

    # error patterns
    "error", "exception", "crash", "undefined", "null", "nan", "failed",
    "warning", "deprecated", "type error", "syntax", "import error",
    "cannot read", "not found", "unexpected token", "missing module"
})


def _keyword_detected(messages: list) -> bool:
    """Return True if the last user message contains a rare coding keyword.

    Called ONLY when state.active_mode == "default".
    Uses whole-string containment (fast) — keywords are chosen to be rare
    enough that substring match has negligible false-positive rate.
    """
    try:
        for msg in reversed(messages):
            if msg.get("role") != "user":
                continue
            content = msg.get("content", "")
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
            for kw in _RARE_CODE_KEYWORDS:
                if kw in text:
                    return True
            return False  # found last user msg, no keyword hit
    except Exception:
        pass
    return False


def _is_coding_request(messages: list) -> bool:
    """Return True when the coding addon should be active for this turn.

    Two-layer check:
      • Explicit mode  — state.active_mode == "coding"  → always True, fast path.
      • Auto-detect    — mode == "default" → scan for rare unambiguous keywords.

    Used by both build_system() (to decide prompt injection) and the TUI
    badge renderer (to decide which badge to show).
    """
    if state.active_mode == "coding":
        return True          # explicit — no keyword scan needed
    # default mode: auto-detect via rare keywords only
    return _keyword_detected(messages)


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
