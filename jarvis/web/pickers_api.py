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
    PROVIDER_OPENAI_CODEX,
    PROVIDER_OPENAI_CODEX_AUTH,
    PROVIDER_OPENCODE_ZEN,
    model_option_id,
    parse_model_option_id,
    provider_label,
)
from .. import state
from ..storage.sessions import (
    db_count_sessions, db_list_sessions, db_search_session_ids, db_session_folders, db_session_info,
)
from ..tui.model_modal import model_picker_rows
from ..utils.time_fmt import _fmt_ts


def _cursor_pool(source: str, model_id: str) -> str:
    """``cursor`` | ``other`` for a Cursor model, ``""`` for any other provider."""
    if source != "cursor":
        return ""
    from ..auth.cursor_agent import pool_of

    return pool_of(model_id)


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
    return state.provider == source


def _side_model_is_active(source: str, model_id: str) -> bool:
    """Active in the *commit text model* picker: the saved side choice, not the
    chat's model."""
    from .. import side_model

    return side_model.preference() == (source, model_id)


def list_sessions(*, limit: int = 50, offset: int = 0, scope: str = "all", cwd: str | None = None,
                  q: str = "", view: str = "active", sort: str = "recent",
                  folders: bool = False) -> dict[str, Any]:
    """``GET /api/sessions``.

    ``scope``: ``all`` (every folder) · ``project`` (the folder Jarvis runs in)
    · ``folder`` (``cwd``; "" = chats whose folder isn't recorded).
    ``view``: ``active`` (not archived) · ``pinned`` · ``archived``.
    ``q`` searches titles, models, ``#id`` and every message (with a snippet).
    ``folders=True`` adds the folder list (Sessions dialog rail).
    """
    from ..storage import session_library as lib
    from .. import tabs as tabs_mod

    lib.backfill_folders()
    limit = max(1, min(int(limit), 200))
    offset = max(0, int(offset))
    here = lib.here()
    folder: str | None = None
    if scope == "project":
        folder = here
    elif scope == "folder":
        folder = str(cwd or "")
    archived: bool | None = view == "archived"
    pinned = True if view == "pinned" else None
    query = (q or "").strip()
    current = state.current_session_id

    if query:
        ids = db_search_session_ids(query, cwd=folder, archived=archived, limit=offset + limit + 1)
        if pinned:
            ids = [i for i in ids if (db_session_info(i) or {}).get("pinned")]
        total = len(ids)
        page = ids[offset:offset + limit]
        rows = db_list_sessions(limit=len(page) or 1, ids=page, archived=None) if page else []
        order = {sid: n for n, sid in enumerate(page)}
        rows = sorted(rows, key=lambda r: order.get(int(r["id"]), 0))
    else:
        rows = db_list_sessions(limit=limit, offset=offset, cwd=folder, archived=archived,
                                pinned=pinned, sort=sort, pinned_first=view == "active")
        total = db_count_sessions(cwd=folder, archived=archived, pinned=pinned)

    open_tabs = {}
    try:
        for t in tabs_mod.public():
            if t.get("session_id"):
                open_tabs[int(t["session_id"])] = t
    except Exception:
        pass
    sessions = []
    for row in rows:
        item = lib.public_row(row, current_sid=current, current_cwd=here)
        tab = open_tabs.get(item["id"])
        if tab and not item["active"]:
            item["tab"] = {"id": tab["id"], "busy": tab.get("busy", False)}
        if query:
            try:
                item["snippet"] = lib.snippet(item["id"], query)
            except Exception:
                item["snippet"] = None
        sessions.append(item)

    out: dict[str, Any] = {
        "sessions": sessions,
        "total": total,
        "limit": limit,
        "offset": offset,
        "more": offset + len(sessions) < total,
        "query": query,
        "scope": scope,
        "view": view,
        "current_session_id": current,
        "current_cwd": here,
        "current_name": lib.name_of(here),
        "current_display": lib.display(here),
    }
    if folders:
        rows_f = db_session_folders()
        out["folders"] = [lib.folder_info(f, current=here) for f in rows_f]
        if here and not any(f["cwd"] == here for f in out["folders"]):
            out["folders"].insert(0, lib.folder_info({"cwd": here}, current=here))
        out["counts"] = {
            "all": sum(int(f.get("count") or 0) for f in rows_f),
            "pinned": sum(int(f.get("pinned") or 0) for f in rows_f),
            "archived": sum(int(f.get("archived") or 0) for f in rows_f),
            "project": next((int(f.get("count") or 0) for f in rows_f if f.get("cwd") == here), 0),
        }
    return out


