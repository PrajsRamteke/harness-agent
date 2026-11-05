"""/project — the TUI side of the multi-project workspace (``storage/workspace.py``).

``/project`` (aliases ``/workspace``, ``/p``) lists, adds, links and switches the
folders the user works on. The TUI is one process with one folder, so *switching*
here means moving this process: ``fs_api.change_cwd`` (what ``/cd`` does, in full)
and then that project's most recent chat — or a fresh one. Holding several projects
at once is the web remote's job.

Everything that isn't printing lives in small functions (``switch_project``,
``project_rows``, ``find_repos`` …) so the modal (``tui/project_modal.py``) and
the tests reuse it.
"""
from __future__ import annotations

import os
import shlex
from pathlib import Path
from typing import Any, Callable

from rich.markup import escape

from ..console import console, Table
from ..storage import workspace
from ..storage.workspace import WorkspaceError

ALIASES = ("/project", "/workspace", "/p")
SUBCOMMANDS = (
    "list", "ls", "add", "rm", "remove", "link", "unlink", "write", "rename", "color",
    "startup", "settings", "scan", "group", "master", "switch", "help",
)
_SWITCH_WORDS = ("switch", "go", "open")
_TRUE = ("true", "on", "yes", "1")
_FALSE = ("false", "off", "no", "0")

# The TUI app registers ``lambda: self._busy`` so a switch is refused mid-reply.
_busy_probe: Callable[[], bool] | None = None


def set_busy_probe(fn: Callable[[], bool] | None) -> None:
    global _busy_probe
    _busy_probe = fn


def is_busy() -> bool:
    try:
        return bool(_busy_probe and _busy_probe())
    except Exception:
        return False


# ─── helpers shared with the modal ───────────────────────────────────────────

def display(path: str | Path) -> str:
    """``/Users/me/x`` → ``~/x``."""
    p = str(path)
    home = str(Path.home())
    if p == home:
        return "~"
    if p.startswith(home + os.sep):
        return "~" + p[len(home):]
    return p


def _real(path: str | Path) -> str:
    try:
        return os.path.realpath(os.path.expanduser(str(path)))
    except (OSError, ValueError):
        return str(path)


def find_project(ref: str, data: dict[str, Any] | None = None) -> dict[str, Any] | None:
    """A project by id, then exact name (case-insensitive), then 1-based position."""
    ref = (ref or "").strip()
    if not ref:
        return None
    data = data or workspace.load()
    proj = workspace.get(ref, data) or workspace.get(ref.lower(), data)
    if proj:
        return proj
    ordered = workspace.projects(data)
    named = [p for p in ordered if p["name"].lower() == ref.lower()]
    if len(named) == 1:
        return named[0]
    if ref.isdigit() and 1 <= int(ref) <= len(ordered):
        return ordered[int(ref) - 1]
    return None


def running_elsewhere(path: str | Path) -> list[dict[str, Any]]:
    """Other Jarvis processes (the web remote's instances) working in ``path``."""
    try:
        from ..web import registry

        mine = os.getpid()
        real = _real(path)
        return [
            rec for rec in registry.list_instances(prune=False)
            if int(rec.get("pid") or 0) != mine and _real(rec.get("cwd") or "") == real
        ]
    except Exception:
        return []


