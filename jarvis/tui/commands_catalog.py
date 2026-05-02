"""Flat list of slash commands shown in the TUI palette."""

COMMANDS = [
    # Session
    ("/help", "show command reference"),
    ("/new", "start a fresh conversation (keeps pinned context)"),
    ("/reset", "clear conversation"),
    ("/retry", "re-send last user message"),
    ("/history", "show message summary"),
    ("/search ", "search conversation for a phrase"),
    ("/export ", "export conversation as markdown"),
    ("/save ", "save session JSON"),
    ("/load ", "load session JSON"),
    ("/session", "list persisted sessions & resume"),
    ("/session resume ", "resume a session by id"),
    # /new does the same thing — /session new removed as duplicate
    ("/session delete ", "delete a stored session"),
    ("/clear", "clear the terminal screen"),
    ("/exit", "quit"),
    # Context
    ("/pin ", "pin context injected into every system prompt"),
    ("/unpin", "clear pinned context"),
    ("/note ", "append a note to your notes file"),
    ("/notes", "show your notes file"),
    ("/alias ", "create a shortcut alias (e.g. /alias gs=/git)"),
    ("/aliases", "list aliases"),
    # Memory
    ("/memory", "show stored personal facts"),
    ("/memory add ", "save a personal fact"),
    ("/memory del ", "delete a fact by id"),
    ("/memory clear", "wipe all memory"),
    # Skills
    ("/skill", "list saved skills"),
    ("/skill search ", "find relevant skills for a task"),
    ("/skill add ", "save a skill"),
    ("/skill del ", "delete a skill"),
    ("/skill clear", "wipe all skills"),
    # Files & Shell
    ("/ls", "list directory"),
    ("/cd ", "change working dir"),
    ("/pwd", "print working dir"),
    ("/find ", "glob find files"),
    ("/run ", "run a shell command"),
    ("/undo", "restore last file edit"),
    ("/git", "git status"),
    ("/diff", "git diff"),
    # Project
    ("/project graph", "generate interactive project structure visualization"),
    ("/project graph --rebuild", "force rescan before generating graph"),
    # Clipboard
    ("/copy", "copy last assistant response"),
    ("/paste", "send clipboard text / OCR clipboard image"),
    # Modes
    ("/coding", "toggle coding mode ON/OFF (adds large-codebase rules)"),
    ("/mode", "show current mode"),
    ("/mode coding", "switch to coding mode"),
    ("/mode default", "switch back to default mode"),
    # Control
    ("/think", "toggle extended thinking"),
    ("/verbose", "toggle internal tool trace (thinking UI needs /think)"),
    ("/auto", "toggle auto-approve bash"),
    ("/multi", "enter a multiline message"),
    ("/model ", "switch model"),
    ("/tokens", "usage so far"),
    ("/cost", "estimated USD cost"),
    ("/stats", "session stats"),
    ("/key reset", "delete stored API key"),
    ("/login", "log in with Anthropic"),
    ("/logout", "clear OAuth tokens"),
    ("/auth", "show current provider + auth"),
    ("/provider ", "switch provider (anthropic/openrouter)"),
]


def filter_commands(query: str):
    """Return commands whose cmd or description matches the query substring."""
    q = query.strip().lower()
    if not q or q == "/":
        return COMMANDS
    return [
        (c, d) for (c, d) in COMMANDS
        if q in c.lower() or q in d.lower()
    ]
