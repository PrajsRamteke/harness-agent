"""Slash command handler for /mcp — manage MCP servers from the chat."""

from __future__ import annotations

import shlex

from .config import MCPConfig, get_config, reload_config
from .registry import mcp_registry


def handle_mcp_command(arg: str) -> str | None:
    """Handle /mcp <subcommand> [args...].

    Returns a string to display to the user, or None if handled silently.
    """
    if not arg:
        return _show_usage()

    parts = shlex.split(arg)
    cmd = parts[0].lower()

    if cmd == "list":
        return _cmd_list()
    elif cmd in ("add", "register"):
        return _cmd_add(parts[1:])
    elif cmd in ("rm", "remove", "delete", "del"):
        return _cmd_remove(parts[1:])
    elif cmd in ("conn", "connect"):
        return _cmd_connect(parts[1:])
    elif cmd in ("disconn", "disconnect"):
        return _cmd_disconnect(parts[1:])
    elif cmd == "reload":
        return _cmd_reload()
    elif cmd in ("help", "--help", "-h"):
        return _show_usage()
    else:
        return f"Unknown /mcp subcommand: {cmd}\n\n{_show_usage()}"


def _show_usage() -> str:
    return (
        "[bold yellow]MCP — Model Context Protocol[/]\n\n"
        "Connect external tool servers to Jarvis.\n\n"
        "[cyan]/mcp list[/]                  — show configured & connected servers\n"
        "[cyan]/mcp add <name> --command <cmd> [args...] [--env KEY=VAL ...][/]  — add a stdio server\n"
        "[cyan]/mcp add <name> --url <url>[/] — add an SSE server\n"
        "[cyan]/mcp remove <name>[/]          — remove a server config\n"
        "[cyan]/mcp connect <name>[/]         — connect a configured server\n"
        "[cyan]/mcp disconnect <name>[/]      — disconnect a running server\n"
        "[cyan]/mcp reload[/]                 — reload config from file\n\n"
        "[dim]Example:[/]\n"
        "  /mcp add my-db --command uvx -- mcp-server-sqlite --db test.db\n"
        "  /mcp connect my-db\n"
        "  → tools become available as [i]mcp__my-db__query[/], etc.\n\n"
        "Configuration is saved to [dim]~/.config/harness-agent/mcp.json[/]"
    )


def _cmd_list() -> str:
    config = get_config()
    servers = config.list_servers()
    auto_connect = config.get_auto_connect()

    lines = ["[bold yellow]MCP Servers[/]\n"]

    if not servers:
        lines.append("[dim]No servers configured. Use /mcp add <name> --command <cmd>[/]")
    else:
        for name, cfg in sorted(servers.items()):
            transport = cfg.get("type", "stdio")
            endpoint = cfg.get("command", cfg.get("url", ""))
            is_auto = " [cyan]auto[/]" if name in auto_connect else ""
            connected = " [green]✓[/]" if mcp_registry.is_connected(name) else " [dim]✗[/]"
            tool_count = len(mcp_registry.get_server_tools(name))
            tools_str = f" ({tool_count} tools)" if tool_count > 0 else ""
            lines.append(f"  {connected} [bold]{name}[/]{is_auto} [dim]{transport}[/] {endpoint}{tools_str}")

    connected = mcp_registry.list_connected()
    total_tools = mcp_registry.tool_count()
    lines.append(f"\n[dim]{len(connected)} connected · {total_tools} MCP tools active[/]")
    if total_tools > 0:
        lines.append("\n[dim]MCP tools are namespaced as [i]mcp__<server>__<tool>[/]")

    return "\n".join(lines)


def _cmd_add(args: list[str]) -> str:
    if len(args) < 2:
        return "[red]Usage: /mcp add <name> --command <cmd> [args...]  or  /mcp add <name> --url <url>[/]"

    name = args[0]
    rest = args[1:]

    # Parse flags
    command = None
    url = None
    cmd_args = []
    env = {}
    auto_connect = False

    i = 0
    while i < len(rest):
        if rest[i] == "--command" and i + 1 < len(rest):
            i += 1
            command = rest[i]
        elif rest[i] == "--url" and i + 1 < len(rest):
            i += 1
            url = rest[i]
        elif rest[i] == "--env" and i + 1 < len(rest):
            i += 1
            if "=" in rest[i]:
                k, v = rest[i].split("=", 1)
                env[k] = v
            else:
                return f"[red]Invalid env format: {rest[i]}. Use KEY=VALUE[/]"
        elif rest[i] == "--auto":
            auto_connect = True
        elif rest[i] == "--":
            cmd_args.extend(rest[i + 1:])
            break
        else:
            cmd_args.append(rest[i])
        i += 1

    config = get_config()
    try:
        config.add_server(
            name,
            command=command,
            args=cmd_args,
            env=env if env else None,
            url=url,
            auto_connect=auto_connect,
        )
        config.save()
        parts = []
        if command:
            parts.append(f"stdio ({command} {' '.join(cmd_args)})")
        else:
            parts.append(f"sse ({url})")
        if auto_connect:
            parts.append("auto-connect")
        return f"[green]✓[/] Server '[bold]{name}[/]' added ({'; '.join(parts)}).\nUse [cyan]/mcp connect {name}[/] to start it."
    except ValueError as e:
        return f"[red]{e}[/]"


def _cmd_remove(args: list[str]) -> str:
    if not args:
        return "[red]Usage: /mcp remove <name>[/]"

    name = args[0]
    if mcp_registry.is_connected(name):
        mcp_registry.disconnect(name)

    config = get_config()
    if config.remove_server(name):
        config.save()
        return f"[green]✓[/] Server '[bold]{name}[/]' removed."
    else:
        return f"[red]Server '{name}' not found.[/]"


def _cmd_connect(args: list[str]) -> str:
    if not args:
        return "[red]Usage: /mcp connect <name>[/]"

    name = args[0]
    config = get_config()
    server_cfg = config.get_server(name)
    if server_cfg is None:
        return f"[red]Server '{name}' not found in config. Add it with /mcp add first.[/]"

    error = mcp_registry.connect(name, server_cfg)
    if error:
        return f"[red]Failed to connect '{name}': {error}[/]"
    else:
        count = len(mcp_registry.get_server_tools(name))
        tool_list = ", ".join(f"[cyan]mcp__{name}__{t.name}[/]" for t in mcp_registry.get_server_tools(name)[:5])
        extra = f"\nTools: {tool_list}" + ("…" if count > 5 else "")
        return f"[green]✓[/] '{name}' connected ({count} tools).{extra}"


def _cmd_disconnect(args: list[str]) -> str:
    if not args:
        return "[red]Usage: /mcp disconnect <name>[/]"

    name = args[0]
    error = mcp_registry.disconnect(name)
    if error:
        return f"[red]{error}[/]"
    return f"[green]✓[/] '{name}' disconnected."


def _cmd_reload() -> str:
    config = reload_config()
    servers = config.list_servers()
    auto = config.get_auto_connect()
    lines = [
        f"[green]✓[/] Config reloaded ({len(servers)} servers, {len(auto)} auto-connect).",
        "",
        _cmd_list(),
    ]
    return "\n".join(lines)
