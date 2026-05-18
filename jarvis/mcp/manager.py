"""Slash command handler for /mcp — manage MCP servers from the chat."""

from __future__ import annotations

import shlex
from typing import Any

from rich.align import Align
from rich.console import Group
from rich.padding import Padding
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from .. import state
from .config import (
    MCPConfig,
    MCP_GLOBAL_CONFIG_FILE,
    get_config,
    reload_config,
    _project_config_path,
    _global_sources,
)
from .registry import mcp_registry


# ── public entry point ───────────────────────────────────────────────────

def handle_mcp_command(arg: str) -> Any:
    """Handle ``/mcp <subcommand>`` and return a Rich renderable (or string).

    Anything truthy returned is passed to ``console.print()`` by the dispatcher,
    so we can return ``Panel`` / ``Group`` objects directly for richer output.
    """
    if not arg:
        return _usage_panel()

    parts = shlex.split(arg)
    cmd = parts[0].lower()
    sub_args = parts[1:]

    if cmd == "list":
        return _cmd_list()
    if cmd in ("add", "register"):
        return _cmd_add(sub_args)
    if cmd in ("rm", "remove", "delete", "del"):
        return _cmd_remove(sub_args)
    if cmd in ("conn", "connect"):
        return _cmd_connect(sub_args)
    if cmd in ("disconn", "disconnect"):
        return _cmd_disconnect(sub_args)
    if cmd == "reload":
        return _cmd_reload()
    if cmd in ("paths", "path", "where"):
        return _cmd_paths()
    if cmd == "global":
        return _cmd_global(sub_args)
    if cmd in ("help", "--help", "-h"):
        return _usage_panel()
    return Group(
        Text.from_markup(f"[red]Unknown /mcp subcommand:[/] [bold]{cmd}[/]"),
        _usage_panel(),
    )


# ── shared visual primitives ─────────────────────────────────────────────

_SOURCE_ICONS = {
    "project":  "▣",
    "jarvis":   "✦",
    "claude":   "◆",
    "opencode": "◇",
    "cursor":   "⌘",
    "windsurf": "≈",
    "vscode":   "⬡",
}

_SOURCE_LABELS = {
    "project":  "Project",
    "jarvis":   "Jarvis",
    "claude":   "Claude Code",
    "opencode": "OpenCode",
    "cursor":   "Cursor",
    "windsurf": "Windsurf",
    "vscode":   "VS Code",
}

_SOURCE_ORDER = ["jarvis", "claude", "opencode", "cursor", "windsurf", "vscode"]


def _format_endpoint(cfg: dict, max_len: int = 60) -> str:
    """Render the runtime command line / URL for display."""
    if cfg.get("url"):
        s = str(cfg["url"])
    else:
        parts = [str(cfg.get("command", ""))]
        parts += [str(a) for a in cfg.get("args", [])]
        s = " ".join(p for p in parts if p)
    if len(s) > max_len:
        s = s[: max_len - 1] + "…"
    return s


def _status_cell(connected: bool, tools: int) -> Text:
    if connected:
        text = Text("● live", style="bold green")
        if tools:
            text.append(f"  {tools} tools", style="dim green")
        return text
    return Text("○ idle", style="dim")


def _server_table(rows: list[tuple[str, dict, bool]]) -> Table:
    """Build a server table. Each row is (name, cfg, is_auto_connect)."""
    t = Table.grid(padding=(0, 2), expand=True)
    t.add_column(justify="left", no_wrap=True, width=16)   # status
    t.add_column(justify="left", no_wrap=True, min_width=14)  # name
    t.add_column(justify="left", no_wrap=True, width=6)    # auto
    t.add_column(justify="left", no_wrap=True, width=6)    # transport
    t.add_column(justify="left", overflow="ellipsis")      # endpoint

    for name, cfg, is_auto in rows:
        connected = mcp_registry.is_connected(name)
        tools = len(mcp_registry.get_server_tools(name))
        transport = cfg.get("type", "stdio")
        endpoint = _format_endpoint(cfg, max_len=80)

        t.add_row(
            _status_cell(connected, tools),
            Text(name, style="bold white"),
            Text("auto", style="cyan") if is_auto else Text("", style="dim"),
            Text(transport, style="dim"),
            Text(endpoint, style="dim"),
        )
    return t


# ── /mcp (usage) ─────────────────────────────────────────────────────────

