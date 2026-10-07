"""Build the system prompt (with live datetime + pinned context + OAuth identity).

Cache: the expensive memory+skills+body string is rebuilt only when the
memory/skills content actually changes between turns. The date/time line is
always fresh but is cheap (~50 tokens) so we splice it in separately.

Agent addon logic:
  When ``state.active_agent`` is set (resolved record from storage.agents),
  the agent's body markdown is appended as an addon. With no active agent,
  the base system prompt is used unchanged.
"""
from datetime import datetime
from typing import Union, List, Dict

from ..constants import OAUTH_IDENTITY, AUTH_OAUTH
from ..constants.providers import PROVIDER_LABELS, MODEL_INFO
from ..constants.system_prompt import build_base_system
from ..storage.memory import as_prompt_block
from ..storage.lessons import as_prompt_block as lessons_prompt_block
from ..storage.skills import as_prompt_block as skills_prompt_block
from ..mcp.registry import as_prompt_block as mcp_prompt_block
from ..storage.agents import (
    as_prompt_block as agents_prompt_block,
    load_agent_body,
)
from .. import state

# ── cache state ────────────────────────────────────────────────────────────────
_cached_body: str = ""
_cached_mem_key: str = ""
_cached_sk_key: str = ""
_cached_skills_key: str = ""
_cached_mcp_key: str = ""
_cached_agents_key: str = ""
_cached_pinned: str = ""
_cached_cwd_branch: str = ""
_cached_ctx_key: str = ""


# The prompt as sent for the current user turn (see build_system).
_turn_prompt: dict = {"turn": None, "critical": None, "value": None}


def invalidate_system_cache() -> None:
    """Force the system prompt body to rebuild on the next turn."""
    global _cached_body, _cached_mem_key, _cached_sk_key, _cached_skills_key
    global _cached_mcp_key, _cached_agents_key, _cached_pinned, _cached_cwd_branch, _cached_ctx_key
    _turn_prompt["value"] = None
    _cached_body = ""
    _cached_mem_key = ""
    _cached_sk_key = ""
    _cached_skills_key = ""
    _cached_mcp_key = ""
    _cached_agents_key = ""
    _cached_pinned = ""
    _cached_cwd_branch = ""
    _cached_ctx_key = ""


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


_OAUTH_IDENTITY_OVERRIDE = (
    "OAUTH WIRE BLOCK (ignore for user-facing answers):\n"
    "The system message immediately before this one is an Anthropic OAuth "
    "wire-protocol requirement only — NOT your identity. When asked who you "
    "are, always answer as Jarvis (Harness Agent).\n\n"
)


def _selected_model_block() -> str:
    """Selected model id + display name + provider — injected every turn."""
    model_id = state.MODEL
    info = MODEL_INFO.get(model_id)
    model_name = info[0] if info else model_id
    if state.harness_agent_free:
        provider_label = "Harness Agent (free)"
    else:
        provider_label = PROVIDER_LABELS.get(state.provider, state.provider)
    return (
        f"SELECTED MODEL: {model_id}\n"
        f"MODEL NAME: {model_name}\n"
        f"PROVIDER: {provider_label}\n\n"
    )


def _agent_addon_block() -> str:
    """Return the active agent's body as an addon, or ''.

    Resolves ``state.active_agent`` lazily — if only the name is set
    (e.g. just after a settings reload), this triggers the storage lookup.
    """
    rec = state.active_agent
    if rec is None and state.active_agent_name:
        rec = state.resolve_active_agent()
    if not rec:
        return ""
    body = load_agent_body(rec["name"])
    if not body:
        body = rec.get("_body") or ""
    body = body.strip()
    if not body:
        return ""
    return "\n\n" + body


_PLAN_MODE_BLOCK = (
    "\n\nPLAN MODE — ACTIVE (read-only):\n"
    "The user enabled plan mode. You may ONLY research: read files, search, "
    "inspect git, fetch docs, and ask questions. File edits, shell commands, "
    "and any mutating tools are unavailable and will be rejected.\n"
    "Workflow: 1) investigate the codebase until you understand the change; "
    "2) write a concrete implementation plan (files, edits, order, "
    "verification); 3) call exit_plan_mode with the full plan to request user "
    "approval. Do not describe edits as done — nothing can be changed until "
    "the plan is approved."
)


def _subagents_block() -> str:
    """When to reach for spawn_agents (only while parallel subagents are on)."""
    try:
        from ..subagents import enabled
        if not enabled():
            return ""
    except Exception:
        return ""
    return (
        "\n\nPARALLEL AGENTS (spawn_agents)\n"
        "For a BIG task with independent parts — exploring/auditing several areas, several "
        "research questions, or changes across separate files/modules — split it and run 2-6 "
        "subagents at once with ONE spawn_agents call instead of doing every part in sequence. "
        "Each brief must stand alone (agents can't see this chat); give edit agents disjoint "
        "files. Keep small or tightly sequential work to yourself. Afterwards verify what "
        "matters and combine the reports into one answer."
    )


