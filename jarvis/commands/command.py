"""/command slash command — manage custom user-defined prompt commands.

A custom command is a markdown prompt template in ``.harness/commands/``
(project) or ``~/.harness/commands/`` (global). Once created it triggers
directly: typing ``/pr-description fix the auth bug`` expands the template
(substituting ``$ARGUMENTS`` / ``$1..$9``) and sends it as the user message.

Syntax:
    /command                       list all custom commands
    /command list                  same as above
    /command new <name> [desc]     scaffold a new command file
    /command edit <name>           open the command file in $EDITOR
    /command show <name>           render the command's template
    /command delete <name>         delete the command file
    /command run <name> [args]     trigger explicitly (same as /<name> [args])
    /command refresh               bust the cache and re-scan
    /command global on|off         toggle global command discovery (persisted)
    /command scope project|global  default scope used by /command new
    /command export <name>         copy a project command to ~/.harness/commands/
    /command import <name>         copy a global command into .harness/commands/

Direct trigger (handled by dispatch as a fallback for unknown slashes):
    /<name> [args]                 expand and send — built-ins always win
"""
from __future__ import annotations

import os
import subprocess

from rich.markup import escape

from ..console import console, Panel, Markdown
from ..storage import commands as cc
from .. import state


# Session-level: which scope `/command new` writes to (mirrors /agent scope).
_NEW_SCOPE: str = "project"


def _render_commands(rows: list[dict]) -> None:
    if not rows:
        scope = "global" if state.global_commands else "project"
        console.print(Panel(
            "(no {0} commands found — run /command new <name>, or drop a\n"
            " markdown prompt into .harness/commands/<name>.md)".format(scope),
            title="⌘ Commands",
            border_style="cyan",
        ))
        return
    lines = []
    for r in rows:
        tag = " [dim]\\[global][/]" if r.get("scope") == "global" else ""
        hint = f" [dim]{escape(r['argument_hint'])}[/]" if r.get("argument_hint") else ""
        lines.append(f"[bold cyan]/{r['name']}[/]{hint}{tag}  [dim]{escape(r['description'])}[/]")
        lines.append(f"  [dim]▣ {r.get('source_tag') or r.get('source_dir', '')}[/]")
        lines.append("")
    scope_label = "◎ global" if state.global_commands else "▣ project"
    console.print(Panel(
        "\n".join(lines).rstrip(),
        title=f"⌘ Commands ({len(rows)}, {scope_label})",
        border_style="cyan",
    ))
    console.print("  [dim]trigger directly: /<name> \\[args] — e.g. /pr-description extra notes[/]")
    if not state.global_commands:
        gc = cc.global_count()
        if gc:
            console.print(f"  [dim]({gc} global commands hidden — /command global on to show)[/]")


def try_custom_command(cmd: str, arg: str):
    """Expand ``/<name> [args]`` when <name> is a discovered custom command.

    Returns the expanded prompt text to send, or None when no custom command
    matches. Called by dispatch as the last step before 'unknown command'.
    """
    name = cmd.lstrip("/").strip().lower()
    if not name:
        return None
    rec = cc.find_command(name)
    if not rec:
        return None
    expanded = cc.expand_command(name, arg)
    if expanded:
        tag = " (global)" if rec.get("scope") == "global" else ""
        console.print(f"[dim]⌘ /{name}{tag} → sending expanded prompt[/]")
    return expanded


