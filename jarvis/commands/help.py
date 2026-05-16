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
    ("Agents", [
        ("/agent", "open the agent picker (project + global)"),
        ("/agent list", "list all available agents"),
        ("/agent <name>", "activate an agent by name"),
        ("/agent off", "deactivate — base system prompt only"),
        ("/agent new <name>", "scaffold a new agent in .harness/agents/"),
        ("/agent edit <name>", "open an existing agent in $EDITOR"),
        ("/agent show <name>", "render an agent's body in the transcript"),
        ("/agent refresh", "re-scan disk for new/changed agents"),
        ("/agent global on|off", "toggle global agent discovery (persisted)"),
        ("/agent scope project|global", "default scope for /agent new"),
        ("/agent init", "scaffold a .harness/ tree in the current project"),
    ]),
    ("Skills (LLM auto-invokes)", [
        ("/skill", "open the skill browser modal"),
        ("/skill list", "list all available skills"),
        ("/skill <name>", "load and preview a skill"),
        ("/skill refresh", "re-scan disk for new/changed skills"),
        ("/skill global on|off", "toggle global skill discovery (persisted)"),
    ]),
    ("MCP (Model Context Protocol)", [
        ("/mcp", "open the MCP control modal — list, toggle, import JSON, scope"),
    ]),
    ("Settings", [
        ("/settings", "show all preferences (single settings.json)"),
        ("/settings get <key>", "show one preference value"),
        ("/settings set <key> <value>", "change a preference — auto-saves + applies"),
        ("/settings reset <key>", "restore a preference to its default"),
        ("/settings reload", "re-read settings.json from disk"),
        ("/settings edit", "open settings.json in $EDITOR"),
        ("/settings path", "print settings.json file path"),
    ]),
    ("Control", [
        ("/upgrade", "update Jarvis to the latest version (git pull + pip install)"),
        ("/upgrade check", "check version status without upgrading"),
        ("/version", "show Jarvis version"),
        ("/think", "toggle extended thinking"),
        ("/think mode", "open thinking effort picker (xhigh/high/medium/low/minimal/none)"),
        ("/verbose", "toggle internal tool trace (thinking panels only with /think on)"),
        ("/auto", "toggle auto-approve bash"),
        ("/multi", "enter a multiline message (end with ';;' line)"),
        ("/model <name>", "switch model"),
        ("/provider <name>", "switch provider (anthropic/openrouter/opencode/opencode_zen)"),
        ("/tokens", "usage so far"),
        ("/cost", "estimated USD cost of session"),
        ("/stats", "session stats (time, msgs, tools, tokens)"),
        ("/key reset", "delete stored API key"),
        ("/login", "log in with your Anthropic Pro/Max subscription (OAuth)"),
        ("/logout", "log out of Anthropic OAuth (revert to API key)"),
        ("/auth", "show current auth mode + token info"),
    ]),
    ("Keyboard (TUI)", [
        ("Enter", "send"),
        ("Shift+Enter / Alt+Enter / Ctrl+J", "insert newline"),
        ("/", "open command palette"),
        ("Tab", "cycle agents"),
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
