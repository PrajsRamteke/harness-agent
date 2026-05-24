"""Handlers for context-management slash commands: pin, alias, notes, clipboard."""
from rich.markup import escape

from ..console import console, Panel, Markdown
from ..constants import NOTES_FILE, PANEL_PREVIEW_CHARS, PIN_FILE
from ..tools.mac.clipboard import clipboard_get, clipboard_set
from ..tools.image_input import append_image_block, clipboard_image_to_file, file_digest, ocr_image_block
from ..storage.prefs import save_aliases
from ..storage import pin as pin_store
from .. import state


def render_pin_preview() -> None:
    """Show numbered pinned-context preview in a panel."""
    text = pin_store.pin_text()
    enabled = pin_store.is_enabled()
    if not text:
        console.print(Panel(
            "[dim]No pinned context yet.[/]\n\n"
            "[dim]Use [/][cyan]/pin <text>[/][dim] to append standing instructions, "
            "or [/][cyan]/pin[/][dim] in the TUI to open the pin viewer.[/]",
            title="📌 Pinned Context",
            border_style="magenta",
        ))
        return
    lines, chars = pin_store.pin_stats(text)
    noun = "lines" if lines != 1 else "line"
    body = "\n".join(
        f"[dim]{line_no:>3}[/]  {escape(content)}"
        for line_no, content in pin_store.preview_lines(text)
    )
    state_label = "[green]injection on[/]" if enabled else "[yellow]injection paused[/]"
    console.print(Panel(
        f"{state_label}\n\n{body}\n\n[dim]{PIN_FILE}[/]",
        title=f"📌 Pinned Context  ({lines} {noun}, {chars} chars)",
        border_style="magenta" if enabled else "yellow",
    ))


def _handle_pin_toggle(arg: str) -> bool:
    """Handle /pin on|off|toggle. Returns True if ``arg`` was a toggle subcommand."""
    sub = (arg.split(maxsplit=1)[0] if arg else "").lower()
    if sub in ("on", "enable", "enabled"):
        pin_store.set_enabled(True)
        console.print("[green]▪ pin injection enabled[/]")
        return True
    if sub in ("off", "disable", "disabled"):
        pin_store.set_enabled(False)
        console.print("[yellow]▪ pin injection disabled — saved text kept[/]")
        return True
    if sub in ("toggle", "switch"):
        on = pin_store.toggle_enabled()
        if on:
            console.print("[green]▪ pin injection enabled[/]")
        else:
            console.print("[yellow]▪ pin injection disabled — saved text kept[/]")
        return True
    return False


def handle_context(c: str, arg: str):
    """Return (handled, new_inp_or_None). new_inp signals fall-through send."""
    if c == "/pin":
        if not arg:
            render_pin_preview()
            return True, None
        if _handle_pin_toggle(arg):
            return True, None
        lines, chars = pin_store.append_pin(arg)
        enabled = "on" if pin_store.is_enabled() else "paused"
        console.print(
            f"[green]▪ pinned ({lines} line{'s' if lines != 1 else ''}, "
            f"{chars} chars, injection {enabled})[/]"
        )
        return True, None
    if c == "/unpin":
        pin_store.clear_pin()
        console.print("[green]▪ cleared[/]")
        return True, None
    if c == "/notes":
        if NOTES_FILE.exists():
            console.print(Panel(Markdown(NOTES_FILE.read_text()),
                                title="✎ notes", border_style="yellow"))
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
        # First check if clipboard has an image
        img = clipboard_image_to_file()
        if img is not None:
            console.print(f"[dim]▣ image on clipboard → OCR ({img})[/]")
            body, ocr = ocr_image_block(img, label="clipboard")
            state.last_clipboard_image_digest = file_digest(img)
            if arg.strip():
                body = append_image_block(arg.strip(), body)
            console.print(Panel(ocr[:PANEL_PREVIEW_CHARS] + ("…" if len(ocr) > PANEL_PREVIEW_CHARS else ""),
                                title="▣ pasted image (OCR)", border_style="cyan"))
            return True, body
        pasted = clipboard_get()
        if not pasted.strip():
            console.print("[dim]clipboard empty[/]"); return True, None
        console.print(Panel(pasted[:PANEL_PREVIEW_CHARS] + ("…" if len(pasted) > PANEL_PREVIEW_CHARS else ""),
                            title="☰ pasted", border_style="dim"))
        return True, pasted  # fall through to send as user message
    return False, None
