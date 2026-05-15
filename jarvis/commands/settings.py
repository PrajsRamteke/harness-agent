"""``/settings`` — view, set, reset, or open the unified preferences file."""

from __future__ import annotations

import json
import os
import shlex
import subprocess
from typing import Any

from rich.console import Group
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from ..console import console
from ..storage.settings import (
    DEFAULTS,
    SETTINGS_FILE,
    get_settings,
    reload_settings,
)
from .. import state


# ── helpers ──────────────────────────────────────────────────────────────

def _flatten(data: dict, prefix: str = "") -> list[tuple[str, Any]]:
    out: list[tuple[str, Any]] = []
    for k, v in data.items():
        path = f"{prefix}.{k}" if prefix else k
        if isinstance(v, dict):
            out.extend(_flatten(v, path))
        else:
            out.append((path, v))
    return out


_DESCRIPTIONS: dict[str, str] = {
    "model":         "Active model identifier",
    "theme":         "Visual theme — red or purple",
    "skills.global": "Include skills from ~/.config/*/skills (otherwise project-only)",
    "mcp.global":    "Include MCP servers from Claude Code / OpenCode / Cursor / Windsurf / VS Code",
    "think.mode":    "Enable extended thinking",
    "think.effort":  "Thinking effort — none / low / medium / high",
}


def _format_value(v: Any) -> Text:
    if isinstance(v, bool):
        return Text("true" if v else "false", style="bold green" if v else "bold red")
    if isinstance(v, str):
        if v == "":
            return Text("(unset — using default)", style="dim italic")
        return Text(v, style="white")
    if v is None:
        return Text("null", style="dim")
    return Text(json.dumps(v), style="white")


# ── /settings (root view) ────────────────────────────────────────────────

def _render_settings_panel() -> Panel:
    settings = get_settings()
    merged = settings.all()
    overrides = settings.overrides()

    t = Table(
        show_header=True,
        header_style="bold cyan",
        box=None,
        padding=(0, 2),
        expand=True,
    )
    t.add_column("key",         no_wrap=True, style="cyan")
    t.add_column("value",       no_wrap=True)
    t.add_column("default",     no_wrap=True, style="dim")
    t.add_column("description", overflow="fold", style="white")

    rows = _flatten(merged)
    defaults_flat = dict(_flatten(DEFAULTS))
    overrides_flat = dict(_flatten(overrides))

    for path, value in rows:
        default_val = defaults_flat.get(path, "")
        is_override = path in overrides_flat
        key_text = Text(path, style="bold cyan" if is_override else "cyan")
        value_text = _format_value(value)
        default_text = _format_value(default_val) if value != default_val else Text("—", style="dim")
        desc = _DESCRIPTIONS.get(path, "")
        t.add_row(key_text, value_text, default_text, desc)

    footer = Text.from_markup(
        f"[dim]file:[/] {SETTINGS_FILE}\n"
        f"[dim]·[/] [cyan]/settings set <key> <value>[/]  "
        f"[dim]·[/] [cyan]/settings reset <key>[/]  "
        f"[dim]·[/] [cyan]/settings edit[/]  "
        f"[dim]·[/] [cyan]/settings reload[/]"
    )

    return Panel(
        Group(t, Text(""), footer),
        title="[bold yellow]⚙  Settings[/]",
        subtitle=f"[dim]{len(overrides_flat)} overrides · {len(rows)} keys[/]",
        border_style="yellow",
        padding=(1, 2),
    )


# ── subcommands ──────────────────────────────────────────────────────────

def _cmd_get(args: list[str]):
    if not args:
        return Text.from_markup("[red]Usage:[/] [cyan]/settings get <key>[/]")
    path = args[0]
    val = get_settings().get(path)
    if val is None and path not in {p for p, _ in _flatten(DEFAULTS)}:
        return Text.from_markup(f"[red]Unknown key:[/] [bold]{path}[/]")
    label = Text.from_markup(f"[cyan]{path}[/] = ")
    label.append(_format_value(val))
    return label


def _cmd_set(args: list[str]):
    if len(args) < 2:
        return Text.from_markup(
            "[red]Usage:[/] [cyan]/settings set <key> <value>[/] "
            "[dim](e.g. /settings set mcp.global true)[/]"
        )
    path = args[0]
    raw = " ".join(args[1:])
    # JSON-style values are parsed if possible — otherwise treat as a string.
    try:
        value: Any = json.loads(raw)
    except json.JSONDecodeError:
        value = raw

    try:
        coerced = get_settings().set(path, value)
    except ValueError as e:
        return Text.from_markup(f"[red]{e}[/]")

    # Re-apply to live state so the change takes effect immediately.
    state.apply_settings_to_state()

    label = Text.from_markup(f"[green]✓[/] [cyan]{path}[/] = ")
    label.append(_format_value(coerced))
    return Panel(label, border_style="green", padding=(0, 1))