def _subagents_request_block() -> str:
    """This turn's message asked for (sub)agents outright: say it plainly —
    weaker models otherwise ignore the tool and do everything themselves."""
    try:
        from ..subagents import enabled
        from ..subagents.tool import asked_for_agents

        if not enabled() or not asked_for_agents():
            return ""
    except Exception:
        return ""
    return (
        "\n\nTHIS TURN: THE USER EXPLICITLY ASKED FOR MULTIPLE / PARALLEL SUBAGENTS.\n"
        "You MUST do the main work with the spawn_agents tool — doing it all yourself ignores "
        "the request. Steps:\n"
        "1. Look only as far as you need to plan the split (a few reads at most).\n"
        "2. If the agents build one thing together, create the shared ground first in a few "
        "calls: the target folder, the file layout, shared conventions (CSS variables, data "
        "shapes, global names) — e.g. a short SPEC.md they all read.\n"
        "3. Call spawn_agents ONCE with 2-6 agents; builders use mode 'edit' and each owns "
        "different files. Every brief is self-contained: absolute paths, the conventions, "
        "what to build, what to report. Attached images are shown to every agent.\n"
        "4. When they report, wire the parts together, check the result, fix gaps, then answer."
    )


def _plan_mode_block() -> str:
    """Plan-mode addon — appended dynamically; never part of the cached body."""
    return _PLAN_MODE_BLOCK if state.plan_mode else ""


def _loop_block() -> str:
    """/loop instructions while a loop is active (see jarvis/loop.py)."""
    try:
        from ..loop import prompt_block

        return prompt_block()
    except Exception:
        return ""


def _background_jobs_block() -> str:
    """Live run_bg jobs (running, or finished with unread output)."""
    try:
        from ..tools.background import prompt_block

        return prompt_block()
    except Exception:
        return ""


def _build_static_body() -> str:
    """Everything except the date/time line and agent addon. Cached between turns."""
    global _cached_body, _cached_mem_key, _cached_sk_key, _cached_skills_key
    global _cached_mcp_key, _cached_agents_key, _cached_pinned, _cached_cwd_branch, _cached_ctx_key

    mem_block = as_prompt_block()
    sk_block = lessons_prompt_block()
    skills_block = skills_prompt_block()
    mcp_block = mcp_prompt_block()
    agents_block = agents_prompt_block()
    from ..storage import pin as pin_store
    pinned = pin_store.injection_cache_key()
    pinned_for_prompt = pin_store.injection_text()
    from pathlib import Path
    cwd = str(Path.cwd())
    branch = _get_git_branch(cwd)
    cwd_branch_key = cwd + "|" + (branch or "")

    if (mem_block == _cached_mem_key
            and sk_block == _cached_sk_key
            and skills_block == _cached_skills_key
            and mcp_block == _cached_mcp_key
            and agents_block == _cached_agents_key
            and pinned == _cached_pinned
            and cwd_branch_key == _cached_cwd_branch
            and state.project_context_path == _cached_ctx_key
            and _cached_body):
        return _cached_body

    body = build_base_system(Path(cwd), git_branch=branch)
    if pinned_for_prompt:
        body += "\n\nPINNED CONTEXT (user-supplied, always remember):\n" + pinned_for_prompt

    # ── Lazy-load system prompt section ──────────────────────────────────
    # Skill headers (name + description) ARE injected so the agent can match
    # without an extra tool call. Memory, lessons, agents, and project
    # context remain lazy (summaries/counts only, full content on demand).
    body += (
        "\n\nLAZY-LOAD CONTEXT (full content loaded on demand unless noted):\n"
        "To save tokens, the following are NOT injected with full content:"
        "\n- SKILLS (check first): headers listed below — scan before every task; call skill_load for every header that might match (one or more)."
        "\n- MEMORY: use memory_list() only when saved user facts/preferences matter"
        "\n- LESSONS: use lesson_search('<topic>') only when prior experience could help"
        "\n- PROJECT CONTEXT: use read_file('<project_context_file>') only when repository instructions matter"
    )

    if skills_block:
        body += "\n" + skills_block
    if mem_block:
        body += "\n" + mem_block
    if sk_block:
        body += "\n" + sk_block
    if mcp_block:
        body += "\n" + mcp_block
    if agents_block:
        body += "\n" + agents_block
    if state.project_context_file:
        body += "\n" + f"PROJECT CONTEXT: {state.project_context_file} exists. Use read_file('{state.project_context_file}') only when needed."

    body += (
        "\n\nMEMORY: memory_save/memory_list/memory_delete.\n"
        "- Proactively save durable user facts using memory_save WITHOUT asking — when they become evident.\n"
        "- View full memory with memory_list() only when relevant."
    )

    body += (
        "\n\nLESSONS: lesson_search/lesson_save/lesson_list/lesson_delete.\n"
        "- Use lesson_search when a similar past lesson could help the task.\n"
        "- END of task → lesson_save if you learned something non-obvious.\n"
        "- The system prompt only shows a lesson count/topic cloud."
    )

    if skills_block:
        body += (
            "\n\nSKILLS: skill_load('<name>').\n"
            "- BEFORE responding or acting: scan the skill headers above.\n"
            "- Load every skill whose description might apply — call skill_load once per match.\n"
            "- Multiple skills can apply to the same task; load and follow all of them.\n"
            "- Batch parallel skill_load calls in one turn when several match.\n"
            "- Do not skip skill checks for 'simple' tasks.\n"
            "- If a loaded skill references MCP tools (mcp__*) but MCP is offline or global "
            "scope is off, tell the user to enable /mcp global scope and connect — do NOT "
            "read ~/.claude, ~/.cursor, or other MCP config files as a workaround."
        )

    _cached_body = body
    _cached_mem_key = mem_block
    _cached_sk_key = sk_block
    _cached_skills_key = skills_block
    _cached_mcp_key = mcp_block
    _cached_agents_key = agents_block
    _cached_pinned = pinned
    _cached_cwd_branch = cwd_branch_key
    _cached_ctx_key = state.project_context_path
    return body