def project_rows(data: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    """Workspace projects in order with what the list shows.

    ``status``: ``current`` (this process) · ``running`` (another Jarvis has it) ·
    ``stopped`` · ``missing`` (the folder is gone). ``links`` = names this project
    may read.
    """
    data = data or workspace.load()
    cur = workspace.current()
    cur_id = cur["id"] if cur else None
    out = []
    for proj in workspace.projects(data):
        if not os.path.isdir(_real(proj["path"])):
            status = "missing"
        elif proj["id"] == cur_id:
            status = "current"
        elif running_elsewhere(proj["path"]):
            status = "running"
        else:
            status = "stopped"
        linked = workspace.links_of(proj, data)
        out.append({
            "project": proj, "id": proj["id"], "name": proj["name"], "color": proj["color"],
            "display": display(proj["path"]), "status": status,
            "links": [p["name"] for p in linked], "link_ids": [p["id"] for p in linked],
            "link_write": proj["link_write"], "startup": proj["startup"],
        })
    return out


def find_repos(root: str | Path, *, max_depth: int = 2, limit: int = 80) -> list[str]:
    """Git repositories in/under ``root`` (up to ``max_depth`` levels; never inside a repo)."""
    start = Path(_real(root))
    found: list[str] = []
    skip = {"node_modules", ".venv", "venv", "__pycache__", "dist", "build", "target"}

    def walk(folder: Path, depth: int) -> None:
        if len(found) >= limit:
            return
        if (folder / ".git").exists():
            found.append(str(folder))
            return
        if depth >= max_depth:
            return
        try:
            kids = sorted(e for e in folder.iterdir() if e.is_dir() and not e.is_symlink())
        except OSError:
            return
        for kid in kids:
            if kid.name.startswith(".") or kid.name in skip:
                continue
            walk(kid, depth + 1)

    walk(start, 0)
    return found


# ─── switching ───────────────────────────────────────────────────────────────

def latest_session_id(path: str) -> int | None:
    """This project's most recent chat (``None``: it has none yet)."""
    try:
        from ..storage.sessions import db_list_sessions

        for cwd in dict.fromkeys((path, _real(path))):
            rows = db_list_sessions(limit=1, cwd=cwd)
            if rows:
                return int(rows[0]["id"])
    except Exception:
        pass
    return None


def switch_project(ref: str) -> dict[str, Any]:
    """Move this process to workspace project ``ref`` (does not touch the chat).

    ``{ok, project, cwd, display, session_id, web, unchanged}`` or
    ``{ok: False, error}``. ``session_id`` is the project's newest chat to resume
    (``None``: start a new one). Refused while a reply is running.
    """
    proj = find_project(ref)
    if proj is None:
        return {"ok": False, "error": f"No project “{ref}” in your workspace — /project list shows them."}
    if is_busy():
        return {"ok": False, "error": "Jarvis is busy — wait for the reply (or press esc), then switch."}
    path = _real(proj["path"])
    if not os.path.isdir(path):
        return {"ok": False, "error": f"{display(path)} no longer exists — /project rm {proj['id']} drops it."}
    from ..web import fs_api

    res = fs_api.change_cwd(path)
    if not res.get("ok"):
        return {"ok": False, "error": res.get("error") or "Couldn't open that folder."}
    cwd = res.get("cwd") or path
    return {
        "ok": True, "project": proj, "cwd": cwd, "display": res.get("display") or display(cwd),
        "unchanged": bool(res.get("unchanged")),
        "session_id": None if res.get("unchanged") else latest_session_id(cwd),
        "web": running_elsewhere(path),
    }


def web_hint(res: dict[str, Any]) -> str:
    """``also running in the web`` note for a switch result ('' when it isn't)."""
    if not res.get("web"):
        return ""
    return (f"also running in the web remote ({res['project']['name']}) — "
            "open it from the web link's project list; this terminal works on its own copy")


def start_chat_here(console_print: Callable[[str], Any]) -> None:
    """After a switch with no chat to resume: an empty chat just moves; otherwise start fresh."""
    from .. import state

    if state.messages:
        from .history import handle_history

        handle_history("/new", "")
    else:
        from ..web.actions_api import _move_current_session

        _move_current_session(os.getcwd())


def announce_switch(res: dict[str, Any], console_print: Callable[[str], Any]) -> None:
    p = res["project"]
    console_print(f"[green]▶ project {escape(p['name'])}[/] [dim]{escape(res['display'])}[/]")
    hint = web_hint(res)
    if hint:
        console_print(f"[dim]{escape(hint)}[/]")


def do_switch(ref: str) -> bool:
    """Console version: switch, then resume the project's newest chat or start one."""
    res = switch_project(ref)
    if not res["ok"]:
        console.print(f"[red]{escape(res['error'])}[/]")
        return False
    if res.get("unchanged"):
        console.print(f"[dim]already in {escape(res['project']['name'])}[/]")
        return True
    sid = res.get("session_id")
    if sid:
        from .session import _resume_session

        _resume_session(sid)
    else:
        start_chat_here(console.print)
    announce_switch(res, console.print)
    return True


# ─── parsing ─────────────────────────────────────────────────────────────────

def _split(arg: str) -> list[str]:
    try:
        return shlex.split(arg)
    except ValueError:
        return arg.split()


def parse(arg: str) -> tuple[str, list[str]]:
    toks = _split(arg or "")
    return (toks[0].lower(), toks[1:]) if toks else ("", [])


def is_bare(arg: str) -> bool:
    return (arg or "").strip().lower() in ("", "list-modal")


def switch_target(arg: str) -> str | None:
    """``/project api`` · ``/project switch api`` → ``"api"``; anything else → ``None``."""
    sub, rest = parse(arg)
    if sub in _SWITCH_WORDS:
        return rest[0] if rest else None
    if sub and sub not in SUBCOMMANDS and len(rest) == 0:
        return sub if find_project(sub) else None
    return None


# ─── subcommands ─────────────────────────────────────────────────────────────

_STATUS = {
    "current": ("★", "yellow", "here"),
    "running": ("●", "green", "running elsewhere"),
    "stopped": ("◌", "dim", "stopped"),
    "missing": ("✗", "red", "folder missing"),
}


def _list() -> None:
    rows = project_rows()
    if not rows:
        console.print("[dim]no projects yet — /project add [path] adds the current folder[/]")
        return
    t = Table(show_header=True, header_style="bold cyan", box=None, padding=(0, 2))
    t.add_column("#", style="dim", justify="right")
    t.add_column("id", style="cyan")
    t.add_column("name")
    t.add_column("path", style="dim")
    t.add_column("status")
    t.add_column("links", style="magenta")
    for i, row in enumerate(rows, 1):
        icon, style, label = _STATUS[row["status"]]
        links = ", ".join(row["link_ids"])
        if links:
            links += f" ({row['link_write']})"
        grp = workspace.is_group(workspace.get(row["id"]))
        glyph = "◉" if grp else "■"  # ◉ = a master group (its links are its members)
        t.add_row(str(i), row["id"], f"[{row['color']}]{glyph}[/] {escape(row['name'])}", escape(row["display"]),
                  f"[{style}]{icon} {label}[/]", escape(links))
    console.print(t)
    console.print("[dim]/project <id> switches · /project add · link · unlink · write · rename · color · "
                  "startup · settings · scan[/]")


def _group(args: list[str]) -> None:
    """``/project group <name> <id>…`` — a virtual main project over existing projects."""
    if len(args) < 2:
        console.print("usage: /project group <name> <project id> [<project id>…]  "
                      "(then /project master <name> opens its shared master chat)")
        return
    name, ids = args[0], args[1:]
    group = workspace.create_group(name, ids)
    console.print(f"[green]✓ group {escape(group['name'])}[/] [dim]({group['id']}) — {len(group['links'])} "
                  f"project{'s' if len(group['links']) != 1 else ''}: {escape(', '.join(group['links']))}[/]")
    console.print(f"[dim]/project master {group['id']} opens its shared master chat · /project write ask|allow|deny "
                  "sets what it may edit[/]")


def _master(args: list[str]) -> None:
    """``/project master [<group>]`` — switch to a group's hub (its shared master chat)."""
    groups = [p for p in workspace.projects() if workspace.is_group(p)]
    if not groups:
        console.print("[dim]no master groups yet — /project group <name> <id>… creates one[/]")
        return
    if args:
        target = find_project(args[0])
        if target is None or not workspace.is_group(target):
            console.print(f"[red]no master group “{escape(args[0])}”[/] — groups: {escape(', '.join(g['id'] for g in groups))}")
            return
    elif len(groups) == 1:
        target = groups[0]
    else:
        console.print(f"usage: /project master <group> — groups: {escape(', '.join(g['id'] for g in groups))}")
        return
    do_switch(target["id"])


def _add(args: list[str]) -> None:
    paths = [a for a in args if not a.startswith("--")] or [os.getcwd()]
    paths = [os.path.abspath(os.path.expanduser(p)) for p in paths]
    out = workspace.add_many(paths)
    for proj in out["added"]:
        console.print(f"[green]✓ added {escape(proj['name'])}[/] [dim]({proj['id']}) {escape(display(proj['path']))}[/]")
    for err in out["errors"]:
        console.print(f"[red]✗ {escape(display(err['path']))}: {escape(err['error'])}[/]")
    if out["added"] and len(workspace.projects()) == 1:
        console.print("[dim]/project add <path> adds more · /project link <id> lets this project read another[/]")


def _remove(args: list[str]) -> None:
    if not args:
        console.print("usage: /project rm <id> [<id>…]")
        return
    for ref in args:
        proj = find_project(ref)
        if proj is None:
            console.print(f"[red]✗ no project “{escape(ref)}”[/]")
            continue
        try:
            workspace.remove(proj["id"])
            console.print(f"[green]✓ removed {escape(proj['name'])} from the workspace[/] [dim](the folder is untouched)[/]")
        except WorkspaceError as exc:
            console.print(f"[red]✗ {escape(str(exc))}[/]")


def _current_or_say() -> dict[str, Any] | None:
    cur = workspace.current()
    if cur is None:
        console.print("[yellow]This folder isn't in your workspace — run /project add first.[/]")
    return cur


def _link(args: list[str], *, unlink: bool) -> None:
    if not args:
        console.print(f"usage: /project {'unlink' if unlink else 'link'} <id> [<id>…]")
        return
    cur = _current_or_say()
    if cur is None:
        return
    data = workspace.load()
    links = list(cur["links"])
    for ref in args:
        other = find_project(ref, data)
        if other is None:
            console.print(f"[red]✗ no project “{escape(ref)}”[/]")
        elif other["id"] == cur["id"]:
            console.print("[red]✗ a project already reads itself[/]")
        elif unlink:
            if other["id"] in links:
                links.remove(other["id"])
                console.print(f"[green]✓ {escape(cur['name'])} no longer links {escape(other['name'])}[/]")
            else:
                console.print(f"[dim]{escape(other['name'])} wasn't linked[/]")
        elif other["id"] in links:
            console.print(f"[dim]{escape(other['name'])} is already linked[/]")
        else:
            links.append(other["id"])
            console.print(f"[green]✓ {escape(cur['name'])} can now read {escape(other['name'])}[/] "
                          f"[dim](writes: {cur['link_write']} — /project write ask|allow|deny)[/]")
    try:
        workspace.update(cur["id"], links=links)
    except WorkspaceError as exc:
        console.print(f"[red]✗ {escape(str(exc))}[/]")


def _write(args: list[str]) -> None:
    cur = _current_or_say()
    if cur is None:
        return
    if not args:
        console.print(f"writes into linked projects: [cyan]{cur['link_write']}[/]  (ask · allow · deny)")
        return
    mode = args[0].lower()
    try:
        workspace.update(cur["id"], link_write=mode)
        console.print(f"[green]✓ writes into linked projects: {mode}[/]")
    except WorkspaceError as exc:
        console.print(f"[red]✗ {escape(str(exc))}[/]")


def _update_one(args: list[str], field: str, usage: str, *, join: bool = False) -> None:
    if len(args) < 2:
        console.print(f"usage: {usage}")
        return
    proj = find_project(args[0])
    if proj is None:
        console.print(f"[red]✗ no project “{escape(args[0])}”[/]")
        return
    value = " ".join(args[1:]) if join else args[1]
    try:
        new = workspace.update(proj["id"], **{field: value})
        console.print(f"[green]✓ {escape(new.get('name') or proj['name'])}: {field} → {escape(str(new.get(field, value)))}[/]")
    except WorkspaceError as exc:
        console.print(f"[red]✗ {escape(str(exc))}[/]")


def _settings(args: list[str]) -> None:
    data = workspace.load()
    if not args:
        for key, val in data["settings"].items():
            console.print(f"  [cyan]{key}[/] = {val}")
        console.print("[dim]/project settings <key> <value> changes one[/]")
        return
    key = args[0]
    if key not in workspace.DEFAULT_SETTINGS:
        console.print(f"[red]✗ unknown setting “{escape(key)}” — {', '.join(workspace.DEFAULT_SETTINGS)}[/]")
        return
    if len(args) < 2:
        console.print(f"  [cyan]{key}[/] = {data['settings'][key]}")
        return
    raw = args[1].lower()
    val: Any
    if raw in _TRUE:
        val = True if isinstance(workspace.DEFAULT_SETTINGS[key], bool) else int(raw) if raw.isdigit() else raw
    elif raw in _FALSE:
        val = False if isinstance(workspace.DEFAULT_SETTINGS[key], bool) else int(raw) if raw.isdigit() else raw
    elif raw.isdigit():
        val = int(raw)
    else:
        val = raw
    try:
        workspace.update_settings(**{key: val})
        console.print(f"[green]✓ {key} = {val}[/]")
    except WorkspaceError as exc:
        console.print(f"[red]✗ {escape(str(exc))}[/]")


def _scan(args: list[str]) -> None:
    add_all = "--all" in args
    paths = [a for a in args if not a.startswith("--")]
    if not paths:
        console.print("usage: /project scan <folder> [--all]")
        return
    root = os.path.abspath(os.path.expanduser(paths[0]))
    if not os.path.isdir(root):
        console.print(f"[red]✗ {escape(display(root))} isn't a folder[/]")
        return
    repos = find_repos(root)
    if not repos:
        console.print(f"[dim]no git repositories found in {escape(display(root))}[/]")
        return
    data = workspace.load()
    new = [r for r in repos if workspace.find_by_path(r, data=data) is None]
    for repo in repos:
        mark = "[dim]· in workspace[/]" if repo not in new else ""
        console.print(f"  {escape(display(repo))} {mark}")
    if not new:
        console.print("[dim]every repository found is already in your workspace[/]")
        return
    if add_all:
        _add(new)
        return
    quoted = " ".join(shlex.quote(display(r) if " " not in display(r) else r) for r in new)
    console.print(f"[dim]{len(new)} new — add them all with[/] [cyan]/project scan {escape(shlex.quote(display(root)))} --all[/]"
                  f"[dim] or [/][cyan]/project add {escape(quoted)}[/]")


def _help() -> None:
    console.print(
        "[bold]/project[/] (/workspace, /p) — the folders you work on\n"
        "  [cyan]/project[/]  open the project list (↵ switch · a add · d remove · l links · w write · r rename · J/K move · s settings)\n"
        "  [cyan]/project list[/] · [cyan]/project <id>[/] · [cyan]/project switch <id>[/]\n"
        "  [cyan]/project add [path…][/]  (default: this folder) · [cyan]/project rm <id>…[/]\n"
        "  [cyan]/project link <id>…[/] · [cyan]/project unlink <id>…[/]  (from this project)\n"
        "  [cyan]/project write ask|allow|deny[/]  this project's writes into linked ones\n"
        "  [cyan]/project rename <id> <name>[/] · [cyan]color <id> <#hex>[/] · [cyan]startup <id> lazy|warm|pinned[/]\n"
        "  [cyan]/project settings [key value][/] · [cyan]/project scan <folder> [--all][/]"
    )


def handle_project(c: str, arg: str) -> bool:
    """Run ``/project …`` (console output). ``True`` when the command was ours."""
    if c not in ALIASES:
        return False
    sub, args = parse(arg)
    try:
        if sub in ("", "list", "ls"):
            _list()
        elif sub in ("help", "?"):
            _help()
        elif sub == "add":
            _add(args)
        elif sub in ("rm", "remove"):
            _remove(args)
        elif sub == "link":
            _link(args, unlink=False)
        elif sub == "unlink":
            _link(args, unlink=True)
        elif sub == "write":
            _write(args)
        elif sub == "rename":
            _update_one(args, "name", "/project rename <id> <name>", join=True)
        elif sub == "color":
            _update_one(args, "color", "/project color <id> <#hex>")
        elif sub == "startup":
            _update_one(args, "startup", "/project startup <id> <lazy|warm|pinned>")
        elif sub == "settings":
            _settings(args)
        elif sub == "scan":
            _scan(args)
        elif sub == "group":
            _group(args)
        elif sub == "master":
            _master(args)
        else:
            target = switch_target(arg)
            if target is None:
                if sub in _SWITCH_WORDS:
                    console.print("usage: /project switch <id>")
                else:
                    console.print(f"[red]no project or subcommand “{escape(sub)}”[/] — /project help")
            else:
                do_switch(target)
    except WorkspaceError as exc:
        console.print(f"[red]{escape(str(exc))}[/]")
    return True


# ─── TUI routing (tui/app.py) ────────────────────────────────────────────────

def handled_by_app(text: str) -> bool:
    """``/project`` (bare → the modal) and ``/project <id>`` (switch) need the app's chat
    plumbing; every other subcommand is plain output and goes through ``handle_slash``."""
    head, _, arg = (text or "").strip().partition(" ")
    if head not in ALIASES:
        return False
    return is_bare(arg) or switch_target(arg) is not None
