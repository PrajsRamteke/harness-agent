"""/skill slash command — view, search, add, delete agent skill-memory."""
from ..console import console, Panel
from ..storage import skills as sk


def _render(rows, title):
    if not rows:
        console.print(Panel("(no skills)", title=f"🧠 {title}", border_style="magenta"))
        return
    lines = []
    for r in rows:
        tag = f" [dim][{', '.join(r.get('tags', []))}][/]" if r.get("tags") else ""
        lines.append(f"[magenta]#{r['id']}[/] [dim]hits={r.get('hits',0)}[/]{tag}  "
                     f"[bold]{r['task']}[/] → {r['lesson']}")
    console.print(Panel("\n".join(lines),
                        title=f"🧠 {title} ({len(rows)})", border_style="magenta"))


def handle_skill(cmd: str, arg: str):
    """Syntax:
       /skill                        → list all
       /skill search <query>         → search
       /skill add <task> :: <lesson> [:: tag1,tag2]
       /skill del <id>               → delete
       /skill clear                  → wipe all (confirm)
    """
    if cmd not in ("/skill", "/skills"):
        return False, None

    parts = arg.split(maxsplit=1)
    sub = parts[0].lower() if parts else ""
    rest = parts[1] if len(parts) > 1 else ""

    if sub == "" or sub == "list":
        _render(sk.list_skills(), "skills")
    elif sub == "search":
        if not rest.strip():
            console.print("[red]usage: /skill search <query>[/]"); return True, None
        _render(sk.search(rest.strip(), limit=10), f"search: {rest.strip()}")
    elif sub == "add":
        chunks = [c.strip() for c in rest.split("::")]
        if len(chunks) < 2 or not chunks[0] or not chunks[1]:
            console.print("[red]usage: /skill add <task> :: <lesson> [:: tag1,tag2][/]")
            return True, None
        tags = [t.strip() for t in chunks[2].split(",")] if len(chunks) >= 3 else []
        s = sk.add_skill(chunks[0], chunks[1], tags)
        console.print(f"[green]✓ saved #{s['id']}: {s['task']} → {s['lesson']}[/]")
    elif sub in ("del", "delete", "rm"):
        try: sid = int(rest.strip())
        except ValueError:
            console.print("[red]usage: /skill del <id>[/]"); return True, None
        ok = sk.delete_skill(sid)
        console.print(f"[green]✓ deleted #{sid}[/]" if ok else f"[yellow]no skill #{sid}[/]")
    elif sub == "clear":
        try:
            confirm = console.input("[yellow]wipe all skills? type 'yes': [/]").strip().lower()
        except (EOFError, KeyboardInterrupt, RuntimeError):
            console.print("[dim](interactive confirm not available in TUI — skipping)[/]")
            confirm = ""
        if confirm == "yes":
            n = sk.clear_all()
            console.print(f"[green]✓ cleared {n} skill(s)[/]")
        else:
            console.print("[dim]cancelled[/]")
    else:
        console.print("[red]usage: /skill [list|search <q>|add <task> :: <lesson> [:: tags]|del <id>|clear][/]")
    return True, None