def export_session(session_id: int) -> dict[str, Any]:
    from ..storage import session_library as lib

    text = lib.markdown(int(session_id))
    if text is None:
        return {"ok": False, "error": "session not found"}
    return {"ok": True, "name": lib.export_name(int(session_id)), "markdown": text}


def list_models(*, query: str = "", for_side: bool = False) -> dict[str, Any]:
    # Tags shared with the terminal /model picker (constants/providers.py).
    from ..constants.providers import (
        VISION_SEARCH_WORDS, free_model_ids, model_is_free, model_sees_images,
    )

    from ..constants.providers import is_local_provider, local_models_are_fresh, refresh_local_models

    if not local_models_are_fresh():
        try:  # local servers start and stop all the time: a ~20 ms look
            refresh_local_models()
        except Exception:
            pass
    q = (query or "").strip().lower()
    vision_only = q in VISION_SEARCH_WORDS
    free_only = q == "free"
    free_ids = free_model_ids()
    models: list[dict[str, Any]] = []
    for src, model_id, desc in model_picker_rows():
        label = MODEL_SOURCE_LABELS.get(src) or provider_label(src)
        if src == PROVIDER_HARNESS_AGENT:
            label = "Harness Agent"
        images = model_sees_images(model_id, src)
        free = model_is_free(model_id, src, free_ids)
        if vision_only:
            if not images:
                continue
        elif free_only:
            if not free:
                continue
        elif q and q not in model_id.lower() and q not in desc.lower() and q not in label.lower():
            if q not in ("harness", "agent"):
                continue
        models.append({
            "id": model_option_id(src, model_id),
            "source": src,
            "source_label": label,
            "model_id": model_id,
            "description": desc,
            "active": _side_model_is_active(src, model_id) if for_side else _model_is_active(src, model_id),
            "images": images,
            "free": free,
            "local": is_local_provider(src),
            # Cursor: which usage pool the model draws on ("cursor" = the plan's own models).
            "pool": _cursor_pool(src, model_id),
        })
    if for_side:
        from .. import side_model

        current = {**side_model.public(), "model_id": state.MODEL, "provider": state.provider,
                   "auth_mode": state.auth_mode, "harness_agent_free": state.harness_agent_free}
        return {"models": models, "current": current}
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
    from ..utils.origins import other_tools, tool_label

    if include_global is None:
        include_global = state.global_agents
    agents_raw = ag.discover_agents(force=True, include_global=include_global)
    active = state.active_agent_name or ""
    agents = []
    for rec in sorted(agents_raw, key=lambda r: (r.get("scope") != "project", r.get("name", ""))):
        name = rec.get("name") or ""
        tool = rec.get("tool") or "jarvis"
        agents.append({
            "name": name,
            "description": rec.get("description") or "",
            "icon": rec.get("icon") or "",
            "scope": rec.get("scope") or "project",
            "source_tag": rec.get("source_tag") or "",
            "tool": tool,                       # claude, cursor … (utils/origins.py)
            "tool_label": tool_label(tool),
            "also_labels": [tool_label(t) for t in other_tools(tool, rec.get("also") or ())],
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
    """Every installed skill (project + global) with where it lives; ``active`` says
    whether the agent can see it under the current scope. ``include_global`` is
    kept for older clients and ignored — the scope switch is ``skills_scope``."""
    from ..storage import skill_install as si

    return si.describe_installed(query)


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
    from .extensions_api import mcp_state

    return mcp_state(query)


def parse_model_body(data: dict[str, Any]) -> tuple[str, str]:
    """Return (source, model_id) from POST body."""
    if data.get("option_id"):
        return parse_model_option_id(str(data["option_id"]))
    return str(data.get("source") or ""), str(data.get("model_id") or "")
