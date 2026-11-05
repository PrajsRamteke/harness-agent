"""Web API for the workspace (``storage/workspace.py``): the user's saved projects.

Host-level routes — answered by whichever Jarvis the page talks to, like
``/api/projects/*`` (they describe every project, not the one on screen):

``GET  /api/workspace``            projects (in order) + runtime state + settings
``GET  /api/workspace/terminals``  every live shell of every running workspace project
``GET  /api/workspace/scan?path=`` git repos found inside a folder (for "add all")
``POST /api/workspace/<op>``       add · update · remove · reorder · open · stop ·
                                   settings · import_order

A project row::

    {id, name, path, display, color, icon, startup, links, link_write, added_at,
     last_state, slot, missing, status: running|starting|stopped|missing,
     branch, instance_id, instances: [{id, self, busy, busy_for, needs_approval,
     session_id, session_title, model, headless, terminals}]}

Changes that widen what Jarvis can reach (adding a folder, links, the write
policy, settings) are refused for a visit that didn't come direct (an Anywhere
tunnel) — same rule as the terminal.
"""
from __future__ import annotations

import os
import time
from pathlib import Path
from typing import Any

from ..storage import workspace as ws
from . import fs_api, instance_client, registry

DIRECT_ONLY_OPS = {"add", "settings", "import_order", "create_group"}
DIRECT_ONLY_FIELDS = {"links", "link_write", "startup", "new_id"}
SCAN_MAX = 60
SCAN_SKIP = {"node_modules", ".git", "__pycache__", ".venv", "venv", "dist", "build", "target"}


def _inst_row(rec: dict[str, Any], self_id: str | None) -> dict[str, Any]:
    now = time.time()
    since = rec.get("busy_since")
    busy = bool(rec.get("busy"))
    return {
        "id": rec.get("id"),
        "self": rec.get("id") == self_id,
        "busy": busy,
        "busy_for": max(0, int(now - since)) if busy and isinstance(since, (int, float)) else None,
        "needs_approval": bool(rec.get("needs_approval")),
        "session_id": rec.get("session_id"),
        "session_title": rec.get("session_title") or "",
        "model": rec.get("model") or "",
        "headless": bool(rec.get("headless")),
        "terminals": int(rec.get("terminals") or 0),
        "branch": rec.get("branch") or "",
    }


def _starting_paths() -> set[str]:
    """Folders a launch is booting right now (``launcher.starting_paths`` when it has one)."""
    try:
        from . import launcher

        fn = getattr(launcher, "starting_paths", None)
        return {os.path.realpath(p) for p in fn()} if callable(fn) else set()
    except Exception:
        return set()


