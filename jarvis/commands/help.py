"""/help — show the command reference, optionally filtered by topic/keyword."""
from ..console import console, Panel, Table


_SECTIONS = [
    ("Session", [
        ("/help", "this menu — pass a keyword to filter, e.g. /help session"),
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
    ("Skills (Lessons — agent self-learning)", [
        ("/lesson", "list saved lessons (what agent learned)"),
        ("/lesson search <q>", "find relevant lessons for a task"),
        ("/lesson add <task> :: <lesson> [:: tags]", "save a lesson"),
        ("/lesson del <id>", "delete a lesson"),
        ("/lesson clear", "wipe all lessons"),
    ]),
    ("Skills (Project-base — SKILL.md)", [
        ("/skill", "list available project-base skills (headers only)"),
        ("/skill <name>", "load and show full skill content"),
        ("/skill refresh", "re-scan for new/changed skills"),
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
    ("Modes", [
        ("/coding", "toggle coding mode ON/OFF (adds large-codebase rules)"),
        ("/mode", "show current mode / switch mode (e.g. /mode coding)"),
    ]),
    ("MCP (Model Context Protocol)", [
        ("/mcp", "manage MCP servers: list, add, connect, disconnect, reload"),
        ("/mcp list", "show configured & connected servers"),
        ("/mcp add <name> --command <cmd>", "add a stdio MCP server"),
        ("/mcp add <name> --url <url>", "add an SSE MCP server"),
        ("/mcp connect <name>", "connect a configured server"),
        ("/mcp disconnect <name>", "disconnect a server"),
        ("/mcp remove <name>", "remove a server config"),
    ]),
    ("Control", [
        ("/version", "show Jarvis version"),
        ("/think", "toggle extended thinking"),
        ("/verbose", "toggle internal tool trace (thinking panels only with /think on)"),
        ("/auto", "toggle auto-approve bash"),
        ("/multi", "enter a multiline message (end with ';;' line)"),
        ("/model <name>", "switch model"),
        ("/provider <name>", "switch provider (anthropic/openrouter)"),
        ("/tokens", "usage so far"),
        ("/cost", "estimated USD cost of session"),
        ("/stats", "session stats (time, msgs, tools, tokens)"),
        ("/key reset", "delete stored API key"),
        ("/login", "log in with Anthropic (Pro/Max subscription)"),
        ("/logout", "clear OAuth tokens (fall back to API key)"),
        ("/auth", "show current auth mode + token info"),
    ]),
    ("Keyboard (TUI)", [
        ("Enter", "send"),
        ("Shift+Enter / Alt+Enter / Ctrl+J", "insert newline"),
        ("/", "open command palette"),
        ("Tab", "cycle modes"),
        ("F2 / Ctrl+T", "toggle internal tool trace"),
        ("Esc", "cancel current turn"),
        ("Ctrl+C", "copy selection · cancel turn · press twice to quit"),
        ("Ctrl+D", "quit"),
    ]),
]


def _filter(query: str):
    q = query.strip().lower()
    if not q:
        return _SECTIONS
    out = []
    for section, rows in _SECTIONS:
        kept = [
            (c, d)
            for (c, d) in rows
            if q in c.lower() or q in d.lower() or q in section.lower()
        ]
        if kept:
            out.append((section, kept))
    return out


def cmd_help(arg: str = ""):
    """Show the command reference. With *arg* set, only show matching commands."""
    sections = _filter(arg)
    title = "📖 commands"
    if arg:
        if not sections:
            console.print(
                f"[yellow]no commands matched[/] [dim]'{arg}'[/]  "
                f"[dim](try /help for the full list)[/]"
            )
            return
        title = f"📖 commands matching '{arg}'"

    t = Table(show_header=True, header_style="bold cyan", box=None, padding=(0, 2))
    t.add_column("command")
    t.add_column("description")
    for section, rows in sections:
        t.add_row(f"[bold yellow]── {section} ──[/]", "")
        for c, d in rows:
            t.add_row(f"[cyan]{c}[/]", d)
    console.print(Panel(t, title=title, border_style="blue"))
