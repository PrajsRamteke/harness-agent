"""MCP server registry — manages connections, tool registration, call routing.

Bridges MCP's async Python SDK to Jarvis's synchronous tool execution via a
dedicated background thread with a persistent asyncio event loop.
"""

from __future__ import annotations

import asyncio
import concurrent.futures
import contextlib
import logging
import os
import pathlib
import re
import shutil
import sys
import threading
import time
from collections.abc import Iterable
from dataclasses import dataclass, field
from typing import Any, Callable, Literal, TextIO

from mcp import Tool
from mcp.client.session import ClientSession
from mcp.client.sse import sse_client
from mcp.client.stdio import get_default_environment, stdio_client, StdioServerParameters
from mcp.client.streamable_http import streamablehttp_client

from . import secrets as mcp_secrets
from .auth import AuthCancelled, AuthNeeded, FLOW_TTL, build_provider, coordinator as auth_coordinator, forget_login

logger = logging.getLogger("jarvis.mcp")

# ── tool naming ──────────────────────────────────────────────────────────
_MCP_TOOL_PREFIX = "mcp__"
_MCP_TOOL_RE = re.compile(r"^mcp__(.+)__(.+)$")


def encode_tool_name(server_name: str, tool_name: str) -> str:
    """Create namespaced Jarvis tool name from MCP server + tool name."""
    return f"{_MCP_TOOL_PREFIX}{server_name}__{tool_name}"


def decode_tool_name(jarvis_tool_name: str) -> tuple[str, str] | None:
    """Extract (server_name, tool_name) from namespaced Jarvis tool name."""
    m = _MCP_TOOL_RE.match(jarvis_tool_name)
    if m:
        return m.group(1), m.group(2)
    return None


def is_mcp_tool(jarvis_tool_name: str) -> bool:
    """Check if a Jarvis tool name is an MCP-backed tool."""
    return jarvis_tool_name.startswith(_MCP_TOOL_PREFIX)


def _mcp_stderr_to_terminal() -> bool:
    """When True, MCP stdio server stderr is inherited (can corrupt the TUI)."""
    return os.getenv("HARNESS_MCP_STDERR", "").strip().lower() in ("1", "true", "yes")


def _open_stdio_errlog(server_name: str) -> TextIO:
    """Sink for MCP server stderr — devnull by default to keep the TUI clean."""
    if _mcp_stderr_to_terminal():
        return sys.stderr
    log_flag = os.getenv("HARNESS_MCP_STDERR_LOG", "").strip().lower()
    if log_flag in ("1", "true", "yes"):
        log_dir = pathlib.Path.home() / ".config" / "harness-agent" / "mcp-logs"
        log_dir.mkdir(parents=True, exist_ok=True)
        safe = re.sub(r"[^\w.-]+", "_", server_name) or "server"
        return open(log_dir / f"{safe}.log", "a", encoding="utf-8")
    return open(os.devnull, "w")


def _stdio_env(config: dict[str, Any]) -> dict[str, str]:
    """Build subprocess env: MCP defaults + per-server overrides."""
    env = get_default_environment()
    user_env = config.get("env")
    if isinstance(user_env, dict):
        env.update({str(k): str(v) for k, v in user_env.items()})
    return env


_MCP_SLOW_TOOL_MS = 5_000


@dataclass
class MCPHealthRecord:
    """Persistent health metadata for one MCP server (survives disconnect)."""

    last_connect_error: str | None = None
    last_connect_at: float | None = None
    last_disconnect_reason: str | None = None
    last_tool_error: str | None = None
    last_tool_at: float | None = None
    last_tool_ms: float | None = None
    last_tool_name: str | None = None
    tool_calls_ok: int = 0
    tool_calls_err: int = 0


MCPHealthStatus = Literal["connecting", "live", "idle", "failed", "warn", "auth"]


def _preflight_hints(name: str, config: dict[str, Any]) -> list[str]:
    """Surface likely setup problems before the user connects."""
    hints: list[str] = []
    transport = config.get("type", "stdio")
    if transport == "stdio":
        cmd = str(mcp_secrets.expand(config.get("command", ""))).strip()
        if cmd and shutil.which(cmd) is None:
            hints.append(f"command not found: {cmd}")
    # ``${VAR}`` references (env, args, headers, url) that nothing defines yet.
    for ref in mcp_secrets.missing(config):
        hints.append(f"missing env: {ref}")
    env_cfg = config.get("env")
    if isinstance(env_cfg, dict):
        for key, raw in env_cfg.items():
            if "${" not in str(raw) and not str(raw).strip():
                hints.append(f"missing value: {key}")
    if not hints and transport in ("sse", "http") and not config.get("url"):
        hints.append("missing url for remote server")
    return hints


# ── server state ─────────────────────────────────────────────────────────

@dataclass
class MCPServerState:
    """Holds the active connection state for one MCP server."""

    config: dict[str, Any]
    session: ClientSession | None = None
    tools: list[Tool] = field(default_factory=list)
    connected: bool = False
    error: str | None = None
    _keep_alive_task: asyncio.Task | None = None
    _stderr_sink: TextIO | None = None
    _cleanup_fns: list[Callable[[], None]] = field(default_factory=list)
    transport: str = ""                     # what actually connected: stdio | http | sse
    _remote_future: Any = None              # concurrent Future of the remote lifetime task

    def add_cleanup(self, fn: Callable[[], None]) -> None:
        self._cleanup_fns.append(fn)

    def cleanup(self) -> None:
        if self._keep_alive_task and not self._keep_alive_task.done():
            self._keep_alive_task.cancel()
        for fn in self._cleanup_fns:
            try:
                fn()
            except Exception:
                pass
        self._cleanup_fns.clear()
        if self._stderr_sink is not None and self._stderr_sink not in (sys.stderr, sys.stdout):
            try:
                self._stderr_sink.close()
            except Exception:
                pass
            self._stderr_sink = None


