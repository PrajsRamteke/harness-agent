"""Mutations for web picker actions (called on TUI main thread)."""
from __future__ import annotations

from typing import Any, Callable

from .. import state
from ..storage.sessions import db_create_session, db_delete_session


def _int(value) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def run_web_action(action: str, data: dict[str, Any], *, console_print: Callable) -> dict[str, Any]:
    """Execute a picker mutation. Returns {ok, error?, ...}."""
    action = (action or "").strip()

    if action.startswith("provider_"):
        from .providers_api import run_provider_action

        return run_provider_action(action, data, console_print=console_print)

    if action.startswith("local_"):
        from .local_api import run_local_action

        return run_local_action(action, data, console_print=console_print)

    if action in ("reply_retry", "reply_branch", "prompt_unsent"):
        from .branch_api import run as run_reply_action

        return run_reply_action(action, data, console_print=console_print)

    if action == "pin":
        from .pin_api import apply

        return apply(data)

    if action == "settings_set":
        from .settings_api import apply as apply_setting

        return apply_setting(data)

    if action == "queue":
        from ..prompt_queue import apply_op

        return apply_op(data)

    if action == "cwd_change":
        # "Move this chat here" (folder picker): the /cd command, done fully.
        from .fs_api import change_cwd

        result = change_cwd(str(data.get("path") or ""))
        if result.get("ok") and not result.get("unchanged"):
            _move_current_session(result.get("cwd") or "")
            console_print(f"[green]📁 moved to {result['display']}[/] [dim](from the web)[/]")
        return result

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
        state.claude_cost_usd = 0.0
        state.current_session_id = db_create_session(state.MODEL)
        console_print(f"[green]▶ new session #{state.current_session_id}[/]")
        return {"ok": True, "session_id": state.current_session_id}

    if action == "session_delete":
        many = data.get("session_ids")
        if isinstance(many, list):  # bulk (Sessions dialog): skip what's open, delete the rest
            from .. import tabs as tabs_mod

            deleted, skipped = [], []
            for raw in many[:500]:
                try:
                    sid = int(raw)
                except (TypeError, ValueError):
                    continue
                if sid == state.current_session_id or tabs_mod.find_session(sid) is not None:
                    skipped.append(sid)
                elif db_delete_session(sid):
                    deleted.append(sid)
            return {"ok": True, "deleted": deleted, "skipped": skipped}
        sid = int(data.get("session_id") or 0)
        if sid <= 0:
            return {"ok": False, "error": "invalid session_id"}
        if sid == state.current_session_id:
            return {"ok": False, "error": "cannot delete the active session"}
        if not db_delete_session(sid):
            return {"ok": False, "error": "session not found"}
        return {"ok": True, "deleted": sid}

    if action in ("session_pin", "session_archive"):
        from ..storage.sessions import db_set_archived, db_set_pinned

        ids = data.get("session_ids")
        if not isinstance(ids, list):
            ids = [data.get("session_id")]
        on = bool(data.get("pinned" if action == "session_pin" else "archived", True))
        if action == "session_archive" and on and state.current_session_id in [_int(x) for x in ids]:
            return {"ok": False, "error": "This chat is open. Start or open another one first, then archive it."}
        fn = db_set_pinned if action == "session_pin" else db_set_archived
        n = fn(ids, on)
        if not n:
            return {"ok": False, "error": "session not found"}
        return {"ok": True, "changed": n}

    if action == "session_move":
        # "Move to this project": the chat now belongs to the folder Jarvis runs in.
        from ..storage.sessions import db_set_session_cwd
        from ..storage.session_library import display, here

        sid = _int(data.get("session_id"))
        folder = here()
        if not sid or not db_set_session_cwd(sid, folder):
            return {"ok": False, "error": "session not found"}
        return {"ok": True, "session_id": sid, "cwd": folder, "display": display(folder)}

    if action == "session_rename":
        sid = int(data.get("session_id") or 0) or None  # none = the session on screen
        title = str(data.get("title") or "")
        from ..commands.session import rename_session
        from ..storage.sessions import db_get_title

        ok, msg = rename_session(sid, title)
        if not ok:
            return {"ok": False, "error": msg[:1].upper() + msg[1:]}
        sid = sid or state.current_session_id
        return {"ok": True, "session_id": sid, "title": db_get_title(sid)}

    if action == "model_select":
        from .pickers_api import parse_model_body
        from ..commands.control import _apply_model_selection

        source, model_id = parse_model_body(data)
        if not model_id:
            return {"ok": False, "error": "model_id required"}
        _apply_model_selection(model_id, source=source)
        return {"ok": True, "model": state.MODEL, "provider": state.provider}

    if action == "side_model_select":
        from .pickers_api import parse_model_body
        from .. import side_model

        source, model_id = parse_model_body(data)
        if not source and not model_id:
            side_model.set_preference("", "")
        elif not source or not model_id:
            return {"ok": False, "error": "source and model_id required"}
        else:
            side_model.set_preference(source, model_id)
        return {"ok": True, "side_model": side_model.public()}

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

    if action == "commands_scope":
        include = bool(data.get("global_commands"))
        state.global_commands = include
        state.save_commands_config()
        from ..storage import commands as cc

        cc.invalidate_cache()
        return {"ok": True, "global_commands": include}

    if action == "mcp_connect":
        from ..mcp.config import get_config, reload_config
        from ..mcp.registry import mcp_registry

        reload_config()
        name = (data.get("name") or "").strip()
        cfg = get_config().list_servers().get(name)
        if not cfg:
            return {"ok": False, "error": f"unknown server '{name}'"}
        from ..mcp.registry import needs_auth

        err = mcp_registry.connect(name, cfg)
        if err:
            return {"ok": False, "error": err, "auth_required": needs_auth(err)}
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


def _move_current_session(cwd: str) -> None:
    """The chat on screen moved with Jarvis to ``cwd`` (``/cd``, "Move this chat here")."""
    if not cwd or not state.current_session_id:
        return
    try:
        from ..storage.sessions import db_set_session_cwd

        db_set_session_cwd(state.current_session_id, cwd)
    except Exception:
        pass
