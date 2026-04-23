"""Handlers for context-management slash commands: pin, alias, notes, clipboard."""
from ..console import console, Panel, Markdown
from ..constants import NOTES_FILE
from ..tools.mac.clipboard import clipboard_get, clipboard_set
from ..storage.prefs import save_pin, save_aliases
from .. import state


def handle_context(c: str, arg: str):
    """Return (handled, new_inp_or_None). new_inp signals fall-through send."""
    if c == "/pin":
        if not arg:
            console.print("usage: /pin <text>"); return True, None
        state.pinned_context = (state.pinned_context + "\n" + arg).strip()
        save_pin()
        console.print(f"[green]📌 pinned ({len(state.pinned_context)} chars)[/]")
        return True, None
    if c == "/unpin":
        state.pinned_context = ""; save_pin()
        console.print("[green]📌 cleared[/]")
        return True, None
    if c == "/notes":
        if NOTES_FILE.exists():
            console.print(Panel(Markdown(NOTES_FILE.read_text()),
                                title="📝 notes", border_style="yellow"))
        else:
            console.print("[dim]no notes yet[/]")
        return True, None
    if c == "/alias":
        if "=" not in arg:
            console.print("usage: /alias <name>=<command>  e.g. /alias gs=/git")
            return True, None
        k, v = arg.split("=", 1)
        state.aliases[k.strip().lstrip("/")] = v.strip()
        save_aliases()
        console.print(f"[green]alias {k.strip()} → {v.strip()}[/]")
        return True, None
    if c == "/aliases":
        if not state.aliases: console.print("[dim]none[/]"); return True, None
        for k, v in state.aliases.items():
            console.print(f"  [cyan]/{k}[/] → {v}")
        return True, None
    if c == "/copy":
        if not state.last_assistant_text:
            console.print("[dim]nothing to copy[/]"); return True, None
        clipboard_set(state.last_assistant_text)
        console.print(f"[green]copied {len(state.last_assistant_text)} chars[/]")
        return True, None
    if c == "/paste":
        pasted = clipboard_get()
        if not pasted.strip():
            console.print("[dim]clipboard empty[/]"); return True, None
        console.print(Panel(pasted[:400] + ("…" if len(pasted) > 400 else ""),
                            title="📋 pasted", border_style="dim"))
        return True, pasted  # fall through to send as user message
    return False, None
