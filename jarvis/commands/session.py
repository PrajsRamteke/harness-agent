"""/session subcommands + session resume helper."""
from typing import Optional, Tuple

from rich.markup import escape

from ..console import console, Panel, Table
from ..storage.sessions import (
    db_list_sessions, db_create_session, db_delete_session, db_load_session,
    db_rename_session,
)
from ..utils.time_fmt import _fmt_ts
from ..repl.trim import count_tool_calls, estimate_session_tokens
from .. import state


def cmd_session(arg: str):
    """Handle /session subcommands. Returns new session_id if switched, else None."""
    parts = arg.split(maxsplit=1)
    sub = parts[0] if parts else "list"
    rest = parts[1] if len(parts) > 1 else ""

    if sub in ("list", "ls", ""):
        rows = db_list_sessions()
        if not rows:
            console.print("[dim]no saved sessions yet[/]"); return None
        t = Table(show_header=True, header_style="bold cyan", box=None, padding=(0, 2))
        t.add_column("#", style="dim"); t.add_column("id", style="cyan")
        t.add_column("title"); t.add_column("msgs", justify="right")
        t.add_column("model", style="magenta"); t.add_column("updated", style="dim")
        for i, r in enumerate(rows, 1):
            marker = " [green]●[/]" if r["id"] == state.current_session_id else ""
            title = r["title"] or "[dim](untitled)[/]"
            t.add_row(str(i), str(r["id"]) + marker, title,
                      str(r["msg_count"]), r["model"] or "-", _fmt_ts(r["updated_at"]))
        console.print(Panel(t, title="▤  sessions", border_style="cyan"))
        sel = console.input("[cyan]resume # or id (enter to cancel, 'd <id>' to delete): [/]").strip()
        if not sel: return None
        if sel.startswith("d "):
            sid = sel[2:].strip()
            if sid.isdigit() and db_delete_session(int(sid)):
                console.print(f"[green]deleted session {sid}[/]")
            else:
                console.print("[red]not found[/]")
            return None
        target = None
        if sel.isdigit():
            i = int(sel)
            if 1 <= i <= len(rows): target = rows[i-1]["id"]
            else: target = i  # treat as raw id
        if target is None:
            console.print("[red]invalid selection[/]"); return None
        return _resume_session(target)

    if sub == "resume":
        if not rest.isdigit():
            console.print("usage: /session resume <id>"); return None
        return _resume_session(int(rest))

    if sub == "new":
        console.print("[yellow]/session new is the same as /new — use [/][cyan]/new[/][yellow] instead[/]")
        return None

    if sub == "delete":
        if not rest.isdigit():
            console.print("usage: /session delete <id>"); return None
        if db_delete_session(int(rest)):
            console.print(f"[green]deleted session {rest}[/]")
        else:
            console.print("[red]not found[/]")
        return None

    if sub == "rename":
        sid, title = parse_rename(rest)
        if not title:
            console.print("usage: /rename <new title>  ·  /session rename [#id] <new title>")
            return None
        ok, msg = rename_session(sid, title)
        console.print(f"[green]{escape(msg)}[/]" if ok else f"[red]{escape(msg)}[/]")
        return None

    if sub == "current":
        console.print(f"[cyan]current session: #{state.current_session_id}[/]")
        return None

    console.print("[red]unknown subcommand[/] — try: /session [list|resume <id>|new|delete <id>|rename [#id] <title>|current]")
    return None


def parse_rename(arg: str) -> Tuple[Optional[int], str]:
    """``"#12 New title"`` → (12, "New title"); ``"New title"`` → (None, …) = this session."""
    arg = (arg or "").strip()
    head, _, rest = arg.partition(" ")
    if head.startswith("#") and head[1:].isdigit():
        return int(head[1:]), rest.strip()
    return None, arg


def rename_session(sid: Optional[int], title: str) -> Tuple[bool, str]:
    """Rename session ``sid`` (None = the current one) → (ok, message for people)."""
    if sid is None:
        sid = state.current_session_id
    if not sid:
        return False, "no session to rename yet"
    try:
        found = db_rename_session(int(sid), title)
    except ValueError:
        return False, "the title can't be empty"
    if not found:
        return False, f"session #{sid} not found"
    from ..storage.sessions import db_get_title

    return True, f"✎ renamed session #{sid} to “{db_get_title(sid)}”"


def _resume_session(sid: int) -> Optional[int]:
    loaded = db_load_session(sid)
    if loaded is None:
        console.print(f"[red]session {sid} not found[/]"); return None
    state.messages = loaded
    state.current_session_id = sid
    state.tool_calls_count = count_tool_calls(loaded)
    state.total_in, state.total_out, state.total_tokens = estimate_session_tokens(loaded)
    console.print(f"[green]▶ resumed session #{sid} ({len(state.messages)} messages)[/]")
    # render a brief tail so the user has context
    tail = state.messages[-6:]
    for m in tail:
        cn = m["content"]
        if isinstance(cn, str):
            preview = cn[:200]
        else:
            texts = []
            for b in cn:
                if isinstance(b, dict) and b.get("type") == "text":
                    texts.append(b.get("text", ""))
            preview = (" ".join(texts))[:200] or "[tool blocks]"
        console.print(f"  [dim]{m['role']}:[/] {preview}")
    return sid