def _turn_anchor() -> int:
    """Index of the message that started the current user turn — the newest
    user message carrying anything besides tool results — or -1."""
    msgs = state.messages or []
    for i in range(len(msgs) - 1, -1, -1):
        m = msgs[i]
        if not isinstance(m, dict) or m.get("role") != "user":
            continue
        content = m.get("content")
        if not isinstance(content, list):
            return i
        for block in content:
            kind = block.get("type") if isinstance(block, dict) else getattr(block, "type", None)
            if kind != "tool_result":
                return i
    return -1


def _critical_key() -> tuple:
    """Settings that must reach the model mid-turn (plan approved, model or
    agent switched, folder changed): any change rebuilds the prompt at once."""
    from pathlib import Path

    agent = state.active_agent.get("name") if state.active_agent else state.active_agent_name
    return (
        state.MODEL, state.provider, state.auth_mode, bool(state.plan_mode),
        agent, bool(getattr(state, "harness_agent_free", False)), str(Path.cwd()),
    )


def build_system() -> Union[str, List[Dict]]:
    """System prompt + today's date + active agent addon + pinned user context.

    The active agent's body is appended as an addon when set. Default
    (no active agent) uses the base prompt only.

    When authenticated via OAuth, Anthropic requires the FIRST system block to be
    exactly the OAuth identity string — so we return a 2-block list in that
    case and keep our real instructions in the second block.

    Stable for a whole user turn: every request of one tool loop gets the same
    prompt, so the provider's prompt cache keeps hitting (and Claude 5's
    thinking blocks stay valid — they are bound to the system prompt). Things
    that change mid-turn — background-job timers, a saved memory or lesson, the
    loop countdown — show up from the next user turn; plan/model/agent/folder
    changes apply at once (_critical_key).
    """
    turn = (state.current_session_id, _turn_anchor())
    critical = _critical_key()
    frozen = _turn_prompt
    if (frozen["value"] is not None and turn[1] >= 0
            and frozen["turn"] == turn and frozen["critical"] == critical):
        value = frozen["value"]
        return [dict(b) for b in value] if isinstance(value, list) else value
    value = _build_system_now()
    frozen.update(turn=turn, critical=critical, value=value)
    return [dict(b) for b in value] if isinstance(value, list) else value


def _build_system_now() -> Union[str, List[Dict]]:
    now = datetime.now()
    # Date only: a clock time here changed the prompt every minute, which made
    # every request a prompt-cache miss.
    date_line = (
        f"\n\nCURRENT DATE: {now.strftime('%A, %B %d, %Y')} "
        f"(timezone: {now.astimezone().tzname()})\n"
        "Never assume or guess the date — the above is the real current date injected at runtime. "
        "The time of day is not included; run `date` when the exact time matters."
    )

    body = _selected_model_block() + _build_static_body()
    body += _agent_addon_block()
    body += _subagents_block()
    body += _plan_mode_block()
    body += _background_jobs_block()
    body += _loop_block()
    body += _subagents_request_block()
    body += date_line

    if state.auth_mode == AUTH_OAUTH:
        return [
            {"type": "text", "text": OAUTH_IDENTITY},
            {"type": "text", "text": _OAUTH_IDENTITY_OVERRIDE + body},
        ]
    return body
