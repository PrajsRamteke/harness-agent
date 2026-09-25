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
    ("/loop", "repeat a task — /loop <task> (self-paced) · /loop 5m <task> · /loop stop"),
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
    ("/copy", "copy last reply — ⌃Y · 'code' = last code block · 'all' = whole chat"),
    ("/paste", "send clipboard text / OCR clipboard image"),
    # Agents — modal handles browse · activate · new · edit · global · scope
    ("/agent", "open the agent control modal"),
    ("/agent init", "scaffold a .harness/ tree in the current project"),
    # Custom commands — user-defined prompt templates triggered as /<name>
    ("/command", "open the command manager modal (new · edit · delete · import/export · global)"),
    # Theme — modal handles the picker
    ("/theme", "open the theme picker (live preview)"),
    # Pets — everyday play lives in the sidebar pen's buttons; the on/off
    # toggle right after this entry is added by filter_commands (it flips).
    ("/pet", "your pet's card — stats · badges · wardrobe · rename · fur"),
    # Scan
    ("/scan", "AI-powered deep scan: identity / docs / projects → memory"),
    # MCP — single modal for everything
    ("/mcp", "MCP control panel — list, toggle, import JSON, manage scope"),
    # Settings — modal handles get · set · reset · reload · edit · path
    ("/settings", "open the settings modal (view · edit · reset · reload)"),
    # Upgrade
    ("/upgrade", "update Jarvis to the latest version (use 'check' to dry-run)"),
    # Version
    ("/version", "show Jarvis version"),
    # Control
    ("/think", "toggle extended thinking (bare /think) — or open effort picker"),
    ("/verbose", "toggle trace — thinking + tool output previews (⌃T)"),
    ("/sidebar", "toggle the session sidebar (⌃B)"),
    ("/auto", "toggle auto-approve bash"),
    ("/plan", "toggle plan mode — read-only research until you approve a plan"),
    ("/multi", "enter a multiline message"),
    ("/model", "open model picker (Harness Agent free models listed first)"),
    ("/mode", "alias for /model — open model picker"),
    ("/tokens", "usage so far"),
    ("/cost", "estimated USD cost"),
    ("/stats", "session stats"),
    ("/provider", "one command for everything — OAuth login, API keys, provider switch"),
]


def _custom_command_entries():
    """User-defined commands from .harness/commands/ (and global dirs).

    Listed after the built-ins so the palette stays predictable. Failures
    (e.g. during early startup) silently yield no extra entries.
    """
    try:
        from ..storage.commands import list_commands
        builtin_heads = {c.split()[0] for c, _ in COMMANDS}
        entries = []
        for rec in list_commands():
            if f"/{rec['name']}" in builtin_heads:
                # built-ins always win in dispatch — don't advertise the shadowed file
                continue
            hint = f" {rec['argument_hint']}" if rec.get("argument_hint") else ""
            tag = "global" if rec.get("scope") == "global" else "project"
            entries.append((
                f"/{rec['name']} ",
                f"⌘ custom ({tag}){hint} — {rec.get('description') or 'user-defined prompt'}",
            ))
        return entries
    except Exception:
        return []


def _pet_toggle_entry() -> tuple[str, str]:
    """``/pet off`` while your pet is showing, ``/pet on`` once it's hidden."""
    try:
        from ..storage.settings import get_settings

        shown = get_settings().get("pet.enabled") is not False
    except Exception:
        shown = True
    return ("/pet off", "hide your pet") if shown else ("/pet on", "bring your pet back")


def _with_toggles(catalog: list[tuple[str, str]]) -> list[tuple[str, str]]:
    out: list[tuple[str, str]] = []
    for entry in catalog:
        out.append(entry)
        if entry[0] == "/pet":
            out.append(_pet_toggle_entry())
    return out


def filter_commands(query: str):
    """Return commands whose cmd or description matches the query substring."""
    catalog = _with_toggles(COMMANDS) + _custom_command_entries()
    q = query.strip().lower()
    if not q or q == "/":
        return catalog
    return [
        (c, d) for (c, d) in catalog
        if q in c.lower() or q in d.lower()
    ]
