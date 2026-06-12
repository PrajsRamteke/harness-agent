"""Plan mode: read-only research until the user approves a presented plan.

While ``state.plan_mode`` is True the tool router only exposes the read-only
allowlist below plus ``exit_plan_mode``. The model researches, writes a plan,
and calls ``exit_plan_mode`` with it; the user approves or rejects via the
same ask-user flow used by ``ask_user_question`` (askbar in the TUI, numbered
prompt in the legacy REPL). Approval flips plan mode off so the very next
API call regains the full tool set within the same turn.
"""
from __future__ import annotations

import json

from ..console import console, Panel
from .. import state

# Tools that cannot mutate the user's machine, repo, or stored data.
# Everything else (file writes, shell, macOS control, memory/lesson writes,
# MCP tools — unknowable side effects) is withheld while planning.
PLAN_MODE_ALLOWED = frozenset({
    # files / docs (read)
    "read_file", "read_document",
    # discovery
    "list_dir", "glob_files", "rank_files", "fast_find", "search_code",
    # git (read)
    "git_status", "git_diff", "git_log",
    # context bundles
    "resolve_context", "read_bundle",
    # internet (read)
    "web_search", "fetch_url", "verified_search",
    # ocr (read)
    "read_image_text", "read_images_text",
    # storage (read)
    "memory_list", "lesson_search", "lesson_list",
    # skills (read/load — instructions only)
    "skill_list", "skill_load",
    # user interaction
    "ask_user_question",
    # the exit gate itself
    "exit_plan_mode",
})


PLAN_TOOLS = [
    {"name": "exit_plan_mode", "description": (
        "You are in PLAN MODE (read-only). Call this ONLY when your research is "
        "done and you are ready to present the implementation plan for user "
        "approval. Pass the full plan as markdown: goal, files to change, "
        "step-by-step edits, and verification steps. If the user approves, plan "
        "mode ends and you may start editing files immediately. If rejected, "
        "stay in plan mode and revise based on their feedback. Do NOT use this "
        "for questions — use ask_user_question instead."
    ),
     "input_schema": {"type": "object", "properties": {
        "plan": {"type": "string", "description": (
            "The complete implementation plan in markdown. Concrete enough to "
            "execute: which files, what changes, in what order, how to verify."
        )},
     }, "required": ["plan"]}},
]


_APPROVAL_QUESTION = [{
    "id": "plan_approval",
    "prompt": "Approve this plan?",
    "header": "plan mode",
    "options": [
        {"id": "approve", "label": "Yes — approve and start implementing",
         "description": "Exits plan mode; the agent may edit files and run commands."},
        {"id": "revise", "label": "No — keep planning",
         "description": "Stays in plan mode; tell the agent what to change."},
    ],
}]


def exit_plan_mode(plan: str = "", **kwargs) -> str:
    """Present the plan, ask for approval, and exit plan mode if approved."""
    if not state.plan_mode:
        return (
            "ERROR: not in plan mode — nothing to exit. Proceed with the task "
            "directly; you already have full tool access."
        )
    plan = (plan or kwargs.get("Plan") or "").strip()
    if not plan:
        return "ERROR: 'plan' is required — pass the full implementation plan as markdown."

    try:
        from ..console import Markdown
        body = Markdown(plan)
    except Exception:
        body = plan
    console.print(Panel(body, title="◆ proposed plan", border_style="cyan", padding=(0, 1)))

    from .ask_user import ask_user_question
    raw = ask_user_question(questions=_APPROVAL_QUESTION)
    try:
        answers = json.loads(raw).get("answers", [])
        selected = answers[0]["selected_ids"][0] if answers else ""
    except Exception:
        selected = ""

    if selected == "approve":
        state.plan_mode = False
        try:
            from ..repl.system import invalidate_system_cache
            invalidate_system_cache()
        except Exception:
            pass
        console.print("[green]✓ plan approved — plan mode off[/]")
        return (
            "Plan APPROVED by the user. Plan mode is now OFF and the full tool "
            "set is available. Begin implementing the approved plan now."
        )

    return (
        "Plan NOT approved — you are still in plan mode (read-only tools only). "
        "Ask the user what to change (ask_user_question or plain text), revise "
        "the plan, and call exit_plan_mode again when ready."
    )
