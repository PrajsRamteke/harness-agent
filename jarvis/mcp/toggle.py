"""Turning MCP on and off — one server, or MCP as a whole.

A server that is off stays in its config file (Jarvis's, the project's
``.mcp.json`` or another tool's — those are never rewritten) but never
connects, offers no tools and is listed to the model as switched off. With
MCP off as a whole, no server connects at all.

The choice lives in Jarvis's ``settings.json`` (``mcp.enabled`` and
``mcp.disabled: [names]``), so it works for servers from every source and
survives restarts. This module only reads and writes that choice; connecting
and disconnecting is ``install.set_server_enabled`` / ``install.set_mcp_enabled``.
"""
from __future__ import annotations


def _settings():
    from ..storage.settings import get_settings

    return get_settings()


def mcp_enabled() -> bool:
    """MCP as a whole (``/mcp on`` · ``/mcp off``)."""
    try:
        return _settings().get("mcp.enabled", True) is not False
    except Exception:
        return True


def disabled_servers() -> set[str]:
    """Servers the user switched off, whatever MCP as a whole is set to."""
    try:
        raw = _settings().get("mcp.disabled", [])
    except Exception:
        return set()
    return {str(n) for n in raw} if isinstance(raw, list) else set()


def server_enabled(name: str) -> bool:
    """The server's own switch (ignores the master switch)."""
    return name not in disabled_servers()


def is_enabled(name: str) -> bool:
    """May ``name`` connect right now — MCP on and the server's switch on."""
    return mcp_enabled() and server_enabled(name)


def off_reason(name: str) -> str | None:
    """Why ``name`` can't connect, in words for the user — ``None`` when it can."""
    if not mcp_enabled():
        return "MCP is turned off — turn it on in /mcp (or /mcp on)"
    if not server_enabled(name):
        return f"'{name}' is turned off — turn it on in /mcp (or /mcp enable {name})"
    return None


def set_server_enabled(name: str, on: bool) -> None:
    names = disabled_servers()
    if on:
        names.discard(name)
    else:
        names.add(name)
    _settings().set("mcp.disabled", sorted(names))


def set_mcp_enabled(on: bool) -> None:
    _settings().set("mcp.enabled", bool(on))


def forget(name: str) -> None:
    """A removed server leaves no switch behind (a new one with that name starts on)."""
    if name in disabled_servers():
        set_server_enabled(name, True)