# ── event loop bridge ────────────────────────────────────────────────────

class _MCPEventLoop:
    """Dedicated asyncio event loop in a daemon thread for MCP operations."""

    def __init__(self) -> None:
        self.loop = asyncio.new_event_loop()
        self.thread = threading.Thread(target=self._run, daemon=True, name="mcp-event-loop")
        self.thread.start()

    def _run(self) -> None:
        asyncio.set_event_loop(self.loop)
        self.loop.run_forever()

    def run_coro(self, coro, timeout: float = 60) -> Any:
        """Schedule a coroutine on the event loop and return result synchronously."""
        future = asyncio.run_coroutine_threadsafe(coro, self.loop)
        return future.result(timeout=timeout)

    def run_and_forget(self, coro) -> asyncio.Task:
        """Schedule a fire-and-forget coroutine (returns the Task)."""
        return asyncio.run_coroutine_threadsafe(coro, self.loop)

    def stop(self) -> None:
        self.loop.call_soon_threadsafe(self.loop.stop)
        self.thread.join(timeout=5)


_loop: _MCPEventLoop | None = None
_lock = threading.Lock()


def _get_loop() -> _MCPEventLoop:
    global _loop
    if _loop is None:
        with _lock:
            if _loop is None:
                _loop = _MCPEventLoop()
    return _loop


# ── tool schema helpers ──────────────────────────────────────────────────

def _mcp_tool_to_schema(tool: Tool) -> dict[str, Any]:
    """Convert an MCP Tool definition to a Jarvis-compatible tool schema dict."""
    return {
        "name": "",  # filled by caller with encoded name
        "description": tool.description or "",
        "input_schema": _normalize_input_schema(tool.inputSchema),
    }


# Schema sanitisation lives in jarvis.utils.schema so the API-boundary
# layer (tools.router) and this registration path use the same logic.
from ..utils.schema import normalize_input_schema as _normalize_input_schema


# ── remote transports & errors ───────────────────────────────────────────

AUTH_REQUIRED_MSG = "Sign-in required — click Authenticate (or run /mcp auth <name>) to finish connecting"
_REMOTE_CONNECT_TIMEOUT = 30.0
_MISMATCH_STATUSES = (400, 404, 405, 406, 415)


def needs_auth(err: str | None) -> bool:
    """True when ``connect`` returned "sign in first" rather than a failure."""
    return bool(err) and str(err).startswith(AUTH_REQUIRED_MSG[:24])


class _AuthMode:
    """Whether a sign-in may start now. Startup connects say no; once a server
    is live a later token expiry may prompt the user."""

    def __init__(self, interactive: bool) -> None:
        self.interactive = interactive

    def __call__(self) -> bool:
        return self.interactive


def _leaf_exceptions(exc: BaseException) -> list[BaseException]:
    """Flatten anyio / asyncio exception groups into the real errors."""
    kids = getattr(exc, "exceptions", None)
    if kids:
        out: list[BaseException] = []
        for k in kids:
            out.extend(_leaf_exceptions(k))
        return out
    return [exc]


def _status_of(exc: BaseException) -> int | None:
    resp = getattr(exc, "response", None)
    code = getattr(resp, "status_code", None)
    return code if isinstance(code, int) else None


def _is_transport_mismatch(leaves: list[BaseException]) -> bool:
    """The URL answered, but not in this transport's language (POST to an SSE
    endpoint, GET to a streamable one) — worth trying the other one."""
    for e in leaves:
        if _status_of(e) in _MISMATCH_STATUSES:
            return True
        if "session terminated" in str(e).lower():
            return True
    return False


def _has_static_auth(config: dict[str, Any]) -> bool:
    headers = config.get("headers")
    return isinstance(headers, dict) and any(str(k).lower() == "authorization" for k in headers)


def _wants_oauth(config: dict[str, Any]) -> bool:
    """Attach browser sign-in unless the entry brings its own credentials or opts out."""
    if config.get("oauth") is False or config.get("auth") in (False, "none"):
        return False
    return not _has_static_auth(config)


def _remote_transports(config: dict[str, Any]) -> list[str]:
    declared = str(config.get("type") or "http").lower()
    return ["sse", "http"] if declared == "sse" else ["http", "sse"]


def _host_of(config: dict[str, Any]) -> str:
    import urllib.parse

    return urllib.parse.urlparse(str(config.get("url") or "")).netloc or str(config.get("url") or "server")


def _describe_remote_error(leaves: list[BaseException], config: dict[str, Any]) -> tuple[str, str]:
    """``(kind, message)`` — kind is ``auth`` | ``cancelled`` | ``error``."""
    import httpx
    from mcp.client.auth import OAuthFlowError, OAuthRegistrationError, OAuthTokenError

    host = _host_of(config)
    for e in leaves:
        if isinstance(e, AuthNeeded):
            return "auth", AUTH_REQUIRED_MSG
    for e in leaves:
        if isinstance(e, AuthCancelled):
            return "cancelled", str(e) or "Sign-in cancelled"
    for e in leaves:
        if isinstance(e, OAuthRegistrationError):
            return "error", (
                f"{host} needs an API key or token — it doesn't offer browser sign-in. "
                "Add one with mcp_add headers (Authorization: Bearer …) or in the MCP dialog."
            )
        if isinstance(e, (OAuthFlowError, OAuthTokenError)):
            return "error", f"Sign-in with {host} failed: {e}"
    for e in leaves:
        code = _status_of(e)
        if code in (401, 403):
            if _has_static_auth(config):
                return "error", f"{host} rejected the token ({code}). Check the token / its permissions."
            return "error", f"{host} refused access ({code}). It may need an API key or token."
        if code == 404:
            return "error", f"{host} answered 404 — check the URL."
        if code:
            return "error", f"{host} answered HTTP {code}."
    for e in leaves:
        if isinstance(e, (httpx.ConnectError, httpx.ConnectTimeout)):
            return "error", f"Couldn't reach {host}. Check the address and your connection."
        if isinstance(e, (httpx.ReadTimeout, httpx.WriteTimeout, httpx.PoolTimeout, TimeoutError, asyncio.TimeoutError)):
            return "error", f"{host} took too long to answer."
    first = leaves[0] if leaves else Exception("unknown error")
    text = str(first).strip() or type(first).__name__
    return "error", f"{type(first).__name__}: {text}"