def _usage_panel() -> Panel:
    cmds = Table.grid(padding=(0, 2))
    cmds.add_column(style="cyan", no_wrap=True)
    cmds.add_column(style="white")
    rows = [
        ("/mcp list",                 "show every project & global server with its status"),
        ("/mcp paths",                "show config-file locations across all known tools"),
        ("/mcp global on",            "load servers from Claude Code / OpenCode / Cursor / Windsurf / VS Code"),
        ("/mcp global off",           "project-only — hide globals (default)"),
        ("/mcp connect <name>",       "start a configured server"),
        ("/mcp disconnect <name>",    "stop a running server"),
        ("/mcp add <name> --command …", "register a stdio server in Jarvis global"),
        ("/mcp add <name> --url …",   "register an SSE/HTTP server in Jarvis global"),
        ("/mcp remove <name>",        "remove a Jarvis-managed global server"),
        ("/mcp reload",               "re-read every config file from disk"),
    ]
    for c, d in rows:
        cmds.add_row(c, d)

    scopes = Text.assemble(
        ("▣ project", "magenta bold"),
        ("  always active — reads ", "dim"),
        (".mcp.json", "white"),
        (" from the current directory (Claude Code compatible)\n", "dim"),
        ("◎ global ", "blue bold"),
        (" opt-in — aggregates servers from Jarvis, Claude Code, OpenCode, Cursor,", "dim"),
        (" Windsurf, VS Code", "dim"),
    )

    body = Group(
        cmds,
        Text(""),
        Panel(scopes, title="[bold]scopes[/]", border_style="dim", padding=(0, 1)),
    )
    return Panel(
        body,
        title="[bold yellow]○ MCP — Model Context Protocol[/]",
        subtitle="[dim]/mcp list  ·  /mcp global on[/]",
        border_style="yellow",
        padding=(1, 2),
    )


# ── /mcp list ────────────────────────────────────────────────────────────

def _project_section(config: MCPConfig, project_rows: list[tuple[str, dict, bool]]) -> Panel:
    proj_path = config.project_config_path()
    if proj_path is not None:
        subtitle = Text.from_markup(f"[dim]{proj_path}[/]")
    else:
        subtitle = Text.from_markup("[dim]no .mcp.json in current directory[/]")

    if project_rows:
        body: Any = _server_table(project_rows)
    else:
        body = Padding(
            Text.from_markup(
                "[dim]Empty.[/] [white]Drop a [bold].mcp.json[/] in this directory[/] "
                "[dim](Claude Code format works).[/]"
            ),
            (0, 0),
        )

    return Panel(
        body,
        title="[bold magenta]▣ Project[/]",
        subtitle=subtitle,
        border_style="magenta",
        padding=(0, 1),
    )


def _global_section(
    config: MCPConfig,
    global_by_source: dict[str, list[tuple[str, dict, bool]]],
) -> Panel:
    if not config.include_global():
        body: Any = Text.from_markup(
            "[yellow]Disabled.[/] [dim]Enable to pull servers from "
            "Claude Code, OpenCode, Cursor, Windsurf and VS Code:[/]\n"
            "    [cyan]/mcp global on[/]"
        )
        return Panel(
            body,
            title="[bold blue]◉ Global[/]",
            subtitle="[dim]off[/]",
            border_style="blue",
            padding=(0, 1),
        )

    if not global_by_source:
        body = Text.from_markup(
            "[dim]No servers in any of the scanned global config files.[/] "
            "[dim]See[/] [cyan]/mcp paths[/]"
        )
        return Panel(
            body,
            title="[bold blue]◉ Global[/]",
            subtitle="[dim]0 servers[/]",
            border_style="blue",
            padding=(0, 1),
        )

    # Build a sub-panel per source — sources stack vertically.
    source_panels: list[Any] = []
    ordered = [s for s in _SOURCE_ORDER if s in global_by_source] + [
        s for s in global_by_source if s not in _SOURCE_ORDER
    ]
    src_paths = {label: p for label, p, exists, _c in config.global_sources() if exists}
    for source in ordered:
        rows = global_by_source[source]
        icon = _SOURCE_ICONS.get(source, "•")
        label = _SOURCE_LABELS.get(source, source)
        path = src_paths.get(source)
        subtitle = Text.from_markup(f"[dim]{path}[/]") if path else Text("")
        source_panels.append(
            Panel(
                _server_table(rows),
                title=f"[bold]{icon} {label}[/]",
                subtitle=subtitle,
                border_style="dim",
                padding=(0, 1),
            )
        )

    total = sum(len(v) for v in global_by_source.values())
    return Panel(
        Group(*source_panels),
        title="[bold blue]◉ Global[/]",
        subtitle=f"[dim]{total} servers · {len(global_by_source)} sources[/]",
        border_style="blue",
        padding=(0, 1),
    )


