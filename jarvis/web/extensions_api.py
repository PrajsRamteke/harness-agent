"""Web API for adding, signing in to and removing skills / MCP servers.

Thin wrappers over ``jarvis.mcp.install`` and ``jarvis.storage.skill_install``
(the same code the agent tools and the terminal use). Everything runs on the
HTTP handler thread — connecting a server can take seconds and must not
freeze the terminal UI — and every function returns ``{"ok": bool, …}``.

Routes (see ``handler.py``)::

    GET  /api/mcp                    servers, health, pending sign-ins, the marketplace (catalog + categories)
    GET  /api/mcp/auth?name=         where a sign-in stands (poll while the browser is open)
    POST /api/mcp/parse              {source}                       what would be added (dry run)
    POST /api/mcp/add                {source, scope, name?, credentials?, connect?, replace?}
    POST /api/mcp/remove             {name, scope?}
    POST /api/mcp/move               {name, scope}
    POST /api/mcp/connect            {name}
    POST /api/mcp/disconnect         {name}
    POST /api/mcp/auth/start         {name}  → {url}  (the Authenticate button)
    POST /api/mcp/auth/paste         {name, address}  sign-in finished on another device
    POST /api/mcp/auth/cancel        {name}
    POST /api/mcp/signout            {name}
    POST /api/mcp/credentials        {name, values: {VAR: value}}
    POST /api/mcp/app                {name, client_id, client_secret?}  sign in through your own OAuth app
    GET  /api/skills                 installed skills (+ where each lives, whether Jarvis manages it)
    POST /api/skills/inspect         {source}  → skills a link holds
    POST /api/skills/install         {source, scope, names?, all?, overwrite?}
    POST /api/skills/remove|move|update
"""
from __future__ import annotations

from typing import Any

from .. import state
from ..mcp import install as mcp_install
from ..mcp.auth import coordinator as auth_coordinator
from ..mcp.config import get_config, reload_config
from ..mcp.registry import mcp_registry
from ..storage import skill_install as skills_install

_MCP_ACTIONS = {
    "/api/mcp/parse", "/api/mcp/add", "/api/mcp/remove", "/api/mcp/move", "/api/mcp/connect",
    "/api/mcp/disconnect", "/api/mcp/auth/start", "/api/mcp/auth/paste", "/api/mcp/auth/cancel",
    "/api/mcp/signout", "/api/mcp/credentials", "/api/mcp/app",
}
_SKILL_ACTIONS = {
    "/api/skills/inspect", "/api/skills/install", "/api/skills/remove", "/api/skills/move", "/api/skills/update",
}


def handles(path: str) -> bool:
    return path in _MCP_ACTIONS or path in _SKILL_ACTIONS


def _err(msg: str, **extra: Any) -> dict[str, Any]:
    return {"ok": False, "error": msg, **extra}


def _name(data: dict[str, Any]) -> str:
    return str(data.get("name") or "").strip()


def _scope(data: dict[str, Any], default: str | None = None) -> str | None:
    raw = str(data.get("scope") or "").strip().lower()
    return raw if raw in ("project", "global") else default


# ── MCP ───────────────────────────────────────────────────────────────────


def mcp_state(query: str = "") -> dict[str, Any]:
    from ..mcp.catalog import oauth_redirect_url

    out = mcp_install.describe_servers(query)
    # Where the browser comes back to after an OAuth-app sign-in (register it with the provider).
    out["oauth_redirect_url"] = oauth_redirect_url()
    return out


def mcp_auth_status(name: str) -> dict[str, Any]:
    name = (name or "").strip()
    connected = mcp_registry.is_connected(name)
    req = auth_coordinator.get(name)
    out: dict[str, Any] = {"ok": True, "name": name, "connected": connected}
    if connected:
        out["status"] = "done"
        out["tool_count"] = len(mcp_registry.get_server_tools(name))
    elif req is not None:
        out.update(req.public())
    else:
        out["status"] = "none"
    return out


