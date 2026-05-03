"""MCP server registry — manages connections, tool registration, call routing.

Bridges MCP's async Python SDK to Jarvis's synchronous tool execution via a
dedicated background thread with a persistent asyncio event loop.
"""

from __future__ import annotations

import asyncio
import logging
import pathlib
import re
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Callable

from mcp import Tool
from mcp.client.session import ClientSession
from mcp.client.sse import sse_client
from mcp.client.stdio import stdio_client, StdioServerParameters

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
    _cleanup_fns: list[Callable[[], None]] = field(default_factory=list)

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


def _normalize_input_schema(schema: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(schema, dict):
        return {"type": "object", "properties": {}}
    if "type" not in schema:
        schema["type"] = "object"
    if schema.get("type") == "object" and "properties" not in schema:
        schema["properties"] = {}
    if "required" not in schema:
        schema["required"] = []
    return schema


# ── registry singleton ────────────────────────────────────────────────────

class MCPRegistry:
    """Central registry of all active MCP server connections and their tools."""

    def __init__(self) -> None:
        self._servers: dict[str, MCPServerState] = {}
        self._lock = threading.Lock()

        # References to the Jarvis tool system — set via init_jarvis()
        self._func_dict: dict | None = None
        self._tool_groups: dict | None = None
        self._tools_list: list | None = None
        self._tool_name_to_group: dict | None = None
        self._console_print: Callable | None = None

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

    def connect(self, server_name: str, config: dict[str, Any]) -> str | None:
        """Connect to an MCP server and register its tools.

        Returns error message on failure, None on success.
        """
        with self._lock:
            if server_name in self._servers and self._servers[server_name].connected:
                return f"Server '{server_name}' is already connected"

            state = MCPServerState(config=config)
            self._servers[server_name] = state

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
                self._log(f"failed to connect '{server_name}': {error}", "error")
                return error

            # List tools
            assert state.session is not None
            result = loop.run_coro(state.session.list_tools(), timeout=30)
            tools: list[Tool] = result.tools if hasattr(result, "tools") else []
            with self._lock:
                state.tools = tools
                state.connected = True

            self._register_tools(server_name, tools)
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
            params = StdioServerParameters(
                command=config["command"],
                args=config.get("args", []),
                env=config.get("env"),
                cwd=config.get("cwd"),
            )

            # These streams are passed directly to the keep-alive task
            streams: list[Any] = []

            async def _inner() -> tuple[ClientSession, Any, Any]:
                ctx = stdio_client(params)
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
        """Disconnect an MCP server and unregister its tools."""
        with self._lock:
            state = self._servers.get(server_name)
            if state is None:
                return f"Server '{server_name}' not found"
            if not state.connected and state.error is None:
                return f"Server '{server_name}' is not connected"

            tools = state.tools
            state.cleanup()
            del self._servers[server_name]

        self._unregister_tools(server_name, tools)
        self._log(f"disconnected '{server_name}'", "info")
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
        try:
            result = loop.run_coro(
                session.call_tool(tool_name, arguments=args),
                timeout=120,
            )
            return self._format_tool_result(result)
        except TimeoutError:
            return f"ERROR: MCP tool '{server_name}/{tool_name}' timed out (120s)"
        except Exception as e:
            return f"ERROR: {type(e).__name__}: {e}"

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


def auto_connect_servers(console_print: Callable | None = None) -> None:
    """Auto-connect MCP servers listed in the config's auto_connect field."""
    from .config import get_config

    config = get_config()
    names = config.get_auto_connect()
    if not names:
        if console_print:
            console_print("[dim]mcp: no servers configured for auto-connect[/]")
        return

    for name in names:
        server_cfg = config.get_server(name)
        if server_cfg is None:
            if console_print:
                console_print(f"[yellow]mcp: server '{name}' not found in config, skipping[/]")
            continue
        if console_print:
            console_print(f"[dim]mcp: auto-connecting '{name}'…[/]")
        error = mcp_registry.connect(name, server_cfg)
        if error and console_print:
            console_print(f"[red]mcp: failed to connect '{name}': {error}[/]")
        elif console_print:
            cnt = len(mcp_registry.get_server_tools(name))
            console_print(f"[green]mcp: '{name}' connected ({cnt} tools)[/]")
