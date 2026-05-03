"""/lesson slash command — view, search, add, delete agent lesson-memory."""
from ..console import console, Panel
from ..storage import lessons as ls


def _render(rows, title):
    if not rows:
        console.print(Panel("(no lessons)", title=f"🧠 {title}", border_style="magenta"))
        return
    lines = []
    for r in rows:
        tag = f" [dim][{', '.join(r.get('tags', []))}][/]" if r.get("tags") else ""
        lines.append(f"[magenta]#{r['id']}[/] [dim]hits={r.get('hits',0)}[/]{tag}  "
                     f"[bold]{r['task']}[/] → {r['lesson']}")
    console.print(Panel("\n".join(lines),
                        title=f"🧠 {title} ({len(rows)})", border_style="magenta"))


def handle_lesson(cmd: str, arg: str):
    """Syntax:
       /lesson                       → list all
       /lesson search <query>        → search
       /lesson add <task> :: <lesson> [:: tag1,tag2]
       /lesson del <id>              → delete
       /lesson clear                 → wipe all (confirm)
    """
    if cmd not in ("/lesson", "/lessons"):
        return False, None

    parts = arg.split(maxsplit=1)
    sub = parts[0].lower() if parts else ""
    rest = parts[1] if len(parts) > 1 else ""

    if sub == "" or sub == "list":
        _render(ls.list_lessons(), "lessons")
    elif sub == "search":
        if not rest.strip():
            console.print("[red]usage: /lesson search <query>[/]"); return True, None
        _render(ls.search(rest.strip(), limit=10), f"search: {rest.strip()}")
    elif sub == "add":
        chunks = [c.strip() for c in rest.split("::")]
        if len(chunks) < 2 or not chunks[0] or not chunks[1]:
            console.print("[red]usage: /lesson add <task> :: <lesson> [:: tag1,tag2][/]")
            return True, None
        tags = [t.strip() for t in chunks[2].split(",")] if len(chunks) >= 3 else []
        s = ls.add_lesson(chunks[0], chunks[1], tags)
        console.print(f"[green]✓ saved #{s['id']}: {s['task']} → {s['lesson']}[/]")
    elif sub in ("del", "delete", "rm"):
        try: sid = int(rest.strip())
        except ValueError:
            console.print("[red]usage: /lesson del <id>[/]"); return True, None
        ok = ls.delete_lesson(sid)
        console.print(f"[green]✓ deleted #{sid}[/]" if ok else f"[yellow]no lesson #{sid}[/]")
    elif sub == "clear":
        try:
            confirm = console.input("[yellow]wipe all lessons? type 'yes': [/]").strip().lower()
        except (EOFError, KeyboardInterrupt, RuntimeError):
            console.print("[dim](interactive confirm not available in TUI — skipping)[/]")
            confirm = ""
        if confirm == "yes":
            n = ls.clear_all()
            console.print(f"[green]✓ cleared {n} lesson(s)[/]")
        else:
            console.print("[dim]cancelled[/]")
    else:
        console.print("[red]usage: /lesson [list|search <q>|add <task> :: <lesson> [:: tags]|del <id>|clear][/]")
    return True, None
