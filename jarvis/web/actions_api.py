"""Mutations for web picker actions (called on TUI main thread)."""
from __future__ import annotations

from typing import Any, Callable

from .. import state
from ..storage.sessions import db_create_session, db_delete_session


def run_web_action(action: str, data: dict[str, Any], *, console_print: Callable) -> dict[str, Any]:
    """Execute a picker mutation. Returns {ok, error?, ...}."""
    action = (action or "").strip()

    if action == "session_resume":
        sid = int(data.get("session_id") or 0)
        if sid <= 0:
            return {"ok": False, "error": "invalid session_id"}
        from ..tui.session_modal import resume_session_into_state

        if not resume_session_into_state(sid, console_print, preview=False, quiet=True):
            return {"ok": False, "error": "session not found"}
        return {"ok": True, "session_id": sid}

    if action == "session_new":
        state.messages = []
        state.tool_calls_count = 0
        state.total_in = 0
        state.total_out = 0
        state.total_tokens = 0
        state.current_session_id = db_create_session(state.MODEL)
        console_print(f"[green]▶ new session #{state.current_session_id}[/]")
        return {"ok": True, "session_id": state.current_session_id}

    if action == "session_delete":
        sid = int(data.get("session_id") or 0)
        if sid <= 0:
            return {"ok": False, "error": "invalid session_id"}
        if sid == state.current_session_id:
            return {"ok": False, "error": "cannot delete the active session"}
        if not db_delete_session(sid):
            return {"ok": False, "error": "session not found"}
        return {"ok": True, "deleted": sid}

    if action == "model_select":
        from .pickers_api import parse_model_body
        from ..commands.control import _apply_model_selection

        source, model_id = parse_model_body(data)
        if not model_id:
            return {"ok": False, "error": "model_id required"}
        _apply_model_selection(model_id, source=source)
        return {"ok": True, "model": state.MODEL, "provider": state.provider}

    if action == "agent_select":
        from ..storage import agents as ag

        name = (data.get("name") or "").strip()
        if not name or name == "__off__":
            state.set_active_agent(None)
            return {"ok": True, "active": ""}
        rec = ag.find_agent(name)
        if not rec:
            return {"ok": False, "error": f"agent '{name}' not found"}
        state.set_active_agent(rec)
        return {"ok": True, "active": name}

    if action == "agents_scope":
        from ..storage import agents as ag

        include = bool(data.get("global_agents"))
        state.global_agents = include
        state.save_agent_config()
        ag.invalidate_cache()
        return {"ok": True, "global_agents": include}

    if action == "skills_scope":
        include = bool(data.get("global_skills"))
        state.global_skills = include
        state.save_skills_config()
        from ..storage import skills as sk

        sk.invalidate_cache()
        return {"ok": True, "global_skills": include}

    if action == "mcp_connect":
        from ..mcp.config import get_config, reload_config
        from ..mcp.registry import mcp_registry

        reload_config()
        name = (data.get("name") or "").strip()
        cfg = get_config().list_servers().get(name)
        if not cfg:
            return {"ok": False, "error": f"unknown server '{name}'"}
        err = mcp_registry.connect(name, cfg)
        if err:
            return {"ok": False, "error": err}
        from ..mcp.scope import invalidate_mcp_prompt_cache

        invalidate_mcp_prompt_cache()
        return {"ok": True, "name": name}

    if action == "mcp_disconnect":
        from ..mcp.registry import mcp_registry

        name = (data.get("name") or "").strip()
        err = mcp_registry.disconnect(name)
        if err:
            return {"ok": False, "error": err}
        from ..mcp.scope import invalidate_mcp_prompt_cache

        invalidate_mcp_prompt_cache()
        return {"ok": True, "name": name}

    if action == "mcp_scope":
        from ..mcp.scope import apply_mcp_scope_change

        enable = bool(data.get("global_mcp"))
        state.global_mcp = enable
        state.save_mcp_config()
        result = apply_mcp_scope_change(connect_all=enable)
        return {"ok": True, "global_mcp": enable, **result}

    return {"ok": False, "error": f"unknown action: {action}"}