def project_rows(self_id: str | None, data: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    data = data or ws.load()
    live = registry.list_instances()
    starting = _starting_paths()
    rows: list[dict[str, Any]] = []
    for slot, proj in enumerate(ws.projects(data), start=1):
        real = os.path.realpath(proj["path"])
        insts = [r for r in live if r.get("cwd") and os.path.realpath(str(r["cwd"])) == real]
        missing = not os.path.isdir(real)
        if missing:
            status = "missing"
        elif insts:
            status = "running"
        elif real in starting:
            status = "starting"
        else:
            status = "stopped"
        inst_rows = [_inst_row(r, self_id) for r in insts]
        # Prefer the one this page talks to, then a busy one, then the oldest.
        best = sorted(inst_rows, key=lambda r: (not r["self"], not r["busy"]))[:1]
        group = ws.is_group(proj)
        rows.append({
            **{k: proj[k] for k in ("id", "name", "path", "color", "icon", "startup", "links", "link_write",
                                    "added_at", "last_state", "kind", "master_session")},
            # A group's `links` are its members; a project lists the groups it belongs to.
            "members": list(proj["links"]) if group else [],
            "masters": [] if group else [
                {"id": g["id"], "name": g["name"], "color": g["color"]} for g in ws.groups_of(proj["id"], data)],
            "display": fs_api.display(proj["path"]),
            "slot": slot,
            "missing": missing,
            "status": status,
            "branch": (best[0]["branch"] if best else "") or str(proj["last_state"].get("branch") or ""),
            "instance_id": best[0]["id"] if best else None,
            "instances": inst_rows,
        })
    return rows


def payload(self_id: str | None) -> dict[str, Any]:
    data = ws.load()
    rows = project_rows(self_id, data)
    known = {os.path.realpath(r["path"]) for r in rows}
    ad_hoc = [
        _inst_row(r, self_id) | {"cwd": str(r.get("cwd") or ""), "project": str(r.get("project") or "")}
        for r in registry.list_instances()
        if r.get("cwd") and os.path.realpath(str(r["cwd"])) not in known
    ]
    current = None
    if self_id:
        mine = registry.get(self_id)
        if mine and mine.get("cwd"):
            found = ws.find_by_path(str(mine["cwd"]), within=True, data=data)
            current = found["id"] if found else None
    return {
        "ok": True,
        "revision": ws.revision(),
        "self": self_id or "",
        "current": current,
        "projects": rows,
        "ad_hoc": ad_hoc,
        "settings": data["settings"],
        "max_projects": ws.MAX_PROJECTS,
    }


def terminals_payload(self_id: str | None, *, forwarded_for: str | None = None) -> dict[str, Any]:
    """Every shell of every running workspace project, grouped by project (no tokens)."""
    data = ws.load()
    groups: list[dict[str, Any]] = []
    for row in project_rows(self_id, data):
        for inst in row["instances"]:
            try:
                got = instance_client.get_json(inst["id"], "/api/terminal/list", timeout=4.0,
                                               forwarded_for=forwarded_for)
            except instance_client.InstanceError:
                continue
            if not got.get("ok"):
                continue
            groups.append({
                "project_id": row["id"], "name": row["name"], "color": row["color"], "kind": row["kind"],
                "instance_id": inst["id"], "self": inst["self"],
                "allowed": bool(got.get("allowed")),
                "terminals": got.get("terminals") or [],
            })
    return {"ok": True, "groups": groups}


def scan(raw: str) -> dict[str, Any]:
    """Git repos directly inside ``raw`` (one level) — the "add all N repos" suggestion."""
    try:
        base = fs_api.resolve_dir(raw)
    except fs_api.FsError as exc:
        return {"ok": False, "code": exc.code, "error": str(exc)}
    found: list[dict[str, Any]] = []
    try:
        entries = sorted(base.iterdir(), key=lambda p: p.name.lower())
    except OSError:
        return {"ok": False, "code": "no_access", "error": "Couldn't read that folder."}
    data = ws.load()
    for entry in entries:
        if entry.name.startswith(".") or entry.name in SCAN_SKIP or not entry.is_dir():
            continue
        if (entry / ".git").exists():
            found.append({"path": str(entry), "name": entry.name, "display": fs_api.display(entry),
                          "in_workspace": ws.find_by_path(entry, data=data) is not None})
        if len(found) >= SCAN_MAX:
            break
    return {"ok": True, "path": str(base), "repos": found}


def _err(exc: ws.WorkspaceError) -> dict[str, Any]:
    return {"ok": False, "code": exc.code, "error": str(exc)}


def post(op: str, data: dict[str, Any], *, direct: bool, self_id: str | None) -> dict[str, Any]:
    """One mutation. Never raises; the answer always has ``ok``."""
    if op in DIRECT_ONLY_OPS and not direct:
        return {"ok": False, "code": "remote",
                "error": "Change the workspace from this computer or your own network."}
    try:
        if op == "add":
            paths = data.get("paths")
            if not isinstance(paths, list):
                paths = [data.get("path")] if data.get("path") else []
            result = ws.add_many([str(p) for p in paths][:ws.MAX_PROJECTS])
            for proj in result["added"]:
                fs_api.remember_dir(proj["path"])
            if data.get("open"):
                from . import launcher

                for proj in result["added"][:1]:
                    launcher.open_project(proj["path"], reuse=True)
            return {"ok": bool(result["added"]) or not result["errors"], **result}
        if op == "create_group":
            members = data.get("members")
            group = ws.create_group(str(data.get("name") or ""),
                                    [str(m) for m in members] if isinstance(members, list) else [])
            fs_api.remember_dir(group["path"])
            if data.get("open"):
                from . import launcher

                launcher.open_project(group["path"], reuse=True)
            return {"ok": True, "project": group}
        if op == "update":
            pid = str(data.get("id") or "")
            fields = {k: data.get(k) for k in ("name", "color", "icon", "startup", "link_write", "links", "new_id")
                      if k in data}
            if not direct and DIRECT_ONLY_FIELDS & set(fields):
                return {"ok": False, "code": "remote",
                        "error": "Change links and start-up from this computer or your own network."}
            return {"ok": True, "project": ws.update(pid, **fields)}
        if op == "remove":
            return {"ok": True, "project": ws.remove(str(data.get("id") or ""))}
        if op == "reorder":
            order = data.get("order")
            if not isinstance(order, list):
                return {"ok": False, "code": "bad_value", "error": "order must be a list of project ids."}
            return {"ok": True, "order": ws.reorder([str(x) for x in order])}
        if op == "import_order":
            paths = data.get("paths")
            return {"ok": True, "order": ws.import_order([str(p) for p in paths] if isinstance(paths, list) else [])}
        if op == "settings":
            return {"ok": True, "settings": ws.update_settings(**{
                k: data[k] for k in ws.DEFAULT_SETTINGS if k in data})}
        if op == "open":
            proj = ws.get(str(data.get("id") or ""))
            if proj is None:
                return {"ok": False, "code": "not_found", "error": "That project isn't in your workspace."}
            from . import launcher

            resume = data.get("resume")
            if resume is None and ws.is_group(proj) and proj.get("master_session"):
                resume = proj["master_session"]  # a group always opens on its one shared master chat
                reuse = True
            else:
                reuse = resume is None
            res = launcher.open_project(
                proj["path"], reuse=reuse,
                resume_session=int(resume) if isinstance(resume, int) or str(resume or "").isdigit() else None,
            )
            return {**res, "project_id": proj["id"]}
        if op == "stop":
            proj = ws.get(str(data.get("id") or ""))
            if proj is None:
                return {"ok": False, "code": "not_found", "error": "That project isn't in your workspace."}
            from . import launcher

            real = os.path.realpath(proj["path"])
            stopped, failed = [], []
            for rec in registry.list_instances():
                if rec.get("cwd") and os.path.realpath(str(rec["cwd"])) == real:
                    res = launcher.stop_project(str(rec.get("id")))
                    (stopped if res.get("ok") else failed).append(
                        {"id": rec.get("id"), "error": str(res.get("error") or "")})
            return {"ok": not failed, "stopped": stopped, "failed": failed}
    except ws.WorkspaceError as exc:
        return _err(exc)
    except (OSError, ValueError) as exc:
        return {"ok": False, "code": "error", "error": str(exc)}
    return {"ok": False, "code": "unknown_op", "error": f"Unknown workspace operation '{op}'."}


def shown_path(path: str | Path) -> str:
    return fs_api.display(path)
