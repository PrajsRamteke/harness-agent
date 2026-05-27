"""Flat list of slash commands shown in the TUI palette.

Repeated subcommand groups (``/memory add``, ``/lesson search``,
``/settings set``, …) have been folded into modal-opening entries so the
palette stays scannable. The full subcommand surface is still reachable by
typing the command directly — see each modal for the in-modal key bindings.
"""

COMMANDS = [
    # Session
    ("/help", "open the help reference (type to search)"),
    ("/new", "start a fresh conversation (keeps pinned context)"),
    ("/reset", "clear conversation"),
    ("/retry", "re-send last user message"),
    ("/history", "show message summary"),
    ("/search ", "search conversation for a phrase"),
    ("/export ", "export conversation as markdown"),
    ("/save ", "save session JSON"),
    ("/load ", "load session JSON"),
    ("/session", "open the session modal (resume / delete handled inside)"),
    ("/clear", "clear the terminal screen"),
    ("/keytest", "show what key your terminal sends (Shift+Enter debug)"),
    ("/exit", "quit"),
    # Context
    ("/pin", "pinned context — view · append · /pin on|off|toggle · clear via /unpin"),
    ("/unpin", "clear pinned context"),
    ("/note ", "append a note to your notes file"),
    ("/notes", "show your notes file"),
    ("/alias ", "create a shortcut alias (e.g. /alias gs=/git)"),
    ("/aliases", "list aliases"),
    # Memory — modal handles list / add / delete / clear
    ("/memory", "open the memory modal (list · add · delete · clear)"),
    # Lessons — modal handles list / search / add / delete / clear
    ("/lesson", "open the lesson modal (list · search · add · delete · clear)"),
    # Skills — modal browses; LLM auto-invokes by description
    ("/skill", "open the skill browser modal"),
    # Local commands — shell/file/git that run without LLM
    ("/local", "open the local commands modal (pick and run ls, pwd, cd, git, find, run, undo, diff)"),
    # Clipboard
    ("/copy", "copy last assistant response"),
    ("/paste", "send clipboard text / OCR clipboard image"),
    # Agents — modal handles browse · activate · new · edit · global · scope
    ("/agent", "open the agent control modal"),
    ("/agent init", "scaffold a .harness/ tree in the current project"),
    # Theme — modal handles the picker
    ("/theme", "open the theme picker (red · purple)"),
    # Scan
    ("/scan", "AI-powered deep scan: identity / docs / projects → memory"),
    # MCP — single modal for everything
    ("/mcp", "MCP control panel — list, toggle, import JSON, manage scope"),
    # Browser bridge diagnostics
    ("/browser", "Chrome WebBridge status — is the extension connected & why not"),
    # Settings — modal handles get · set · reset · reload · edit · path
    ("/settings", "open the settings modal (view · edit · reset · reload)"),
    # Upgrade
    ("/upgrade", "update Jarvis to the latest version (use 'check' to dry-run)"),
    # Version
    ("/version", "show Jarvis version"),
    # Control
    ("/think", "toggle extended thinking (bare /think) — or open effort picker"),
    ("/verbose", "toggle internal tool trace (thinking UI needs /think)"),
    ("/auto", "toggle auto-approve bash"),
    ("/multi", "enter a multiline message"),
    ("/model", "open model picker (Harness Agent free models listed first)"),
    ("/mode", "alias for /model — open model picker"),
    ("/tokens", "usage so far"),
    ("/cost", "estimated USD cost"),
    ("/stats", "session stats"),
    ("/key", "open API key manager — Anthropic API, OpenRouter, OpenCode"),
    ("/login", "OAuth sign in — Anthropic or OpenAI Codex subscription"),
    ("/logout", "OAuth sign out — subscription accounts only"),
    ("/auth", "OAuth login modal — sign-in status and activate subscription auth"),
    ("/provider", "open the provider picker (select anthropic / openrouter / opencode)"),
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
