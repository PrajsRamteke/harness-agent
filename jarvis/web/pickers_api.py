"""Read-only list endpoints and helpers for web picker modals."""
from __future__ import annotations

from typing import Any

from ..constants import (
    AUTH_API_KEY,
    AUTH_OAUTH,
    MODEL_SOURCE_LABELS,
    PROVIDER_ANTHROPIC,
    PROVIDER_ANTHROPIC_API,
    PROVIDER_ANTHROPIC_AUTH,
    PROVIDER_HARNESS_AGENT,
    PROVIDER_KIMCHI,
    PROVIDER_OPENAI_CODEX,
    PROVIDER_OPENAI_CODEX_AUTH,
    PROVIDER_OPENCODE_ZEN,
    model_option_id,
    parse_model_option_id,
)
from .. import state
from ..storage.sessions import db_count_sessions, db_list_sessions
from ..tui.model_modal import model_picker_rows
from ..utils.time_fmt import _fmt_ts


def _model_is_active(source: str, model_id: str) -> bool:
    if model_id != state.MODEL:
        return False
    if source == PROVIDER_HARNESS_AGENT:
        return state.provider == PROVIDER_OPENCODE_ZEN and state.harness_agent_free
    if source == PROVIDER_OPENCODE_ZEN:
        return state.provider == PROVIDER_OPENCODE_ZEN and not state.harness_agent_free
    if source == PROVIDER_ANTHROPIC_AUTH:
        return state.provider == PROVIDER_ANTHROPIC and state.auth_mode == AUTH_OAUTH
    if source == PROVIDER_ANTHROPIC_API:
        return state.provider == PROVIDER_ANTHROPIC and state.auth_mode == AUTH_API_KEY
    if source == PROVIDER_OPENAI_CODEX_AUTH:
        return state.provider == PROVIDER_OPENAI_CODEX and state.auth_mode == AUTH_OAUTH
    if source == PROVIDER_KIMCHI:
        return state.provider == PROVIDER_KIMCHI
    return state.provider == source


def list_sessions(*, limit: int = 50, offset: int = 0) -> dict[str, Any]:
    limit = max(1, min(int(limit), 100))
    offset = max(0, int(offset))
    rows = db_list_sessions(limit=limit, offset=offset)
    sessions = []
    for row in rows:
        sid = int(row["id"])
        sessions.append({
            "id": sid,
            "title": (row["title"] or "").strip() or f"Session #{sid}",
            "model": row["model"] or "",
            "msg_count": int(row["msg_count"] or 0),
            "created_at": float(row["created_at"] or 0),
            "updated_at": float(row["updated_at"] or 0),
            "updated_label": _fmt_ts(float(row["updated_at"] or 0)),
            "active": sid == state.current_session_id,
        })
    return {
        "sessions": sessions,
        "total": db_count_sessions(),
        "limit": limit,
        "offset": offset,
        "current_session_id": state.current_session_id,
    }


def list_models(*, query: str = "") -> dict[str, Any]:
    q = (query or "").strip().lower()
    models: list[dict[str, Any]] = []
    for src, model_id, desc in model_picker_rows():
        label = MODEL_SOURCE_LABELS.get(src, src)
        if src == PROVIDER_HARNESS_AGENT:
            label = "Harness Agent"
        if q and q not in model_id.lower() and q not in desc.lower() and q not in label.lower():
            if q not in ("harness", "agent", "free"):
                continue
        models.append({
            "id": model_option_id(src, model_id),
            "source": src,
            "source_label": label,
            "model_id": model_id,
            "description": desc,
            "active": _model_is_active(src, model_id),
        })
    return {
        "models": models,
        "current": {
            "model_id": state.MODEL,
            "provider": state.provider,
            "auth_mode": state.auth_mode,
            "harness_agent_free": state.harness_agent_free,
        },
    }


def list_agents(*, include_global: bool | None = None) -> dict[str, Any]:
    from ..storage import agents as ag

    if include_global is None:
        include_global = state.global_agents
    agents_raw = ag.discover_agents(force=True, include_global=include_global)
    active = state.active_agent_name or ""
    agents = []
    for rec in sorted(agents_raw, key=lambda r: (r.get("scope") != "project", r.get("name", ""))):
        name = rec.get("name") or ""
        agents.append({
            "name": name,
            "description": rec.get("description") or "",
            "icon": rec.get("icon") or "",
            "scope": rec.get("scope") or "project",
            "source_tag": rec.get("source_tag") or "",
            "active": name == active,
        })
    hidden = ag.global_count() if not include_global else 0
    return {
        "agents": agents,
        "active": active,
        "global_agents": include_global,
        "hidden_global_count": hidden,
        "default_off": not active,
    }


def list_skills(*, include_global: bool | None = None, query: str = "") -> dict[str, Any]:
    from ..storage import skills as sk

    if include_global is None:
        include_global = state.global_skills
    q = (query or "").strip().lower()
    skills_raw = sk.discover_skills(force=True, include_global=include_global)
    skills = []
    for rec in sorted(skills_raw, key=lambda r: (r.get("scope") != "project", r.get("name", ""))):
        name = rec.get("name") or ""
        desc = rec.get("description") or ""
        if q and q not in name.lower() and q not in desc.lower():
            continue
        skills.append({
            "name": name,
            "description": desc,
            "scope": rec.get("scope") or "project",
            "source_dir": rec.get("source_dir") or "",
        })
    hidden = sk.global_count() if not include_global else 0
    return {
        "skills": skills,
        "global_skills": include_global,
        "hidden_global_count": hidden,
    }


def get_skill(name: str) -> dict[str, Any] | None:
    from ..storage import skills as sk

    content = sk.load_skill(name)
    if content is None:
        return None
    rec = next((r for r in sk.discover_skills(force=True) if r.get("name") == name), None)
    return {
        "name": name,
        "description": (rec or {}).get("description") or "",
        "content": content,
    }


def list_mcp_servers(*, query: str = "") -> dict[str, Any]:
    from ..mcp.config import get_config
    from ..mcp.registry import mcp_registry

    q = (query or "").strip().lower()
    config = get_config()
    servers_cfg = config.list_servers()
    servers: list[dict[str, Any]] = []
    names = sorted(servers_cfg.keys())
    counts = mcp_registry.health_counts(names)

    auto_connect_names = set(config.get_auto_connect())

    for name in names:
        if q and q not in name.lower():
            continue
        cfg = servers_cfg[name] or {}
        health = mcp_registry.get_server_health(name, cfg)
        transport = cfg.get("type") or ("sse" if cfg.get("url") else "stdio")
        if transport == "stdio":
            cmd = cfg.get("command") or ""
            args = cfg.get("args") or []
            endpoint = " ".join([cmd, *args]).strip() or cmd
        else:
            endpoint = str(cfg.get("url") or "")
        servers.append({
            "name": name,
            "scope": config.get_scope(name),
            "source": config.get_source(name),
            "auto_connect": name in auto_connect_names,
            "transport": transport,
            "endpoint": endpoint,
            "health": health,
        })

    return {
        "servers": servers,
        "global_mcp": state.global_mcp,
        "counts": counts,
        "project_config_path": str(config.project_config_path() or ""),
    }


def parse_model_body(data: dict[str, Any]) -> tuple[str, str]:
    """Return (source, model_id) from POST body."""
    if data.get("option_id"):
        return parse_model_option_id(str(data["option_id"]))
    return str(data.get("source") or ""), str(data.get("model_id") or "")