def run_mcp(path: str, data: dict[str, Any]) -> dict[str, Any]:
    name = _name(data)

    if path == "/api/mcp/parse":
        return mcp_install.describe_source(str(data.get("source") or ""))

    if path == "/api/mcp/add":
        source = str(data.get("source") or "").strip()
        creds = data.get("credentials")
        res = mcp_install.add_mcp(
            source or None,
            name=name or None,
            scope=_scope(data, "global"),
            connect=bool(data.get("connect", True)),
            replace=bool(data.get("replace", False)),
            credentials={str(k): str(v) for k, v in creds.items()} if isinstance(creds, dict) else None,
        )
        return res

    if path == "/api/mcp/remove":
        return mcp_install.remove_mcp(name, scope=_scope(data))

    if path == "/api/mcp/move":
        to = _scope(data)
        return mcp_install.move_mcp(name, to) if to else _err("scope must be 'project' or 'global'")

    if path == "/api/mcp/credentials":
        values = data.get("values")
        if not isinstance(values, dict) or not values:
            return _err("No values given.")
        return mcp_install.set_credentials(name, {str(k): str(v) for k, v in values.items()})

    if path == "/api/mcp/app":
        client_id = str(data.get("client_id") or "").strip()
        if not client_id:
            return _err("Client ID is empty.")
        return mcp_install.set_oauth_app(name, client_id, str(data.get("client_secret") or ""))

    if path == "/api/mcp/auth/paste":
        return auth_coordinator.submit(name, str(data.get("address") or ""))

    if path == "/api/mcp/auth/cancel":
        auth_coordinator.cancel(name)
        mcp_registry.disconnect(name)
        return {"ok": True}

    reload_config()
    cfg = get_config().get_server(name)
    if cfg is None:
        return _err(f"No MCP server named '{name}' in the current scope.")

    if path == "/api/mcp/disconnect":
        err = mcp_registry.disconnect(name)
        return _err(err) if err else {"ok": True, "name": name}

    if path == "/api/mcp/signout":
        mcp_registry.sign_out(name, cfg)
        return {"ok": True, "name": name}

    if path == "/api/mcp/connect":
        res = mcp_install.server_status(name, connect=True)
        res["ok"] = res.get("status") in ("connected", "auth_required", "added", "needs_credentials")
        return res

    if path == "/api/mcp/auth/start":
        res = mcp_registry.authenticate(name, cfg)
        if res.get("connected"):
            res["status"] = "done"
        return res

    return _err("unknown route")


# ── skills ────────────────────────────────────────────────────────────────


def skills_state(query: str = "") -> dict[str, Any]:
    return skills_install.describe_installed(query)


def run_skills(path: str, data: dict[str, Any]) -> dict[str, Any]:
    name = _name(data)
    source = str(data.get("source") or "").strip()

    if path == "/api/skills/inspect":
        return skills_install.inspect_source(source)

    if path == "/api/skills/install":
        names = data.get("names")
        return skills_install.install_skills(
            source,
            scope=_scope(data, "global") or "global",
            names=[str(n) for n in names] if isinstance(names, list) else None,
            install_all=bool(data.get("all", False)),
            overwrite=bool(data.get("overwrite", False)),
        )

    if path == "/api/skills/remove":
        return skills_install.remove_skill(name, scope=_scope(data))

    if path == "/api/skills/move":
        to = _scope(data)
        return skills_install.move_skill(name, to) if to else _err("scope must be 'project' or 'global'")

    if path == "/api/skills/update":
        return skills_install.update_skill(name)

    return _err("unknown route")


def scope_flags() -> dict[str, Any]:
    return {"global_skills": bool(state.global_skills), "global_mcp": bool(state.global_mcp)}


__all__ = ["handles", "mcp_state", "mcp_auth_status", "run_mcp", "skills_state", "run_skills", "scope_flags"]