def _cmd_list() -> Panel:
    config = get_config()
    servers = config.list_servers()
    auto_connect = set(config.get_auto_connect())

    # Partition
    project_rows: list[tuple[str, dict, bool]] = []
    global_by_source: dict[str, list[tuple[str, dict, bool]]] = {}
    for name in sorted(servers):
        cfg = servers[name]
        scope = config.get_scope(name) or "global"
        triple = (name, cfg, name in auto_connect)
        if scope == "project":
            project_rows.append(triple)
        else:
            src = config.get_source(name) or "global"
            global_by_source.setdefault(src, []).append(triple)

    # Footer stats
    connected_count = len(mcp_registry.list_connected())
    tool_count = mcp_registry.tool_count()
    total_servers = len(servers)
    auto_count = len(auto_connect)

    footer_bits = [
        ("● ", "green"), (f"{connected_count} live", "bold green"),
        ("  ·  ", "dim"),
        (f"{tool_count} tools", "green"),
        ("  ·  ", "dim"),
        (f"{total_servers} configured", "white"),
        ("  ·  ", "dim"),
        (f"{auto_count} auto", "cyan"),
    ]
    footer = Text.assemble(*footer_bits)
    hint = Text.from_markup(
        "[dim]Connect any idle server with[/] [cyan]/mcp connect <name>[/]"
        + ("" if config.include_global() else
           "  [dim]·[/]  [cyan]/mcp global on[/] [dim]to load globals[/]")
    )

    scope_label = (
        Text("◎  project + global", style="bold blue")
        if config.include_global()
        else Text("▣  project-only", style="bold magenta")
    )

    body = Group(
        Padding(scope_label, (0, 0, 1, 0)),
        _project_section(config, project_rows),
        Text(""),
        _global_section(config, global_by_source),
        Text(""),
        footer,
        hint,
    )
    return Panel(
        body,
        title="[bold yellow]○ MCP Servers[/]",
        border_style="yellow",
        padding=(1, 2),
    )


# ── /mcp paths ───────────────────────────────────────────────────────────

def _cmd_paths() -> Panel:
    config = get_config()

    t = Table(
        show_header=True,
        header_style="bold cyan",
        box=None,
        padding=(0, 2),
        expand=True,
    )
    t.add_column("scope", no_wrap=True)
    t.add_column("source", no_wrap=True)
    t.add_column("path", overflow="fold")
    t.add_column("status", no_wrap=True, justify="right")

    # Project row first
    proj_path = config.project_config_path()
    if proj_path is not None:
        t.add_row(
            Text("▣ project", style="bold magenta"),
            Text(""),
            Text(str(proj_path), style="white"),
            Text("found", style="green"),
        )
    else:
        t.add_row(
            Text("▣ project", style="bold magenta"),
            Text(""),
            Text(str(_project_config_path()), style="dim"),
            Text("missing", style="yellow"),
        )

    enabled = config.include_global()
    if enabled:
        scanned = config.global_sources()
    else:
        scanned = [(lbl, p, p.exists(), 0) for (lbl, p, _ptr) in _global_sources()]

    for label, path, exists, count in scanned:
        icon = _SOURCE_ICONS.get(label, "•")
        name = _SOURCE_LABELS.get(label, label)
        if not exists:
            status = Text("missing", style="yellow")
        elif enabled and count > 0:
            status = Text(f"{count} servers", style="green")
        elif enabled:
            status = Text("empty", style="dim")
        else:
            status = Text("scanned", style="dim")

        t.add_row(
            Text("◉ global", style="bold blue"),
            Text(f"{icon} {name}", style="white"),
            Text(str(path), style="dim"),
            status,
        )

    footer = Text.from_markup(
        f"global discovery: "
        f"{'[green]on[/]' if enabled else '[yellow]off[/]'}"
        f"   [dim]·[/]   "
        + ("[cyan]/mcp global off[/]" if enabled else "[cyan]/mcp global on[/]")
        + "   [dim]·[/]   [dim]write via[/] [cyan]/mcp add[/] [dim](Jarvis source only)[/]"
    )

    return Panel(
        Group(t, Text(""), footer),
        title="[bold yellow]○ MCP Config Paths[/]",
        border_style="yellow",
        padding=(1, 2),
    )


