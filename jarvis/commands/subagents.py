"""/team <task> — run a task with parallel subagents · /subagents — their settings."""
from __future__ import annotations

from rich.markup import escape

from ..console import console

TEAM_PROMPT = (
    "Do this task with parallel subagents (spawn_agents). Split it into 2-6 independent "
    "parts — one agent per part, each with a complete, self-contained brief (files/areas it "
    "covers, constraints, what to report) and mode 'edit' only for agents that must change "
    "files (give each its own files). Run them in ONE spawn_agents call, then check their "
    "reports and give me one combined answer. If the task truly can't be split, say why and "
    "do it yourself.\n\nTASK:\n{task}"
)

_KEYS = {
    "max": ("subagents.max_parallel", "agents running at once", (1, 6)),
    "parallel": ("subagents.max_parallel", "agents running at once", (1, 6)),
    "steps": ("subagents.max_steps", "model requests per agent", (4, 200)),
    "timeout": ("subagents.timeout_min", "minutes per agent", (1, 120)),
}


def _status() -> None:
    from ..storage.settings import get_settings
    from ..subagents import running_teams

    s = get_settings()
    on = bool(s.get("subagents.enabled", True))
    console.print(
        f"[bold]Parallel subagents[/] {'[green]on[/]' if on else '[red]off[/]'} · "
        f"up to {s.get('subagents.max_parallel', 6)} at once · "
        f"{s.get('subagents.max_steps', 40)} steps · {s.get('subagents.timeout_min', 20)} min each"
    )
    teams = running_teams()
    for t in teams:
        c = t.counts()
        console.print(f"  [cyan]⇉[/] {len(t.agents)} agents — {c['running']} running, "
                      f"{c['done']} done, {c['queued']} queued")
    console.print(
        "[dim]/team <task> — split a task across agents · /subagents on|off · "
        "/subagents max <1-6> · /subagents steps <n> · /subagents timeout <minutes> · "
        "/subagents stop[/]"
    )


def handle_subagents(c: str, arg: str) -> tuple[bool, str | None]:
    """Returns (handled, prompt to send or None)."""
    if c == "/team":
        task = arg.strip()
        if not task:
            console.print("[yellow]usage:[/] /team <task> — Jarvis splits it across 2-6 "
                          "agents working in parallel")
            return True, None
        from ..subagents import enabled

        if not enabled():
            console.print("[yellow]Parallel subagents are off — /subagents on turns them on.[/]")
            return True, None
        return True, TEAM_PROMPT.format(task=task)

    if c not in ("/subagents", "/subagent"):
        return False, None
    from ..storage.settings import get_settings

    parts = arg.split()
    sub = parts[0].lower() if parts else ""
    s = get_settings()
    if not sub or sub in ("status", "show"):
        _status()
    elif sub in ("on", "off", "enable", "disable"):
        on = sub in ("on", "enable")
        s.set("subagents.enabled", on)
        try:
            from ..repl.system import invalidate_system_cache

            invalidate_system_cache()
        except Exception:
            pass
        console.print(f"[green]✓[/] parallel subagents {'on' if on else 'off'}")
    elif sub == "stop":
        from ..subagents import running_teams, stop

        teams = running_teams()
        for t in teams:
            stop(t.id)
        console.print(f"[green]✓[/] stopped {len(teams)} running team(s)" if teams
                      else "[dim]no agents are running[/]")
    elif sub in _KEYS:
        path, label, (lo, hi) = _KEYS[sub]
        if len(parts) < 2:
            console.print(f"{label}: {s.get(path)}  [dim](/subagents {sub} <{lo}-{hi}>)[/]")
        else:
            try:
                val = s.set(path, parts[1])
            except ValueError as e:
                console.print(f"[red]{escape(str(e))}[/]")
            else:
                console.print(f"[green]✓[/] {label}: {val}")
    else:
        console.print(f"[red]unknown:[/] /subagents {escape(sub)}")
        _status()
    return True, None
