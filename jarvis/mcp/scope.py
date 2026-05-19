"""Reconcile MCP config, connections, and system prompt after scope changes."""
from __future__ import annotations

from .config import reload_config
from .registry import mcp_registry


def apply_mcp_scope_change(*, connect_all: bool = False) -> dict:
    """Reload MCP config for the current ``state.global_mcp`` flag.

    Disconnects servers that fall outside the new scope. Optionally connects
    every visible server (``connect_all=True``, used when global scope turns on)
    or only ``auto_connect`` entries (default).

    Returns a short status dict: ``visible``, ``connected``, ``failed``.
    """
    from ..repl.system import invalidate_system_cache

    config = reload_config()
    visible = set(config.list_servers().keys())

    for srv_name, _tools, _err in mcp_registry.list_connected():
        if srv_name not in visible:
            mcp_registry.disconnect(srv_name)

    to_connect = sorted(visible) if connect_all else config.get_auto_connect()
    connected: list[str] = []
    failed: list[tuple[str, str]] = []
    for name in to_connect:
        if name not in visible or mcp_registry.is_connected(name):
            continue
        cfg = config.get_server(name)
        if cfg is None:
            continue
        err = mcp_registry.connect(name, cfg)
        if err:
            failed.append((name, err))
        else:
            connected.append(name)

    invalidate_system_cache()
    return {
        "visible": sorted(visible),
        "connected": connected,
        "failed": failed,
    }


def invalidate_mcp_prompt_cache() -> None:
    """Clear the system-prompt cache after a connect/disconnect in the MCP modal."""
    from ..repl.system import invalidate_system_cache

    invalidate_system_cache()