# ── /mcp global ──────────────────────────────────────────────────────────

def _cmd_global(args: list[str]):
    if not args:
        scope = (
            Text("◎ global ", style="bold blue")
            if state.global_mcp
            else Text("▣ project-only ", style="bold magenta")
        )
        body = Group(
            scope,
            Text(""),
            Text.from_markup(
                "[cyan]/mcp global on[/]   [dim]load servers from Claude Code, OpenCode, "
                "Cursor, Windsurf, VS Code[/]"
            ),
            Text.from_markup(
                "[cyan]/mcp global off[/]  [dim]project-only — hide globals[/]"
            ),
        )
        return Panel(
            body,
            title="[bold yellow]○ MCP scope[/]",
            border_style="yellow",
            padding=(1, 2),
        )

    val = args[0].lower()
    if val not in ("on", "off"):
        return Text.from_markup("[red]Usage:[/] [cyan]/mcp global on|off[/]")

    new = (val == "on")
    changed = (new != state.global_mcp)
    if changed:
        state.global_mcp = new
        state.save_mcp_config()

    config = reload_config()
    servers = config.list_servers()
    auto = config.get_auto_connect()

    # Reconcile live connections with the new scope.
    visible = set(servers.keys())
    for srv_name, _tools, _err in mcp_registry.list_connected():
        if srv_name not in visible:
            mcp_registry.disconnect(srv_name)

    newly_connected: list[str] = []
    failures: list[tuple[str, str]] = []
    for srv_name in auto:
        if mcp_registry.is_connected(srv_name):
            continue
        cfg = config.get_server(srv_name)
        if cfg is None:
            continue
        err = mcp_registry.connect(srv_name, cfg)
        if err:
            failures.append((srv_name, err))
        else:
            newly_connected.append(srv_name)

    # Build a short status banner, then the full list below it.
    headline = (
        Text.from_markup(
            f"[green]✓[/] global scope [bold]enabled[/] "
            f"[dim]({len(servers)} servers visible · {len(auto)} auto-connect)[/]"
        )
        if new else
        Text.from_markup(
            f"[green]✓[/] global scope [bold]disabled[/] "
            f"[dim]({len(servers)} project servers remain visible)[/]"
        )
    )
    deltas: list[Text] = []
    if newly_connected:
        deltas.append(
            Text.from_markup(
                "[dim]connected:[/] "
                + ", ".join(f"[bold]{n}[/]" for n in newly_connected)
            )
        )
    if failures:
        deltas.append(
            Text.from_markup(
                "[red]failed:[/] "
                + ", ".join(f"[bold]{n}[/] [dim]({err})[/]" for n, err in failures)
            )
        )

    banner = Panel(
        Group(headline, *deltas) if deltas else headline,
        border_style="green" if new else "magenta",
        padding=(0, 1),
    )
    return Group(banner, _cmd_list())


# ── /mcp add / remove ────────────────────────────────────────────────────

def _cmd_add(args: list[str]):
    if len(args) < 2:
        return Text.from_markup(
            "[red]Usage:[/] [cyan]/mcp add <name> --command <cmd> [args…][/] "
            "[dim]or[/] [cyan]/mcp add <name> --url <url>[/]"
        )

    name = args[0]
    rest = args[1:]

    command: str | None = None
    url: str | None = None
    cmd_args: list[str] = []
    env: dict[str, str] = {}
    auto_connect = False

    i = 0
    while i < len(rest):
        token = rest[i]
        if token == "--command" and i + 1 < len(rest):
            i += 1
            command = rest[i]
        elif token == "--url" and i + 1 < len(rest):
            i += 1
            url = rest[i]
        elif token == "--env" and i + 1 < len(rest):
            i += 1
            if "=" in rest[i]:
                k, v = rest[i].split("=", 1)
                env[k] = v
            else:
                return Text.from_markup(
                    f"[red]Invalid env format:[/] {rest[i]} [dim](use KEY=VALUE)[/]"
                )
        elif token == "--auto":
            auto_connect = True
        elif token == "--":
            cmd_args.extend(rest[i + 1:])
            break
        else:
            cmd_args.append(token)
        i += 1

    config = get_config()
    if not config.include_global():
        config.load(include_global=True)
    try:
        config.add_server(
            name,
            command=command,
            args=cmd_args,
            env=env or None,
            url=url,
            auto_connect=auto_connect,
        )
        config.save()
    except ValueError as e:
        return Text.from_markup(f"[red]{e}[/]")

    body_lines: list[Text] = [
        Text.from_markup(f"[green]✓[/] added [bold]{name}[/]"),
    ]
    if command:
        body_lines.append(Text.from_markup(
            f"  [dim]stdio · {command} {' '.join(cmd_args)}[/]".rstrip()
        ))
    if url:
        body_lines.append(Text.from_markup(f"  [dim]sse · {url}[/]"))
    if auto_connect:
        body_lines.append(Text.from_markup("  [cyan]auto-connect[/] [dim]enabled[/]"))
    body_lines.append(Text(""))
    body_lines.append(Text.from_markup(
        f"  [dim]start it with[/] [cyan]/mcp connect {name}[/]"
    ))
    if not state.global_mcp:
        body_lines.append(Text.from_markup(
            "  [yellow]note:[/] global scope is off so this server is hidden — "
            "[cyan]/mcp global on[/] to show it"
        ))

    return Panel(
        Group(*body_lines),
        title="[bold green]○ server added[/]",
        border_style="green",
        padding=(1, 2),
    )


