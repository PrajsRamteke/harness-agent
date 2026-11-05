"""``list_projects`` — which projects this Jarvis can reach (workspace links / master group).

Offered only when the current project links others or is a master group's hub, so a plain
single-folder session pays no tokens for it.
"""
from __future__ import annotations

import os
from typing import Any

WORKSPACE_TOOLS = [{
    "name": "list_projects",
    "description": (
        "List the projects you can access from here: this one plus every linked project (or, in a "
        "master group, every member), with path, branch, write policy and whether its Jarvis is running. "
        "Call it when the user asks which projects you have access to."
    ),
    "input_schema": {"type": "object", "properties": {}},
}]

_POLICY = {"deny": "read-only", "ask": "ask before edit", "allow": "editable"}


def _ws():
    from ..storage import workspace

    return workspace


def exposed() -> bool:
    """This project links others, or is a group hub."""
    try:
        ws = _ws()
        here = ws.current()
        return here is not None and (ws.is_group(here) or bool(ws.links_of(here)))
    except Exception:
        return False


def _branch(root: str) -> str:
    from ..constants.system_prompt import _head_branch

    return _head_branch(root)


def _running_paths() -> set[str]:
    try:
        from ..web import registry

        return {os.path.realpath(str(r["cwd"])) for r in registry.list_instances() if r.get("cwd")}
    except Exception:
        return set()


def list_projects(**_: Any) -> str:
    ws = _ws()
    here = ws.current()
    if here is None:
        return (f"This folder ({os.getcwd()}) isn't in the user's workspace, so you only have access to "
                "this one folder.")
    roots = ws.roots_for()
    others = [r for r in roots if not r.get("primary")]
    running = _running_paths()
    lines: list[str] = []
    if ws.is_group(here):
        n = len(others)
        lines.append(f"You are the MASTER of the group '{here['name']}' (hub folder {here['path']}). "
                     f"You have access to {n} project{'s' if n != 1 else ''}:")
    else:
        n = len(others)
        branch = _branch(roots[0]["path"])
        lines.append(f"You are in project '{here['id']}' ({roots[0]['path']}{f', {branch}' if branch else ''}). "
                     f"You have access to {n} linked project{'s' if n != 1 else ''} besides it:")
    for i, r in enumerate(others, 1):
        branch = _branch(r["path"])
        state = "running" if os.path.realpath(r["path"]) in running else "not running"
        lines.append(f"{i}. {r['id']} — {r.get('name') or r['id']} · {r['path']}"
                     f"{f' · {branch}' if branch else ''} · {_POLICY.get(r.get('write'), 'ask before edit')}"
                     f" · {state}")
    if not others:
        lines.append("(none — link projects in the workspace to reach them)")
    else:
        lines.append("Use `<id>:path` in file tools, or project='<id>' / project='*' on search and shell tools.")
    return "\n".join(lines)