def _cmd_reset(args: list[str]):
    if not args:
        return Text.from_markup("[red]Usage:[/] [cyan]/settings reset <key>[/]")
    path = args[0]
    default = get_settings().reset(path)
    state.apply_settings_to_state()
    label = Text.from_markup(f"[green]✓[/] reset [cyan]{path}[/] → ")
    label.append(_format_value(default))
    return Panel(label, border_style="green", padding=(0, 1))


def _cmd_reload(_args: list[str]):
    reload_settings()
    state.apply_settings_to_state()
    return Group(
        Panel(
            Text.from_markup(
                f"[green]✓[/] reloaded from [dim]{SETTINGS_FILE}[/] and re-applied to running session"
            ),
            border_style="green",
            padding=(0, 1),
        ),
        _render_settings_panel(),
    )


def _cmd_path(_args: list[str]):
    exists = SETTINGS_FILE.exists()
    status = "[green]exists[/]" if exists else "[yellow]not created yet — will be written on first /settings set[/]"
    return Panel(
        Text.from_markup(f"{SETTINGS_FILE}  {status}"),
        title="[bold yellow]⚙  settings.json[/]",
        border_style="yellow",
        padding=(0, 1),
    )


def _cmd_edit(_args: list[str]):
    editor = os.environ.get("EDITOR") or os.environ.get("VISUAL") or "nano"
    if not SETTINGS_FILE.exists():
        # Touch the file with current defaults so the user can edit something.
        get_settings().save()
    try:
        subprocess.run([editor, str(SETTINGS_FILE)], check=False)
    except FileNotFoundError:
        return Text.from_markup(
            f"[red]editor not found: [bold]{editor}[/].[/] "
            f"[dim]set $EDITOR or open[/] [cyan]{SETTINGS_FILE}[/] [dim]manually[/]"
        )
    # Re-apply after edit
    reload_settings()
    state.apply_settings_to_state()
    return Group(
        Panel(
            Text.from_markup("[green]✓[/] settings reloaded after edit"),
            border_style="green",
            padding=(0, 1),
        ),
        _render_settings_panel(),
    )


# ── public entry point ───────────────────────────────────────────────────

def _usage() -> Panel:
    table = Table.grid(padding=(0, 2))
    table.add_column(style="cyan", no_wrap=True)
    table.add_column(style="white")
    rows = [
        ("/settings",                  "show all preferences"),
        ("/settings get <key>",        "show one value"),
        ("/settings set <key> <val>",  "set a value (auto-saved + auto-applied)"),
        ("/settings reset <key>",      "remove an override, restore default"),
        ("/settings reload",           "re-read settings.json from disk"),
        ("/settings edit",             "open settings.json in $EDITOR"),
        ("/settings path",             "print the settings.json location"),
    ]
    for k, v in rows:
        table.add_row(k, v)
    return Panel(
        table,
        title="[bold yellow]⚙  /settings[/]",
        subtitle=f"[dim]{SETTINGS_FILE}[/]",
        border_style="yellow",
        padding=(1, 2),
    )


def handle_settings(cmd: str, arg: str):
    """Route ``/settings <sub>`` to its handler. Returns (handled, send_to_llm)."""
    if cmd != "/settings":
        return False, None

    if not arg.strip():
        console.print(_render_settings_panel())
        return True, None

    parts = shlex.split(arg)
    sub = parts[0].lower()
    sub_args = parts[1:]

    if sub == "get":
        console.print(_cmd_get(sub_args))
    elif sub == "set":
        console.print(_cmd_set(sub_args))
    elif sub in ("reset", "unset"):
        console.print(_cmd_reset(sub_args))
    elif sub == "reload":
        console.print(_cmd_reload(sub_args))
    elif sub in ("path", "where"):
        console.print(_cmd_path(sub_args))
    elif sub == "edit":
        console.print(_cmd_edit(sub_args))
    elif sub in ("list", "show", "all"):
        console.print(_render_settings_panel())
    elif sub in ("help", "--help", "-h"):
        console.print(_usage())
    else:
        console.print(Text.from_markup(f"[red]Unknown subcommand:[/] [bold]{sub}[/]"))
        console.print(_usage())
    return True, None
