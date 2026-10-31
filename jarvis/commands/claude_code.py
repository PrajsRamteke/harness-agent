"""``/claude-code [status|use|login|models]`` — the Claude Code CLI provider as text.

The TUI opens ``tui/claude_code_modal.py`` for the bare command; the web opens
its Providers dialog on the Claude Code row. This is the legacy-REPL / typed form.
"""
from __future__ import annotations

import subprocess

from rich.markup import escape
from rich.panel import Panel

from .. import state
from ..auth import claude_agent as ca
from ..console import console


def _status_text(s: ca.Status) -> str:
    def mark(ok: bool) -> str:
        return "[green]✓[/]" if ok else "[yellow]✗[/]"

    lines = [
        f"{mark(s.cli_ok)} Claude Code {escape(s.cli_version or '')} [dim]{escape(s.cli_path or 'not found')}[/]",
        f"{mark(bool(s.sdk_version))} Claude Agent SDK {escape(s.sdk_version or '')}",
        f"{mark(s.logged_in)} Signed in [dim]{escape(s.account_line() or 'no')}[/]",
    ]
    code, msg, fix = s.problem()
    if code:
        lines.append(f"\n[yellow]{escape(msg)}[/]\nRun: [bold]{escape(fix)}[/]")
    else:
        active = state.provider == "claude_agent"
        lines.append("\n[green]In use[/]" if active else "\n[dim]Ready —[/] /claude-code use")
    lines.append("\n[dim]Claude Code makes every request itself, signed in with your account. "
                 "Jarvis never reads or stores that login.[/]")
    return "\n".join(lines)


def handle_claude_code(arg: str) -> None:
    sub = (arg or "").strip().lower()
    if sub in ("", "status", "check"):
        console.print(Panel(_status_text(ca.refresh()), title="◆ Claude Code CLI", border_style="cyan"))
        return
    if sub in ("use", "on", "switch"):
        from .control import _handle_provider

        _handle_provider("claude_agent")
        return
    if sub in ("models", "refresh"):
        if ca.refresh().ready and ca.refresh_models():
            console.print("[green]✓[/] " + " · ".join(m["id"] for m in ca.models()))
        else:
            console.print(f"[yellow]{escape(ca.status().problem()[1] or 'Claude Code did not answer')}[/]")
        return
    if sub in ("login", "signin", "sign-in"):
        s = ca.refresh()
        if not s.cli_path:
            console.print(f"[yellow]Install Claude Code first:[/] {ca.CLI_INSTALL_CMD}")
            return
        # Anthropic's own sign-in, on this terminal (legacy REPL only — the TUI
        # suspends itself for it). Jarvis doesn't read what's shown or typed.
        try:
            subprocess.run([s.cli_path, "auth", "login"], env=ca._probe_env())
        except Exception as e:  # noqa: BLE001
            console.print(f"[red]couldn't start it: {e}[/] — run [bold]{ca.LOGIN_CMD}[/] in a terminal")
            return
        console.print(Panel(_status_text(ca.refresh()), title="◆ Claude Code CLI", border_style="cyan"))
        return
    console.print("[dim]usage: /claude-code [status|use|login|models][/]")
