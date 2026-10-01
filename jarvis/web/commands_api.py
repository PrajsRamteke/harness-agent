"""Web API for custom slash commands — the browser's ``/command``.

Thin wrappers over ``jarvis.storage.commands``: the same files the terminal's
/command manager edits, so a command made in the browser runs in the
terminal and the other way round. Runs on the HTTP handler thread (files
only, nothing slow); every function returns ``{"ok": bool, …}``.

Routes (see ``handler.py``)::

    GET  /api/commands               every command: where it lives, its template, if Jarvis sees it
    POST /api/commands/save          {name, description, argument_hint, body, scope?, path?}
    POST /api/commands/delete        {name, path?}
    POST /api/commands/copy          {name, scope}   copy into this project / global

The global on/off switch is the ``commands_scope`` action (``actions_api``)
— settings are written on the terminal's thread.
"""
from __future__ import annotations

from typing import Any

from .. import state
from ..constants import HARNESS_COMMANDS_DIR, PROJECT_COMMANDS_DIRNAME
from ..storage import commands as cc
from ..utils import origins

_ACTIONS = {"/api/commands/save", "/api/commands/delete", "/api/commands/copy"}


def handles(path: str) -> bool:
    return path in _ACTIONS


def _err(msg: str, **extra: Any) -> dict[str, Any]:
    return {"ok": False, "error": msg, **extra}


def _row(rec: dict[str, Any], active: set[str], reserved: set[str]) -> dict[str, Any]:
    body = rec.get("_body") or ""
    hint = rec.get("argument_hint") or ""
    used = cc.placeholders_in(body)
    tool = origins.tool_for_path(rec.get("path") or "")
    return {
        "name": rec["name"],
        "description": rec.get("description") or "",
        "argument_hint": hint,
        "body": body.strip(),
        "placeholders": used,
        # Picking it in the slash menu leaves room to type: it uses what you type.
        "takes_args": bool(used or hint),
        "scope": rec.get("scope") or cc.SCOPE_PROJECT,
        "path": rec.get("path") or "",
        "source_dir": rec.get("source_dir") or "",
        "tool": tool,
        "tool_label": origins.tool_label(tool),
        "active": rec["name"] in active,
        # A built-in with the same name always runs instead.
        "builtin": rec["name"] in reserved,
    }


def list_commands() -> dict[str, Any]:
    everything = cc.discover_commands(force=True, include_global=True)
    active = {c["name"] for c in cc.discover_commands(force=True)}
    reserved = cc.reserved_names()
    rows = [_row(c, active, reserved) for c in everything]
    rows.sort(key=lambda r: (r["scope"] != cc.SCOPE_PROJECT, r["name"]))
    return {
        "ok": True,
        "commands": rows,
        "global_commands": bool(getattr(state, "global_commands", True)),
        "hidden_global_count": sum(1 for r in rows if not r["active"]),
        "project_dir": str(cc._find_project_root() / PROJECT_COMMANDS_DIRNAME),
        "global_dir": str(HARNESS_COMMANDS_DIR),
        "reserved": sorted(reserved),
    }


def _known(path: str) -> dict[str, Any] | None:
    """The discovered command at ``path`` — the page may only edit those."""
    for rec in cc.discover_commands(force=True, include_global=True):
        if rec.get("path") == path:
            return rec
    return None


def _save(data: dict[str, Any]) -> dict[str, Any]:
    name = str(data.get("name") or "").strip().lstrip("/").lower()
    description = str(data.get("description") or "")
    body = str(data.get("body") or "")
    hint = data.get("argument_hint")
    path = str(data.get("path") or "").strip()
    scope = str(data.get("scope") or "").strip().lower()
    if scope not in (cc.SCOPE_PROJECT, cc.SCOPE_GLOBAL):
        scope = cc.SCOPE_PROJECT

    existing = None
    if path:
        existing = _known(path)
        if existing is None:
            return _err("That command isn’t there any more. Reload the list and try again.")
    # Another command already answers to this name (a rename onto it, or a
    # new one with a name in the other scope that would shadow or be shadowed).
    clash = next(
        (c for c in cc.discover_commands(force=True, include_global=True)
         if c["name"] == name and (existing is None or c["path"] != existing["path"])),
        None,
    )
    if clash is not None:
        where = "this project" if clash.get("scope") == cc.SCOPE_PROJECT else "global"
        return _err(f"/{name} already exists ({where}). Edit that one, or pick another name.",
                    field="name")

    ok, out = cc.write_command(
        name, description, body,
        scope=scope,
        existing_path=existing["path"] if existing else None,
        argument_hint=None if hint is None else str(hint),
    )
    if not ok:
        field = "body" if "empty" in out else "name"
        return _err(out[:1].upper() + out[1:] + ("" if out.endswith(".") else "."), field=field)
    return {"ok": True, "name": name, "path": out, "created": existing is None}


def _delete(data: dict[str, Any]) -> dict[str, Any]:
    name = str(data.get("name") or "").strip().lstrip("/").lower()
    path = str(data.get("path") or "").strip()
    if path:
        rec = _known(path)
        if rec is None or rec["name"] != name:
            return _err("That command isn’t there any more. Reload the list and try again.")
    ok, out = cc.delete_command(name)
    return {"ok": True, "name": name, "path": out} if ok else _err(out)


def _copy(data: dict[str, Any]) -> dict[str, Any]:
    name = str(data.get("name") or "").strip().lstrip("/").lower()
    scope = str(data.get("scope") or "").strip().lower()
    if scope == cc.SCOPE_GLOBAL:
        res = cc.export_command_to_global(name)
    elif scope == cc.SCOPE_PROJECT:
        res = cc.import_command_to_project(name)
    else:
        return _err("scope must be 'project' or 'global'")
    if res.get("error"):
        return _err(res["error"])
    if not res.get("added"):
        where = "this project" if scope == cc.SCOPE_PROJECT else "global"
        return _err(f"/{name} is already in {where}.")
    return {"ok": True, "name": name, "path": res.get("path", ""), "scope": scope}


def run(path: str, data: dict[str, Any]) -> dict[str, Any]:
    if path == "/api/commands/save":
        return _save(data)
    if path == "/api/commands/delete":
        return _delete(data)
    if path == "/api/commands/copy":
        return _copy(data)
    return _err("unknown route")


__all__ = ["handles", "list_commands", "run"]
