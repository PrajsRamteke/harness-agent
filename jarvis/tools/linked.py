"""Which project roots a tool call touches: this project, plus the ones it links.

A tool's optional ``project`` argument is an id, a list of ids (or a comma-separated
string) or ``"*"`` (this project and every linked one). Everything here reads the
workspace lazily and degrades to "just the current folder" when there is none.
"""
from __future__ import annotations

import pathlib
from typing import Any

from .. import constants


def _cwd() -> pathlib.Path:
    return pathlib.Path(constants.CWD)


def _workspace_roots() -> list[dict]:
    try:
        from ..storage import workspace

        return workspace.roots_for()
    except Exception:
        return []


def primary_root() -> dict[str, Any]:
    """The root this Jarvis runs in — a workspace project, or just the folder."""
    for root in _workspace_roots():
        if root.get("primary"):
            return root
    return {"id": "", "path": str(_cwd()), "primary": True, "write": "allow", "name": ""}


def linked_ids() -> list[str]:
    return [r["id"] for r in _workspace_roots() if not r.get("primary")]


def _split(project: Any) -> list[str]:
    if project is None:
        return []
    if isinstance(project, str):
        items = [p for p in project.replace(",", " ").split() if p]
    elif isinstance(project, (list, tuple)):
        items = [str(p).strip() for p in project if str(p).strip()]
    else:
        items = [str(project).strip()]
    return list(dict.fromkeys(items))


def unknown_project_error(pid: str) -> str:
    try:
        from ..storage import workspace

        known = workspace.get(pid) is not None
    except Exception:
        known = False
    if known:
        return (f"ERROR: project '{pid}' isn't linked to this project. Link it in the workspace "
                f"(/project link {pid}) before using it.")
    have = ", ".join(linked_ids()) or "none"
    return f"ERROR: unknown project '{pid}'. Linked projects: {have}."


def resolve_roots(project: Any) -> tuple[list[dict], str | None]:
    """The roots ``project`` selects (``None`` / empty → this project only) or an ERROR string."""
    ids = _split(project)
    primary = primary_root()
    if not ids:
        return [primary], None
    roots = _workspace_roots()
    if ids == ["*"] or "*" in ids:
        return (roots or [primary]), None
    by_id = {r["id"]: r for r in roots}
    out: list[dict] = []
    for pid in ids:
        root = by_id.get(pid)
        if root is None:
            if pid == primary["id"]:
                root = primary
            else:
                return [], unknown_project_error(pid)
        out.append(root)
    return out, None


def shell_root(project: Any, *, readonly: bool) -> tuple[dict | None, str | None]:
    """The root a shell command runs in: ``(None, None)`` = the usual project root.

    A ``deny`` link only ever runs commands that change nothing; ``ask`` links go through
    the normal shell approval (which names the project).
    """
    ids = _split(project)
    if not ids:
        return None, None
    if len(ids) > 1 or ids == ["*"]:
        return None, "ERROR: `project` for a shell command must be a single project id."
    roots, err = resolve_roots(ids)
    if err:
        return None, err
    root = roots[0]
    if root.get("primary"):
        return None, None
    if root.get("write") == "deny" and not readonly:
        return None, (f"ERROR: project {root['id']} is linked read-only, so only commands that "
                      "change nothing may run there. Change Write policy in the workspace Links "
                      "dialog or with /project link.")
    return root, None


def prefix(root: dict, rel: str) -> str:
    """``rel`` as the model should name it: ``api:src/x.ts`` outside the primary root."""
    return rel if root.get("primary") or not root.get("id") else f"{root['id']}:{rel}"


def base_for(root: dict, path: str) -> tuple[pathlib.Path | None, str | None]:
    """``path`` (default ``.``) read relative to ``root`` — plain relative paths only;
    ``id:path`` refs and absolute paths are taken as they are."""
    from ..path_resolve import robust_resolve

    raw = (path or ".").strip() or "."
    p = pathlib.Path(raw).expanduser()
    if p.is_absolute() or ":" in raw and raw[:1].isalnum():
        try:
            return robust_resolve(raw), None
        except Exception as exc:
            return None, f"ERROR: {exc}"
    return robust_resolve(raw, pathlib.Path(root["path"])), None