async def _list_all_tools(session: ClientSession) -> list[Tool]:
    tools: list[Tool] = []
    cursor: str | None = None
    for _ in range(20):
        result = await session.list_tools(cursor) if cursor else await session.list_tools()
        tools.extend(getattr(result, "tools", []) or [])
        cursor = getattr(result, "nextCursor", None)
        if not cursor:
            break
    return tools


# ── registry singleton ────────────────────────────────────────────────────

class MCPRegistry:
    """Central registry of all active MCP server connections and their tools."""

    def __init__(self) -> None:
        self._servers: dict[str, MCPServerState] = {}
        self._health: dict[str, MCPHealthRecord] = {}
        self._connecting: set[str] = set()  # connect() in flight
        self._pending: dict[str, MCPServerState] = {}  # remote servers still signing in / connecting
        self._needs_auth: set[str] = set()  # a quiet connect found the server wants a sign-in
        self._listeners: list[Callable[[str, str], None]] = []
        self._tls = threading.local()
        self._lock = threading.RLock()

        # References to the Jarvis tool system — set via init_jarvis()
        self._func_dict: dict | None = None
        self._tool_groups: dict | None = None
        self._tools_list: list | None = None
        self._tool_name_to_group: dict | None = None
        self._console_print: Callable | None = None

        auth_coordinator.add_listener(self._on_auth_event)

    # ── change notifications (sidebar, dialogs, web) ─────────────────────

    def add_listener(self, fn: Callable[[str, str], None]) -> None:
        """``fn(event, server_name)`` on connect / disconnect / failure / sign-in
        changes. Called from whichever thread caused it — hop to your own."""
        with self._lock:
            if fn not in self._listeners:
                self._listeners.append(fn)

    def remove_listener(self, fn: Callable[[str, str], None]) -> None:
        with self._lock:
            if fn in self._listeners:
                self._listeners.remove(fn)

    def _emit(self, event: str, server_name: str) -> None:
        with self._lock:
            listeners = list(self._listeners)
        for fn in listeners:
            try:
                fn(event, server_name)
            except Exception:
                logger.debug("mcp listener failed", exc_info=True)

    def _on_auth_event(self, kind: str, data: dict[str, Any]) -> None:
        name = str(data.get("name") or "")
        if kind == "auth_done":
            self._needs_auth.discard(name)
        self._emit(kind, name)

    @contextlib.contextmanager
    def startup_connect(self):
        """Connects made inside never start a browser sign-in — a server that
        needs one just reads "sign-in needed" until the user clicks the button."""
        prev = getattr(self._tls, "quiet", False)
        self._tls.quiet = True
        try:
            yield
        finally:
            self._tls.quiet = prev

    # ── integration with Jarvis tool infra ───────────────────────────────

    def init_jarvis(
        self,
        func_dict: dict,
        tool_groups: dict,
        tools_list: list,
        tool_name_to_group: dict | None = None,
        console_print: Callable | None = None,
    ) -> None:
        """Wire the registry into Jarvis's global tool dictionaries."""
        self._func_dict = func_dict
        self._tool_groups = tool_groups
        self._tools_list = tools_list
        self._tool_name_to_group = tool_name_to_group
        self._console_print = console_print

    def _log(self, msg: str, level: str = "info") -> None:
        getattr(logger, level, logger.info)(msg)
        if self._console_print:
            color = {"info": "dim", "warn": "yellow", "error": "red"}.get(level, "dim")
            self._console_print(f"[{color}]mcp: {msg}[/]")

    def _health_record(self, server_name: str) -> MCPHealthRecord:
        rec = self._health.get(server_name)
        if rec is None:
            rec = MCPHealthRecord()
            self._health[server_name] = rec
        return rec

    def _record_connect_error(self, server_name: str, error: str) -> None:
        with self._lock:
            rec = self._health_record(server_name)
            rec.last_connect_error = error
            rec.last_disconnect_reason = None

    def _record_connect_ok(self, server_name: str) -> None:
        with self._lock:
            rec = self._health_record(server_name)
            rec.last_connect_error = None
            rec.last_connect_at = time.monotonic()
            rec.last_disconnect_reason = None

    def _record_disconnect(self, server_name: str, reason: str | None = None) -> None:
        with self._lock:
            rec = self._health_record(server_name)
            if reason:
                rec.last_disconnect_reason = reason

    def _record_runtime_error(self, server_name: str, error: str) -> None:
        with self._lock:
            rec = self._health_record(server_name)
            rec.last_disconnect_reason = error

    def set_connecting(self, names: Iterable[str], on: bool) -> None:
        """Mark servers as queued/in-flight so health reads "connecting"."""
        with self._lock:
            if on:
                self._connecting.update(names)
            else:
                self._connecting.difference_update(names)

    def get_server_health(
        self,
        server_name: str,
        config: dict[str, Any] | None = None,
        *,
        connecting: bool = False,
    ) -> dict[str, Any]:
        """Return UI-friendly health for one server."""
        with self._lock:
            rec = self._health_record(server_name)
            connected = (
                server_name in self._servers
                and self._servers[server_name].connected
            )
            tool_count = (
                len(self._servers[server_name].tools)
                if connected
                else 0
            )
            last_connect_error = rec.last_connect_error
            last_tool_error = rec.last_tool_error
            last_tool_ms = rec.last_tool_ms
            last_tool_name = rec.last_tool_name
            last_tool_at = rec.last_tool_at
            last_disconnect = rec.last_disconnect_reason
            tool_calls_err = rec.tool_calls_err
            connecting = connecting or (
                server_name in self._connecting and not connected
            )
            needs_auth_flag = server_name in self._needs_auth

        hints = _preflight_hints(server_name, config) if config else []
        auth_req = None if connected else auth_coordinator.get(server_name)
        signing_in = auth_req is not None and auth_req.status in ("pending", "working")

        if signing_in or (needs_auth_flag and not connected):
            status: MCPHealthStatus = "auth"
        elif connecting:
            status = "connecting"
        elif connected:
            if last_tool_error and tool_calls_err > 0:
                status = "warn"
            elif last_tool_ms is not None and last_tool_ms >= _MCP_SLOW_TOOL_MS:
                status = "warn"
            else:
                status = "live"
        elif last_connect_error:
            status = "failed"
        elif hints:
            status = "warn"
        else:
            status = "idle"

        detail_parts: list[str] = []
        if status == "auth":
            detail_parts.append(
                (auth_req.message if signing_in and auth_req.message else "")
                or "Sign in to finish connecting"
            )
        elif last_connect_error:
            detail_parts.append(f"connect failed: {last_connect_error}")
        if last_disconnect and not connected:
            detail_parts.append(f"disconnected: {last_disconnect}")
        if last_tool_error:
            detail_parts.append(f"last tool error: {last_tool_error}")
        elif last_tool_name and last_tool_ms is not None and connected:
            detail_parts.append(
                f"last tool {last_tool_name} ({last_tool_ms:.0f}ms)"
            )
        if hints:
            detail_parts.append(" · ".join(hints))

        summary = {
            "live": f"live · {tool_count} tools",
            "idle": "idle · not connected",
            "connecting": "connecting…",
            "failed": "connect failed",
            "warn": "needs attention",
            "auth": "sign-in needed",
        }[status]

        return {
            "status": status,
            "summary": summary,
            "detail": " · ".join(detail_parts) if detail_parts else "",
            "hints": hints,
            "connected": connected,
            "tool_count": tool_count,
            "last_connect_error": last_connect_error,
            "auth": auth_req.public() if signing_in else None,
            "needs_auth": status == "auth",
            "needs_credentials": mcp_secrets.missing(config) if config else [],
        }

    def health_counts(
        self,
        names: Iterable[str],
        *,
        connecting: set[str] | None = None,
    ) -> dict[str, int]:
        """Aggregate health states for a list of configured server names."""
        connecting = connecting or set()
        counts = {"live": 0, "idle": 0, "failed": 0, "warn": 0, "connecting": 0, "auth": 0}
        config = None
        try:
            from .config import get_config
            config = get_config()
        except Exception:
            pass
        for name in names:
            if name in connecting:
                counts["connecting"] += 1
                continue
            cfg = config.get_server(name) if config else None
            h = self.get_server_health(name, cfg)
            counts[h["status"]] = counts.get(h["status"], 0) + 1
        return counts

    # ── tool schema management ───────────────────────────────────────────

    def get_tool_schemas(self) -> list[dict[str, Any]]:
        """Return tool schemas for all currently connected MCP servers."""
        schemas = []
        with self._lock:
            for server_name, state in self._servers.items():
                if not state.connected or state.session is None:
                    continue
                for tool in state.tools:
                    schema = _mcp_tool_to_schema(tool)
                    schema["name"] = encode_tool_name(server_name, tool.name)
                    schemas.append(schema)
        return schemas

    def _register_tools(self, server_name: str, tools: list[Tool]) -> None:
        """Register MCP tools into the Jarvis tool system (FUNC + TOOL_NAME_TO_GROUP)."""
        if self._func_dict is not None:
            for tool in tools:
                jarvis_name = encode_tool_name(server_name, tool.name)

                # Closure captures server/tool names for this handler
                def _make_handler(srv: str, t: Tool) -> Callable:
                    def handler(**kwargs: Any) -> str:
                        return self._call_mcp_tool(srv, t.name, kwargs)
                    handler.__name__ = encode_tool_name(srv, t.name)
                    handler.__qualname__ = f"MCP.{srv}.{t.name}"
                    return handler

                self._func_dict[jarvis_name] = _make_handler(server_name, tool)

        # Also update TOOL_NAME_TO_GROUP if accessible
        self._rebuild_tool_group()

    def _unregister_tools(self, server_name: str, tools: list[Tool]) -> None:
        """Remove MCP tools from the Jarvis tool system."""
        if self._func_dict is not None:
            for tool in tools:
                jarvis_name = encode_tool_name(server_name, tool.name)
                self._func_dict.pop(jarvis_name, None)
        self._rebuild_tool_group()

    def _rebuild_tool_group(self) -> None:
        """Rebuild the 'mcp' group in TOOL_GROUPS and update TOOL_NAME_TO_GROUP."""
        schemas = self.get_tool_schemas()
        if self._tool_groups is not None:
            old = self._tool_groups.get("mcp", [])
            old.clear()
            old.extend(schemas)

        # Update TOOL_NAME_TO_GROUP — remove old mcp entries, add current ones
        if self._tool_name_to_group is not None:
            # Remove any existing mcp entries
            to_del = [k for k, v in self._tool_name_to_group.items() if v == "mcp"]
            for k in to_del:
                del self._tool_name_to_group[k]
            # Add current ones
            for s in schemas:
                self._tool_name_to_group[s["name"]] = "mcp"

    # ── connection management ───────────────────────────────────────────

    def connect(
        self,
        server_name: str,
        config: dict[str, Any],
        *,
        interactive: bool | None = None,
    ) -> str | None:
        """Connect to an MCP server and register its tools.

        Returns error message on failure, None on success. A remote server that
        needs a browser sign-in returns ``AUTH_REQUIRED_MSG`` (see ``needs_auth``)
        while the sign-in waits in the background — the UI shows its button, and
        the tools register on their own once the user is through.

        ``interactive=None`` starts a sign-in unless this thread is inside
        ``startup_connect()``.
        """
        if interactive is None:
            interactive = not getattr(self._tls, "quiet", False)
        config = mcp_secrets.expand(config)
        transport_type = config.get("type", "stdio")
        with self._lock:
            if server_name in self._servers and self._servers[server_name].connected:
                return f"Server '{server_name}' is already connected"
            if server_name in self._pending:
                if auth_coordinator.is_pending(server_name):
                    return AUTH_REQUIRED_MSG
                return f"Server '{server_name}' is already connecting"

        if transport_type in ("http", "sse"):
            return self._connect_remote(server_name, config, interactive)

        with self._lock:
            state = MCPServerState(config=config)
            self._servers[server_name] = state
            self._connecting.add(server_name)
        try:
            return self._connect(server_name, state, config)
        finally:
            with self._lock:
                self._connecting.discard(server_name)
            self._emit("connected" if self.is_connected(server_name) else "failed", server_name)

    # ── remote (http / sse) ──────────────────────────────────────────────
    #
    # One task owns the whole lifetime of a remote connection (the SDK's
    # transports use cancel scopes that must be entered and left by the same
    # task). ``connect`` starts it and waits only until the server is live, a
    # sign-in is pending, or it failed — the task carries on by itself after.

    def _connect_remote(self, server_name: str, config: dict[str, Any], interactive: bool) -> str | None:
        if not config.get("url"):
            err = "missing url for remote server"
            self._record_connect_error(server_name, err)
            return err
        loop = _get_loop()
        ready: concurrent.futures.Future = concurrent.futures.Future()
        state = MCPServerState(config=config)
        with self._lock:
            self._pending[server_name] = state
            self._connecting.add(server_name)
            self._needs_auth.discard(server_name)
        state._remote_future = loop.run_and_forget(
            self._run_remote(server_name, state, config, ready, interactive)
        )
        self._emit("connecting", server_name)
        deadline = time.monotonic() + _REMOTE_CONNECT_TIMEOUT
        try:
            while True:
                try:
                    return ready.result(timeout=0.2)
                except concurrent.futures.TimeoutError:
                    pass
                if auth_coordinator.is_pending(server_name):
                    return AUTH_REQUIRED_MSG
                if time.monotonic() > deadline:
                    err = f"timed out connecting to {_host_of(config)} ({int(_REMOTE_CONNECT_TIMEOUT)}s)"
                    self._abort_remote(server_name, state)
                    self._record_connect_error(server_name, err)
                    self._log(f"failed to connect '{server_name}': {err}", "error")
                    self._emit("failed", server_name)
                    return err
        finally:
            with self._lock:
                self._connecting.discard(server_name)

    def _abort_remote(self, server_name: str, state: MCPServerState) -> None:
        with self._lock:
            if self._pending.get(server_name) is state:
                del self._pending[server_name]
        fut = state._remote_future
        if fut is not None and not fut.done():
            fut.cancel()
        auth_coordinator.cancel(server_name)

    async def _run_remote(
        self,
        server_name: str,
        state: MCPServerState,
        config: dict[str, Any],
        ready: "concurrent.futures.Future[str | None]",
        interactive: bool,
    ) -> None:
        mode = _AuthMode(interactive)
        transports = _remote_transports(config)
        error: str | None = None
        kind = "error"
        try:
            for idx, transport in enumerate(transports):
                try:
                    await self._serve_remote(server_name, state, config, transport, ready, mode)
                    break
                except (asyncio.CancelledError, KeyboardInterrupt, SystemExit):
                    raise
                except BaseException as exc:  # noqa: BLE001 — groups, anyio, httpx…
                    leaves = _leaf_exceptions(exc)
                    if (
                        idx + 1 < len(transports)
                        and not state.connected
                        and _is_transport_mismatch(leaves)
                        and not auth_coordinator.is_pending(server_name)
                    ):
                        continue
                    kind, error = _describe_remote_error(leaves, config)
                    logger.debug("remote mcp %s failed", server_name, exc_info=True)
                    break
        except asyncio.CancelledError:
            error = None
            kind = "cancelled"
        finally:
            with self._lock:
                if self._pending.get(server_name) is state:
                    del self._pending[server_name]
                registered = self._servers.get(server_name) is state
                if registered:
                    del self._servers[server_name]
                tools = list(state.tools)
                state.connected = False
                state.session = None
            if registered or tools:
                self._unregister_tools(server_name, tools)

            if kind == "auth":
                with self._lock:
                    self._needs_auth.add(server_name)
                if not ready.done():
                    ready.set_result(AUTH_REQUIRED_MSG)
                self._emit("auth", server_name)
            elif error:
                self._record_connect_error(server_name, error)
                auth_coordinator.finish(server_name, False, error)
                self._log(f"failed to connect '{server_name}': {error}", "error")
                if not ready.done():
                    ready.set_result(error)
                self._emit("failed", server_name)
            else:
                if registered:
                    self._record_disconnect(server_name)
                auth_coordinator.finish(server_name, False, "Sign-in cancelled")
                if not ready.done():
                    ready.set_result("cancelled")
                self._emit("disconnected", server_name)
            self._invalidate_prompt()

    async def _serve_remote(
        self,
        server_name: str,
        state: MCPServerState,
        config: dict[str, Any],
        transport: str,
        ready: "concurrent.futures.Future[str | None]",
        mode: _AuthMode,
    ) -> None:
        url = str(config["url"])
        headers = dict(config.get("headers") or {}) or None
        auth = None
        if _wants_oauth(config):
            auth, _storage = build_provider(server_name, url, interactive=mode)
        if transport == "sse":
            cm = sse_client(url, headers=headers, auth=auth)
        else:
            cm = streamablehttp_client(url, headers=headers, auth=auth)
        async with cm as streams:
            read_stream, write_stream = streams[0], streams[1]
            async with ClientSession(read_stream, write_stream) as session:
                init_timeout = (FLOW_TTL + 60) if mode.interactive else 45.0
                await asyncio.wait_for(session.initialize(), timeout=init_timeout)
                tools = await asyncio.wait_for(_list_all_tools(session), timeout=60.0)
                with self._lock:
                    state.session = session
                    state.tools = tools
                    state.transport = transport
                    state.connected = True
                    self._pending.pop(server_name, None)
                    self._servers[server_name] = state
                    self._needs_auth.discard(server_name)
                self._register_tools(server_name, tools)
                self._record_connect_ok(server_name)
                mode.interactive = True  # a later expiry may ask the user
                auth_coordinator.finish(server_name, True, f"Connected · {len(tools)} tools")
                self._log(f"connected '{server_name}' ({len(tools)} tools)", "info")
                self._invalidate_prompt()
                if not ready.done():
                    ready.set_result(None)
                self._emit("connected", server_name)
                await asyncio.Event().wait()  # park until cancelled (disconnect)

    @staticmethod
    def _invalidate_prompt() -> None:
        try:
            from ..repl.system import invalidate_system_cache

            invalidate_system_cache()
        except Exception:
            pass

    # ── sign-in helpers (the Authenticate button) ────────────────────────

    def authenticate(self, server_name: str, config: dict[str, Any]) -> dict[str, Any]:
        """Make sure a sign-in is waiting for ``server_name`` and describe it.

        ``{"ok": True, "connected": True}`` when it's already through (saved
        login refreshed silently); ``{"ok": True, "url": …}`` when the user must
        open the link; ``{"ok": False, "error": …}`` otherwise.
        """
        if self.is_connected(server_name):
            return {"ok": True, "connected": True, "name": server_name}
        req = auth_coordinator.get(server_name)
        if req is not None and req.status in ("pending", "working"):
            return {"ok": True, "name": server_name, **req.public()}
        err = self.connect(server_name, config, interactive=True)
        if err is None:
            return {"ok": True, "connected": True, "name": server_name}
        if needs_auth(err):
            req = auth_coordinator.get(server_name)
            if req is not None:
                return {"ok": True, "name": server_name, **req.public()}
        return {"ok": False, "error": err, "name": server_name}

    def sign_out(self, server_name: str, config: dict[str, Any] | None = None) -> None:
        """Disconnect and forget the saved login (the next connect signs in again)."""
        self.disconnect(server_name)
        auth_coordinator.forget(server_name)
        with self._lock:
            self._needs_auth.discard(server_name)
        url = str((config or {}).get("url") or "")
        if url:
            forget_login(server_name, url)
        self._emit("disconnected", server_name)

    def _connect(
        self, server_name: str, state: MCPServerState, config: dict[str, Any],
    ) -> str | None:
        loop = _get_loop()
        try:
            transport_type = config.get("type", "stdio")

            if transport_type == "stdio":
                error = loop.run_coro(
                    self._connect_and_init_stdio(server_name, state, config),
                    timeout=30,
                )
            elif transport_type == "sse":
                error = loop.run_coro(
                    self._connect_and_init_sse(server_name, state, config),
                    timeout=30,
                )
            else:
                error = f"Unknown transport type: {transport_type}"

            if error:
                with self._lock:
                    state.connected = False
                    state.error = error
                    state.cleanup()
                    if server_name in self._servers:
                        del self._servers[server_name]
                self._record_connect_error(server_name, error)
                self._log(f"failed to connect '{server_name}': {error}", "error")
                return error

            # List tools
            assert state.session is not None
            result = loop.run_coro(state.session.list_tools(), timeout=30)
            tools: list[Tool] = result.tools if hasattr(result, "tools") else []
            with self._lock:
                state.tools = tools
                state.connected = True
                state.transport = "stdio" if transport_type == "stdio" else transport_type

            self._register_tools(server_name, tools)
            self._record_connect_ok(server_name)
            self._log(f"connected '{server_name}' ({len(tools)} tools)", "info")
            return None

        except Exception as e:
            error = f"{type(e).__name__}: {e}"
            with self._lock:
                if server_name in self._servers:
                    state = self._servers[server_name]
                    state.connected = False
                    state.error = error
                    state.cleanup()
                    del self._servers[server_name]
            self._record_connect_error(server_name, error)
            self._log(f"error connecting '{server_name}': {error}", "error")
            return error

    async def _connect_and_init_stdio(
        self,
        server_name: str,
        state: MCPServerState,
        config: dict[str, Any],
    ) -> str | None:
        """Phase 1: create stdio process, session, initialize. Returns None on success, error string on failure."""
        try:
            errlog = _open_stdio_errlog(server_name)
            state._stderr_sink = errlog
            params = StdioServerParameters(
                command=config["command"],
                args=config.get("args", []),
                env=_stdio_env(config),
                cwd=config.get("cwd"),
            )

            # These streams are passed directly to the keep-alive task
            streams: list[Any] = []

            async def _inner() -> tuple[ClientSession, Any, Any]:
                ctx = stdio_client(params, errlog=errlog)
                read_stream, write_stream = await ctx.__aenter__()
                streams.extend([ctx, read_stream, write_stream])
                session = ClientSession(read_stream, write_stream)
                await session.__aenter__()
                await session.initialize()
                return session, ctx, read_stream, write_stream

            session, ctx, read_stream, write_stream = await _inner()
            state.session = session

            # Start keep-alive task that holds the context managers open
            loop = asyncio.get_running_loop()
            state._keep_alive_task = asyncio.ensure_future(
                self._keep_stdio_alive(ctx, read_stream, write_stream, session, server_name)
            )
            return None

        except Exception as e:
            return f"{type(e).__name__}: {e}"

    async def _keep_stdio_alive(
        self,
        ctx,
        read_stream,
        write_stream,
        session: ClientSession,
        server_name: str,
    ) -> None:
        """Keep the stdio connection alive by parking the coroutine."""
        try:
            # Hold the context managers open indefinitely
            # Any disconnect will raise an exception here
            while True:
                await asyncio.sleep(3600)
        except (asyncio.CancelledError, GeneratorExit):
            pass
        except Exception as e:
            self._log(f"connection lost for '{server_name}': {e}", "warn")
            self._record_runtime_error(server_name, f"{type(e).__name__}: {e}")
        finally:
            # Cleanup
            try:
                await session.__aexit__(None, None, None)
            except Exception:
                pass
            try:
                await ctx.__aexit__(None, None, None)
            except Exception:
                pass

    async def _connect_and_init_sse(
        self,
        server_name: str,
        state: MCPServerState,
        config: dict[str, Any],
    ) -> str | None:
        """Connect to an SSE-based MCP server."""
        try:
            headers = config.get("headers", {})
            url = config["url"]

            streams: list[Any] = []

            async def _inner() -> tuple[ClientSession, Any, Any]:
                ctx = sse_client(url, headers=headers)
                read_stream, write_stream = await ctx.__aenter__()
                streams.extend([ctx, read_stream, write_stream])
                session = ClientSession(read_stream, write_stream)
                await session.__aenter__()
                await session.initialize()
                return session, ctx, read_stream, write_stream

            session, ctx, read_stream, write_stream = await _inner()
            state.session = session

            loop = asyncio.get_running_loop()
            state._keep_alive_task = asyncio.ensure_future(
                self._keep_sse_alive(ctx, read_stream, write_stream, session, server_name)
            )
            return None

        except Exception as e:
            return f"{type(e).__name__}: {e}"

    async def _keep_sse_alive(
        self,
        ctx,
        read_stream,
        write_stream,
        session: ClientSession,
        server_name: str,
    ) -> None:
        """Keep the SSE connection alive."""
        try:
            while True:
                await asyncio.sleep(3600)
        except (asyncio.CancelledError, GeneratorExit):
            pass
        except Exception as e:
            self._log(f"SSE connection lost for '{server_name}': {e}", "warn")
            self._record_runtime_error(server_name, f"{type(e).__name__}: {e}")
        finally:
            try:
                await session.__aexit__(None, None, None)
            except Exception:
                pass
            try:
                await ctx.__aexit__(None, None, None)
            except Exception:
                pass

    def disconnect(self, server_name: str) -> str | None:
        """Disconnect an MCP server and unregister its tools.

        Safe to call from any thread — the keep-alive task lives on the
        MCP event-loop thread and is cancelled via ``call_soon_threadsafe``.
        Also stops a remote server that is still waiting for its sign-in.
        """
        with self._lock:
            pending = self._pending.pop(server_name, None)
            srv = self._servers.get(server_name)
            if srv is None and pending is None:
                self._needs_auth.discard(server_name)
                return f"Server '{server_name}' not found"
            self._needs_auth.discard(server_name)
            tools = list(srv.tools) if srv is not None else []
            keep_alive = srv._keep_alive_task if srv is not None else None
            cleanup_fns = list(srv._cleanup_fns) if srv is not None else []
            if srv is not None:
                srv._cleanup_fns.clear()
                del self._servers[server_name]

        for st in (pending, srv):
            fut = getattr(st, "_remote_future", None)
            if fut is not None and not fut.done():
                fut.cancel()
        if pending is not None:
            auth_coordinator.cancel(server_name)

        # Cancel the keep-alive task on its own loop (cross-thread safe).
        if keep_alive is not None and not keep_alive.done():
            try:
                loop_obj = _get_loop()
                loop_obj.loop.call_soon_threadsafe(keep_alive.cancel)
            except Exception as e:
                self._log(f"keep-alive cancel failed for '{server_name}': {e}", "warn")

        # Run any cleanup functions outside the lock.
        for fn in cleanup_fns:
            try:
                fn()
            except Exception:
                pass

        self._unregister_tools(server_name, tools)
        self._record_disconnect(server_name)
        self._log(f"disconnected '{server_name}'", "info")
        self._emit("disconnected", server_name)
        return None

    def disconnect_all(self) -> None:
        """Disconnect all MCP servers."""
        names = list(self._servers.keys())
        for name in names:
            self.disconnect(name)

    # ── tool call routing ────────────────────────────────────────────────

    def _call_mcp_tool(self, server_name: str, tool_name: str, args: dict[str, Any]) -> str:
        """Call an MCP tool on the connected server and return the result as a string."""
        with self._lock:
            state = self._servers.get(server_name)
            if state is None or not state.connected or state.session is None:
                return f"ERROR: MCP server '{server_name}' is not connected"
            session = state.session

        loop = _get_loop()
        t0 = time.monotonic()
        try:
            result = loop.run_coro(
                session.call_tool(tool_name, arguments=args),
                timeout=120,
            )
            out = self._format_tool_result(result)
            elapsed_ms = (time.monotonic() - t0) * 1000
            is_err = out.startswith("ERROR:")
            with self._lock:
                rec = self._health_record(server_name)
                rec.last_tool_at = time.monotonic()
                rec.last_tool_ms = elapsed_ms
                rec.last_tool_name = tool_name
                if is_err:
                    rec.last_tool_error = out[:240]
                    rec.tool_calls_err += 1
                else:
                    rec.last_tool_error = None
                    rec.tool_calls_ok += 1
            return out
        except TimeoutError:
            elapsed_ms = (time.monotonic() - t0) * 1000
            err = f"ERROR: MCP tool '{server_name}/{tool_name}' timed out (120s)"
            with self._lock:
                rec = self._health_record(server_name)
                rec.last_tool_at = time.monotonic()
                rec.last_tool_ms = elapsed_ms
                rec.last_tool_name = tool_name
                rec.last_tool_error = err
                rec.tool_calls_err += 1
            return err
        except Exception as e:
            elapsed_ms = (time.monotonic() - t0) * 1000
            err = f"ERROR: {type(e).__name__}: {e}"
            with self._lock:
                rec = self._health_record(server_name)
                rec.last_tool_at = time.monotonic()
                rec.last_tool_ms = elapsed_ms
                rec.last_tool_name = tool_name
                rec.last_tool_error = err
                rec.tool_calls_err += 1
            return err

    def _format_tool_result(self, result) -> str:
        """Format an MCP CallToolResult into a string."""
        if hasattr(result, "content"):
            parts = []
            for item in result.content:
                if hasattr(item, "text") and item.text:
                    parts.append(item.text)
                elif hasattr(item, "data") and item.data:
                    parts.append(f"[binary data: {len(item.data)} bytes]")
                elif hasattr(item, "resource"):
                    parts.append(f"[resource: {item.resource}]")
            text = "\n".join(parts)
            if hasattr(result, "isError") and result.isError:
                return f"ERROR:\n{text}"
            return text
        return str(result)

    # ── queries ──────────────────────────────────────────────────────────

    def list_connected(self) -> list[tuple[str, list[Tool], str | None]]:
        """List connected servers with their tools and optional error."""
        results = []
        with self._lock:
            for name, state in self._servers.items():
                results.append((name, state.tools, state.error if not state.connected else None))
        return results

    def is_connected(self, server_name: str) -> bool:
        """Check if a specific server is connected."""
        with self._lock:
            state = self._servers.get(server_name)
            return state is not None and state.connected

    def get_server_tools(self, server_name: str) -> list[Tool]:
        """Get tools for a connected server."""
        with self._lock:
            state = self._servers.get(server_name)
            if state:
                return state.tools
            return []

    def tool_count(self) -> int:
        """Total number of registered MCP tools across all servers."""
        count = 0
        with self._lock:
            for state in self._servers.values():
                if state.connected:
                    count += len(state.tools)
        return count


