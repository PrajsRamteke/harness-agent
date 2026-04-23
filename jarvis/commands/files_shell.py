"""Handlers for file/shell/git slash commands."""
import json, os, pathlib, time

from ..console import console
from ..constants import CONFIG_DIR, NOTES_FILE
from ..tools.dirs import list_dir, glob_files
from ..tools.shell import run_bash
from ..tools.git import git_status, git_diff
from ..storage.prefs import save_pin, save_aliases, export_markdown
from ..utils.serialize import _msg_to_json
from ..storage.sessions import db_create_session, db_replace_session_messages
from .. import state


def handle_files_shell(c: str, arg: str) -> bool:
    """Return True if the command was handled here."""
    if c == "/ls": console.print(list_dir(arg or ".")); return True
    if c == "/cd":
        try:
            os.chdir(arg or str(pathlib.Path.home()))
            console.print(f"[green]cwd → {pathlib.Path.cwd()}[/]")
        except Exception as e: console.print(f"[red]{e}[/]")
        return True
    if c == "/pwd": console.print(str(pathlib.Path.cwd())); return True
    if c == "/git": console.print(git_status()); return True
    if c == "/diff": console.print(git_diff(arg)); return True
    if c == "/find":
        if not arg: console.print("usage: /find <glob>"); return True
        console.print(glob_files(arg)); return True
    if c == "/run":
        if not arg: console.print("usage: /run <cmd>"); return True
        prev_auto = state.auto_approve; state.auto_approve = True
        console.print(run_bash(arg))
        state.auto_approve = prev_auto
        return True
    if c == "/undo":
        if not state.backups: console.print("nothing to undo"); return True
        path, prev = state.backups.pop()
        pathlib.Path(path).write_text(prev)
        console.print(f"[green]restored {path}[/]")
        return True
    if c == "/export":
        if not arg: arg = f"claude-session-{int(time.time())}.md"
        console.print(f"[green]exported → {export_markdown(arg)}[/]")
        return True
    if c == "/save":
        if not arg: console.print("usage: /save <file>"); return True
        pathlib.Path(arg).write_text(json.dumps(
            [_msg_to_json(m) for m in state.messages], indent=2))
        console.print(f"[green]saved → {arg}[/]")
        return True
    if c == "/load":
        state.messages = json.loads(pathlib.Path(arg).read_text())
        state.current_session_id = db_create_session(state.MODEL)
        db_replace_session_messages(state.current_session_id, state.messages)
        console.print(f"[green]loaded {len(state.messages)} messages → session #{state.current_session_id}[/]")
        return True
    if c == "/note":
        if not arg: console.print("usage: /note <text>"); return True
        CONFIG_DIR.mkdir(parents=True, exist_ok=True)
        with NOTES_FILE.open("a") as f:
            f.write(f"- [{time.strftime('%Y-%m-%d %H:%M')}] {arg}\n")
        console.print(f"[green]📝 saved to {NOTES_FILE}[/]")
        return True
    return False