def _cmd_remove(args: list[str]):
    if not args:
        return Text.from_markup("[red]Usage:[/] [cyan]/mcp remove <name>[/]")

    name = args[0]
    if mcp_registry.is_connected(name):
        mcp_registry.disconnect(name)

    config = get_config()
    if not config.include_global():
        config.load(include_global=True)
    try:
        removed = config.remove_server(name)
    except ValueError as e:
        return Text.from_markup(f"[yellow]{e}[/]")
    if removed:
        config.save()
        return Text.from_markup(f"[green]✓[/] removed [bold]{name}[/]")
    return Text.from_markup(f"[red]Server '{name}' not found.[/]")


# ── /mcp connect / disconnect ────────────────────────────────────────────

def _cmd_connect(args: list[str]):
    if not args:
        return Text.from_markup("[red]Usage:[/] [cyan]/mcp connect <name>[/]")

    name = args[0]
    config = get_config()
    server_cfg = config.get_server(name)
    if server_cfg is None:
        if not config.include_global():
            probe = MCPConfig()
            probe.load(include_global=True)
            if probe.get_server(name) is not None:
                src = probe.get_source(name) or "global"
                label = _SOURCE_LABELS.get(src, src)
                return Text.from_markup(
                    f"[yellow]'{name}' is defined in [bold]{label}[/] but global scope is off.[/]\n"
                    f"  [cyan]/mcp global on[/] then [cyan]/mcp connect {name}[/]"
                )
        return Text.from_markup(
            f"[red]Server '{name}' not found.[/] "
            f"[dim]See[/] [cyan]/mcp list[/]"
        )

    error = mcp_registry.connect(name, server_cfg)
    if error:
        return Text.from_markup(
            f"[red]✗ failed to connect[/] [bold]{name}[/]\n  [dim]{error}[/]"
        )

    tools = mcp_registry.get_server_tools(name)
    count = len(tools)
    sample = ", ".join(f"[cyan]mcp__{name}__{t.name}[/]" for t in tools[:4])
    if count > 4:
        sample += " [dim]…[/]"
    body = Group(
        Text.from_markup(f"[green]✓[/] [bold]{name}[/] connected — [green]{count} tools[/]"),
        Text.from_markup(f"  [dim]tools:[/] {sample}") if sample else Text(""),
    )
    return Panel(body, border_style="green", padding=(0, 1))


def _cmd_disconnect(args: list[str]):
    if not args:
        return Text.from_markup("[red]Usage:[/] [cyan]/mcp disconnect <name>[/]")
    name = args[0]
    error = mcp_registry.disconnect(name)
    if error:
        return Text.from_markup(f"[red]{error}[/]")
    return Text.from_markup(f"[green]✓[/] [bold]{name}[/] disconnected")


# ── /mcp reload ──────────────────────────────────────────────────────────

def _cmd_reload():
    config = reload_config()
    servers = config.list_servers()
    auto = config.get_auto_connect()
    banner = Panel(
        Text.from_markup(
            f"[green]✓[/] config reloaded — "
            f"[bold]{len(servers)}[/] servers · [cyan]{len(auto)}[/] auto-connect"
        ),
        border_style="green",
        padding=(0, 1),
    )
    return Group(banner, _cmd_list())