# Global singleton
mcp_registry = MCPRegistry()


def as_prompt_block() -> str:
    """Format configured MCP servers into the system prompt.

    Project-scoped servers are always listed. Global sources appear when
    ``state.global_mcp`` is on. Connection status is included so the agent
    knows which tools are callable vs need ``/mcp`` connect first.
    """
    from .. import state as jarvis_state
    from .config import get_config

    config = get_config()
    servers = config.list_servers()

    lines: list[str] = []
    if not jarvis_state.global_mcp:
        lines.append(
            "MCP scope: project-only (global OFF). "
            "Servers from ~/.cursor, ~/.claude, OpenCode, etc. are NOT loaded. "
            "Enable via /mcp → press g, then connect. "
            "Do NOT read those config files to bypass missing MCP — tell the user to enable global scope."
        )

    if not servers:
        if lines:
            lines.append(
                "No MCP servers in current scope (add .mcp.json in project or enable global scope)."
            )
            return "\n".join(lines)
        return ""

    connected = mcp_registry.list_connected()
    live_names = {name for name, _tools, _err in connected}

    scope = "project + global" if jarvis_state.global_mcp else "project-only"
    names = sorted(servers.keys())
    def _label(name: str) -> str:
        if name in live_names:
            return name
        try:
            status = mcp_registry.get_server_health(name, servers.get(name)).get("status")
        except Exception:
            status = ""
        if status == "auth":
            return f"{name} (needs sign-in — the user clicks Authenticate; tools appear after)"
        return f"{name} (offline — connect via /mcp)"

    lines.append(f"MCP ({scope}): " + ", ".join(_label(name) for name in names))
    if live_names:
        lines.append(
            "Connected MCP tools are callable as mcp__<server>__<tool>."
            " Re-check after /mcp scope or connection changes in the same session."
        )
    else:
        lines.append(
            "No MCP servers connected — open /mcp and connect before calling MCP tools."
        )
    return "\n".join(lines)


def auto_connect_servers(
    console_print: Callable | None = None,
    on_change: Callable[[], None] | None = None,
) -> None:
    """Auto-connect MCP servers listed in the config's auto_connect field.

    Quiet on success — connection status lives in the sidebar and ``/mcp``;
    problems are printed only when ``console_print`` is given (the TUI passes
    none and shows them in the sidebar). ``on_change`` runs when a server
    starts and finishes connecting, so a status view can repaint.
    """
    from .config import get_config

    config = get_config()
    queued = [n for n in config.get_auto_connect() if config.get_server(n) is not None]
    mcp_registry.set_connecting(queued, True)
    if on_change:
        on_change()
    try:
        for name in config.get_auto_connect():
            server_cfg = config.get_server(name)
            if server_cfg is None:
                if console_print:
                    console_print(f"[yellow]mcp: server '{name}' not found in config, skipping[/]")
                continue
            with mcp_registry.startup_connect():
                error = mcp_registry.connect(name, server_cfg)
            mcp_registry.set_connecting([name], False)
            if on_change:
                on_change()
            if error and console_print and not needs_auth(error):
                console_print(f"[red]mcp: failed to connect '{name}': {error}[/]")
    finally:
        mcp_registry.set_connecting(queued, False)
