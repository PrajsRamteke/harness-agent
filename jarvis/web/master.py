"""The master tab of a workspace *group* (UI-free).

A group (``storage/workspace.py``) has a hub folder where one Jarvis keeps the group's shared
**master chat**: its first tab. Every member project shows that chat as a pinned tab; clicking it
opens the hub. This module says, for the Jarvis on this page:

* ``master``  — this Jarvis *is* a hub: ``{id, name, session_id, members}``;
* ``masters`` — it is a member of these groups: ``[{id, name, color, session_id}]``;

and remembers which saved chat is the master one (``adopt``).
"""
from __future__ import annotations

from typing import Any

from ..storage import workspace as ws


def fields(cwd: str | None = None) -> dict[str, Any]:
    """``{master, masters}`` for the project at ``cwd`` (default: this process's). Never raises."""
    out: dict[str, Any] = {"master": None, "masters": []}
    try:
        proj = ws.current(cwd)
        if proj is None:
            return out
        data = ws.load()
        if ws.is_group(proj):
            out["master"] = {
                "id": proj["id"], "name": proj["name"], "color": proj["color"],
                "session_id": proj.get("master_session"),
                "members": [{"id": m["id"], "name": m["name"], "color": m["color"]}
                            for m in ws.members(proj, data)],
            }
        else:
            out["masters"] = [
                {"id": g["id"], "name": g["name"], "color": g["color"], "session_id": g.get("master_session")}
                for g in ws.groups_of(proj["id"], data)
            ]
    except Exception:
        pass
    return out


def adopt(cwd: str | None = None) -> bool:
    """In a hub with no master chat yet, make its first tab's saved chat the master. True if recorded."""
    try:
        proj = ws.current(cwd)
        if proj is None or not ws.is_group(proj) or proj.get("master_session"):
            return False
        from .. import tabs

        first = tabs.all_tabs()[:1]
        sid = first[0].session_id if first else None
        if not isinstance(sid, int) or isinstance(sid, bool):
            return False
        ws.set_master_session(proj["id"], sid)
        return True
    except Exception:
        return False
