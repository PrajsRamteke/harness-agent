"""``/local-models`` — models running on this computer (Ollama, LM Studio …).

In the TUI the bare command opens the dialog (``tui/local_modal.py``); these
handlers are the text form (legacy REPL, and subcommands typed anywhere):

  /local-models                    list servers and their models
  /local-models scan               look again
  /local-models add <url> [name]   add a server (another computer, a port, …)
  /local-models remove <id>        remove an added server
  /local-models context <size>     window asked for: 8k 16k 32k 64k 128k
  /local-models use <server> [model]
"""
from __future__ import annotations

import re

from rich.markup import escape
from rich.panel import Panel
from rich.table import Table

from .. import state
from ..console import console

NAMES = ("/local-models", "/localmodels")


def _parse_size(text: str) -> int | None:
    m = re.fullmatch(r"\s*(\d+(?:\.\d+)?)\s*([kK])?\s*", text or "")
    if not m:
        return None
    n = float(m.group(1))
    return int(n * 1024) if m.group(2) else int(n)


def _list() -> None:
    from ..auth import local_models as lm

    if not lm.enabled():
        console.print("[dim]local models are off (HARNESS_LOCAL_MODELS=0)[/]")
        return
    data = lm.public(state.provider, state.MODEL)
    t = Table(show_header=False, box=None, pad_edge=False)
    t.add_column(style="cyan")
    t.add_column()
    t.add_column(style="dim")
    running = [s for s in data["servers"] if s["status"] == "online"]
    for s in running:
        t.add_row(f"[bold]{escape(s['name'])}[/]", f"[green]● running[/] [dim]{escape(s['host'])}[/]",
                  f"/provider {s['server']}")
        for m in s["models"]:
            mark = "[green]●[/] " if m["active"] else "  "
            t.add_row(f"{mark}{escape(m['id'])}", escape(m["label"]), "")
    idle = [s for s in data["servers"] if s["status"] != "online" and s["status"] != "off"]
    if idle:
        t.add_row("", "", "")
        for s in idle:
            why = s["error"] or s["status"]
            t.add_row(f"[dim]{escape(s['name'])}[/]", f"[dim]{escape(why)}[/]",
                      escape(s["start"] or s["how"] or s["url"]))
    ctx = data["context"]
    title = f"⌂ local models — {data['online']} running · context {ctx // 1024}K"
    console.print(Panel(t, title=title, border_style="cyan"))
    if not running:
        console.print("[dim]Start Ollama or LM Studio and run [cyan]/local-models scan[/] — "
                      "or [cyan]/local-models add <url>[/] for a server elsewhere.[/]")


def handle_local_models(arg: str) -> None:
    from ..auth import local_models as lm

    parts = (arg or "").split()
    sub = parts[0].lower() if parts else ""
    rest = parts[1:]
    if not sub:
        _list()
        return
    if sub in ("scan", "refresh", "rescan"):
        found = lm.scan()
        n = sum(1 for r in found.values() if r.get("status") == "online")
        console.print(f"[green]✓[/] scanned · {n} running")
        _list()
        return
    if sub == "add":
        if not rest:
            console.print("[yellow]usage: /local-models add <url> [name][/]")
            return
        res = lm.add_server(" ".join(rest[1:]), rest[0])
        if res.get("ok"):
            console.print(f"[green]✓ added {escape(res['name'])}[/] [dim]({res['kind']} · "
                          f"{res.get('models', 0)} models)[/]")
        else:
            console.print(f"[red]{escape(res.get('error') or 'could not add it')}[/]")
        return
    if sub in ("remove", "rm", "delete"):
        target = rest[0] if rest else ""
        pid = target if target.startswith(lm.LOCAL_PREFIX) else lm.provider_id(target)
        if lm.remove_server(pid):
            console.print(f"[green]✓ removed {escape(target)}[/]")
        else:
            console.print(f"[yellow]no added server called {escape(target)}[/] "
                          "[dim](detected runtimes can't be removed — they come back when they run)[/]")
        return
    if sub in ("context", "ctx"):
        size = _parse_size(rest[0]) if rest else None
        if not size:
            console.print(f"[dim]context is {lm.context_setting():,} tokens — "
                          "/local-models context 32k[/]")
            return
        n = lm.set_context(size)
        console.print(f"[green]✓ local context set to {n:,} tokens[/] "
                      "[dim](Ollama reloads the model once at the new size)[/]")
        return
    if sub == "use":
        if not rest:
            console.print("[yellow]usage: /local-models use <server> [model][/]")
            return
        srv = lm.get_server(rest[0])
        if srv is None:
            console.print(f"[red]unknown local server: {escape(rest[0])}[/]")
            return
        if not lm.models(srv.provider):
            lm.scan(only=srv.provider)
        model = rest[1] if len(rest) > 1 else lm.default_model(srv.provider)
        if not model:
            console.print(f"[yellow]{escape(srv.name)} has no models Jarvis can see[/]")
            return
        from .control import _apply_model_selection

        _apply_model_selection(model, source=srv.provider)
        return
    console.print(f"[red]unknown: /local-models {escape(sub)}[/] [dim]— scan · add · remove · context · use[/]")