def handle_command(cmd: str, arg: str):
    """Route /command. Returns (handled, send_text_or_None)."""
    if cmd not in ("/command", "/commands"):
        return False, None

    parts = arg.split(maxsplit=1)
    sub = parts[0].lower() if parts else ""
    rest = parts[1].strip() if len(parts) > 1 else ""

    if sub == "" or sub == "list":
        _render_commands(cc.list_commands())
        return True, None

    if sub == "refresh":
        cc.invalidate_cache()
        rows = cc.discover_commands(force=True)
        scope = "global" if state.global_commands else "project"
        console.print(f"[green]✓[/] Re-scanned — [cyan]{len(rows)}[/] {scope} commands available")
        return True, None

    if sub == "global":
        if rest.lower() in ("on", "off"):
            old = state.global_commands
            state.global_commands = (rest.lower() == "on")
            if old != state.global_commands:
                state.save_commands_config()
            cc.invalidate_cache()
            rows = cc.discover_commands(force=True)
            label = "◎ global (project + global)" if state.global_commands else "▣ project-only"
            console.print(
                f"[green]✓[/] Command scope set to [bold]{label}[/] — "
                f"[cyan]{len(rows)}[/] commands visible"
            )
            _render_commands(rows)
            return True, None
        label = "◎ on" if state.global_commands else "▣ off"
        console.print(f"[cyan]Global commands:[/] {label}  (/command global on|off)")
        return True, None

    if sub == "scope":
        global _NEW_SCOPE
        if rest.lower() in ("project", "global"):
            _NEW_SCOPE = rest.lower()
            console.print(f"[green]✓[/] /command new will now write to [bold]{_NEW_SCOPE}[/] scope")
        else:
            console.print(f"[cyan]/command new[/] scope: [bold]{_NEW_SCOPE}[/]  (/command scope project|global)")
        return True, None

    if sub == "new":
        if not rest:
            console.print("[red]usage:[/] /command new <name> [description]")
            return True, None
        bits = rest.split(maxsplit=1)
        name = bits[0]
        desc = bits[1] if len(bits) > 1 else ""
        ok, msg = cc.scaffold_command(name, scope=_NEW_SCOPE, description=desc)
        if ok:
            clean = name.strip().lstrip("/").lower()
            console.print(
                f"[green]✓[/] Created [cyan]{msg}[/]\n"
                f"  [dim]edit the template, then trigger with /{clean} [args][/]"
            )
            editor = os.environ.get("EDITOR")
            if editor:
                console.print(f"  [dim]edit with: $EDITOR ({editor}) {msg}  — or /command edit {clean}[/]")
        else:
            console.print(f"[red]✗ {msg}[/]")
        return True, None

    if sub == "edit":
        if not rest:
            console.print("[red]usage:[/] /command edit <name>")
            return True, None
        rec = cc.find_command(rest)
        if not rec:
            console.print(f"[red]command '{rest}' not found[/]")
            return True, None
        path = rec["path"]
        editor = os.environ.get("EDITOR")
        if editor:
            try:
                subprocess.Popen([editor, path])
                console.print(f"[green]✓[/] opened in $EDITOR: [cyan]{path}[/]")
            except Exception as e:
                console.print(f"[yellow]could not launch $EDITOR ({editor}): {e}[/]")
                console.print(f"  [dim]path: {path}[/]")
        else:
            console.print(f"[cyan]{path}[/]  [dim](\\$EDITOR not set)[/]")
        return True, None

    if sub == "show":
        if not rest:
            console.print("[red]usage:[/] /command show <name>")
            return True, None
        rec = cc.find_command(rest)
        if not rec:
            console.print(f"[red]command '{rest}' not found[/]")
            return True, None
        body = rec.get("_body") or ""
        title = f"⌘ Command: /{rec['name']}  [dim]({rec.get('scope')})[/]"
        console.print(Panel(Markdown(body or "*(empty template)*"), title=title, border_style="cyan"))
        console.print(f"  [dim]▣ {rec['path']}[/]")
        return True, None

    if sub in ("delete", "rm", "remove"):
        if not rest:
            console.print("[red]usage:[/] /command delete <name>")
            return True, None
        ok, msg = cc.delete_command(rest)
        if ok:
            console.print(f"[green]✓[/] deleted [cyan]{msg}[/]")
        else:
            console.print(f"[red]✗ {msg}[/]")
        return True, None

    if sub == "export":
        if not rest:
            console.print("[red]usage:[/] /command export <name>")
            return True, None
        res = cc.export_command_to_global(rest)
        _report_transfer(res, "exported to global")
        return True, None

    if sub == "import":
        if not rest:
            console.print("[red]usage:[/] /command import <name>")
            return True, None
        res = cc.import_command_to_project(rest)
        _report_transfer(res, "imported to project")
        return True, None

    if sub == "run":
        if not rest:
            console.print("[red]usage:[/] /command run <name> [args]")
            return True, None
        bits = rest.split(maxsplit=1)
        name = bits[0].lstrip("/")
        args = bits[1] if len(bits) > 1 else ""
        expanded = try_custom_command("/" + name, args)
        if expanded is None:
            _suggest(name)
            return True, None
        return True, expanded

    # bare `/command <name> [args]` → treat as run
    expanded = try_custom_command("/" + sub, rest)
    if expanded is not None:
        return True, expanded
    _suggest(sub)
    return True, None


def _suggest(name: str) -> None:
    all_cmds = cc.list_commands()
    matches = [c for c in all_cmds if name.lower() in c["name"]]
    if matches:
        console.print(f"[yellow]'{name}' didn't match any command exactly.[/] Did you mean:")
        for m in matches:
            tag = " [dim]\\[global][/]" if m.get("scope") == "global" else ""
            console.print(f"  [cyan]/{m['name']}[/]{tag} — {escape(m['description'])}")
    elif all_cmds:
        avail = ", ".join("/" + c["name"] for c in all_cmds[:12])
        console.print(f"[red]command '{name}' not found.[/] Available: {avail}")
    else:
        console.print(f"[red]command '{name}' not found.[/] No custom commands yet — /command new <name>")


def _report_transfer(res: dict, verb: str) -> None:
    if res.get("error"):
        console.print(f"[red]✗ {res['error']}[/]")
    elif res.get("added"):
        console.print(f"[green]✓[/] {verb}: [cyan]{res['path']}[/]")
    else:
        console.print(f"[yellow]skipped[/] — already exists at [cyan]{res.get('path', '?')}[/]")


# Re-exported helpers so the TUI can learn / set the /command new scope.
def get_new_scope() -> str:
    return _NEW_SCOPE


def set_new_scope(scope: str) -> None:
    global _NEW_SCOPE
    if scope in ("project", "global"):
        _NEW_SCOPE = scope
