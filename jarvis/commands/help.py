"""/help — show the command reference."""
from ..console import console, Panel, Table


def cmd_help():
    t = Table(show_header=True, header_style="bold cyan", box=None, padding=(0, 2))
    t.add_column("command"); t.add_column("description")
    sections = [
        ("Session", [
            ("/help", "this menu"),
            ("/new", "start a fresh conversation (keeps pinned context)"),
            ("/reset", "clear conversation"),
            ("/retry", "re-send last user message"),
            ("/history", "show message summary"),
            ("/search <q>", "search conversation for a phrase"),
            ("/export <file>", "export conversation as markdown"),
            ("/save <file>", "save session JSON"),
            ("/load <file>", "load session JSON"),
            ("/session", "list persisted sessions & resume"),
            ("/session resume <id>", "resume a session by id"),
            ("/session new", "start a new persisted session"),
            ("/session delete <id>", "delete a stored session"),
            ("/clear", "clear the terminal screen"),
            ("/exit", "quit"),
        ]),
        ("Context", [
            ("/pin <text>", "pin context injected into every system prompt"),
            ("/unpin", "clear pinned context"),
            ("/note <text>", "append a note to your notes file"),
            ("/notes", "show your notes file"),
            ("/alias <n>=<cmd>", "create a shortcut alias (e.g. /alias gs=/git)"),
            ("/aliases", "list aliases"),
        ]),
        ("Memory", [
            ("/memory", "show stored personal facts"),
            ("/memory add <text>", "save a personal fact"),
            ("/memory del <id>", "delete a fact by id"),
            ("/memory clear", "wipe all memory"),
        ]),
        ("Files & Shell", [
            ("/ls [path]", "list directory"),
            ("/cd <dir>", "change working dir"),
            ("/pwd", "print working dir"),
            ("/find <pat>", "glob find files (e.g. **/*.py)"),
            ("/run <cmd>", "run a shell command (auto-approved)"),
            ("/undo", "restore last file edit"),
            ("/git", "git status"),
            ("/diff [path]", "git diff"),
        ]),
        ("Clipboard", [
            ("/copy", "copy last assistant response to clipboard"),
            ("/paste", "send clipboard text, or OCR a clipboard image, as the next message"),
            ("plain prompt + image clipboard", "type your prompt normally; a fresh clipboard image is OCR'd and attached"),
        ]),
        ("Control", [
            ("/think", "toggle extended thinking"),
            ("/auto", "toggle auto-approve bash"),
            ("/multi", "enter a multiline message (end with ';;' line)"),
            ("/model <name>", "switch model"),
            ("/tokens", "usage so far"),
            ("/cost", "estimated USD cost of session"),
            ("/stats", "session stats (time, msgs, tools, tokens)"),
            ("/key reset", "delete stored API key"),
            ("/login", "log in with Anthropic (Pro/Max subscription)"),
            ("/logout", "clear OAuth tokens (fall back to API key)"),
            ("/auth", "show current auth mode + token info"),
        ]),
    ]
    for section, rows in sections:
        t.add_row(f"[bold yellow]── {section} ──[/]", "")
        for c, d in rows:
            t.add_row(f"[cyan]{c}[/]", d)
    console.print(Panel(t, title="📖 commands", border_style="blue"))
