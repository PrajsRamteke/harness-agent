"""/memory slash command — view, add, delete, or clear stored personal facts."""
from ..console import console, Panel
from ..storage import memory as mem


def _render_table() -> None:
    facts = mem.list_facts()
    if not facts:
        console.print(Panel("(memory is empty)", title="🧠 memory", border_style="cyan"))
        return
    lines = [f"[cyan]#{f['id']}[/]  {f['text']}" for f in facts]
    console.print(Panel("\n".join(lines), title=f"🧠 memory ({len(facts)})", border_style="cyan"))


def handle_memory(cmd: str, arg: str):
    """Return (handled, None). Syntax:
       /memory                 → list all
       /memory add <text>      → save a fact
       /memory del <id>        → delete by id
       /memory clear           → wipe all (confirm)
    """
    if cmd != "/memory":
        return False, None

    parts = arg.split(maxsplit=1)
    sub = parts[0].lower() if parts else ""
    rest = parts[1] if len(parts) > 1 else ""

    if sub == "" or sub == "list":
        _render_table()
    elif sub == "add":
        if not rest.strip():
            console.print("[red]usage: /memory add <text>[/]")
        else:
            f = mem.add_fact(rest.strip())
            console.print(f"[green]✓ saved #{f['id']}: {f['text']}[/]")
    elif sub in ("del", "delete", "rm"):
        try: fid = int(rest.strip())
        except ValueError:
            console.print("[red]usage: /memory del <id>[/]")
            return True, None
        ok = mem.delete_fact(fid)
        console.print(f"[green]✓ deleted #{fid}[/]" if ok else f"[yellow]no fact #{fid}[/]")
    elif sub == "clear":
        try:
            confirm = console.input("[yellow]wipe all memory? type 'yes': [/]").strip().lower()
        except (EOFError, KeyboardInterrupt):
            confirm = ""
        if confirm == "yes":
            n = mem.clear_all()
            console.print(f"[green]✓ cleared {n} fact(s)[/]")
        else:
            console.print("[dim]cancelled[/]")
    else:
        console.print("[red]usage: /memory [list|add <text>|del <id>|clear][/]")
    return True, None
