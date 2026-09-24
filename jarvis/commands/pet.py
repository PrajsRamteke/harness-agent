"""/pet — care for Jarvis the pet from the prompt.

    /pet                 the pet card (TUI dialog · printed card in the REPL)
    /pet pat|feed|play|nap|wake|trick
    /pet feed fish|milk|cookie
    /pet name <name>     rename
    /pet fur [name]      change fur (ginger · midnight · snow · smoky · cocoa · theme)
    /pet on|off          show / hide the kitty in the input box
    /pet nudges on|off   gentle reminders (breaks, full context, late nights)
    /pet stats           the card as text
"""
from __future__ import annotations

from rich.console import Group
from rich.table import Table
from rich.text import Text

from ..console import console
from .. import pet as pet_pkg
from ..pet import Reaction, get_pet, save_pet
from ..pet import sprites

USAGE = ("usage: /pet [pat|feed [fish|milk|cookie]|play|nap|wake|trick|stats|"
         "name <name>|fur [name]|on|off|nudges on|off]")


def card(pet=None) -> Group:
    """A static printable card (legacy REPL and ``/pet stats``)."""
    pet = pet or get_pet()
    cat = Text("\n").join(sprites.big_cat("happy" if pet.happiness >= 60 else "idle", 0.0, pet.fur))
    into, span = pet.level_progress()
    info = Text()
    info.append(f"{pet.name}", style="bold")
    info.append(f"  Lv {pet.level} · {pet.title}\n", style="bold magenta")
    info.append(f"{into}/{span} xp to next level\n\n", style="dim")
    info.append(pet.mood_line() + "\n\n", style="italic")
    for label, val in (("♥ love", pet.happiness), ("◆ food", pet.fullness), ("✦ energy", pet.energy)):
        filled = round(val / 100 * 16)
        info.append(f"{label:<9}", style="dim")
        info.append("━" * filled, style="bold")
        info.append("━" * (16 - filled), style="dim")
        info.append(f" {round(val)}\n")
    info.append(f"\n{pet.count('turns')} turns · {pet.count('edits')} edits · "
                f"{pet.count('pats')} pats · {pet.count('snacks')} snacks", style="dim")
    grid = Table.grid(padding=(0, 3))
    grid.add_row(cat, info)
    return Group(grid, Text("try /pet pat · /pet feed · /pet play · /pet trick", style="dim"))


def _say(r: Reaction, fallback: str) -> None:
    pet = get_pet()
    line = r.say or fallback
    if r.level_up:
        line += f"  ★ level up! Lv {r.level_up} · {pet.title}"
    console.print(f"[magenta]♥ {pet.name}:[/] {line}")
    pet_pkg.notify(r)
    save_pet()


def _set_setting(key: str, on: bool) -> None:
    from ..storage.settings import get_settings

    get_settings().set(f"pet.{key}", on)


def handle_pet(c: str, arg: str):
    if c not in ("/pet", "/pets", "/jarvis"):
        return False, None
    pet = get_pet()
    pet.tick()
    parts = arg.split(maxsplit=1)
    sub = parts[0].lower() if parts else ""
    rest = parts[1].strip() if len(parts) > 1 else ""

    if sub in ("", "card", "stats", "status", "info"):
        console.print(card(pet))
    elif sub in ("pat", "pet", "stroke", "love"):
        _say(pet.pat(), "purr~")
    elif sub in ("feed", "snack", "treat"):
        snack = rest.lower() or None
        if snack and snack not in pet_pkg.SNACKS:
            console.print(f"[red]unknown snack '{rest}'[/] — try fish, milk or cookie")
            return True, None
        _say(pet.feed(snack), "nom")
    elif sub == "play":
        _say(pet.play(), "*pounces*")
    elif sub in ("nap", "sleep"):
        _say(pet.nap(), "zzz")
    elif sub == "wake":
        pet.wake()
        _say(Reaction("wave", 1.6, "*stretches* I'm up!"), "")
    elif sub == "trick":
        _say(pet.trick(), "*spins*")
    elif sub in ("name", "rename"):
        if not rest:
            console.print(f"[dim]{pet.name} — rename with /pet name <new name>[/]")
            return True, None
        new = pet.rename(rest)
        _say(Reaction("happy", 1.8, f"{new}? I love it ♥"), "")
    elif sub in ("fur", "color", "colour", "coat"):
        choice = rest.lower()
        if choice and choice not in sprites.FURS:
            names = ", ".join(sprites.FUR_ORDER)
            console.print(f"[red]unknown fur '{rest}'[/] — one of: {names}")
            return True, None
        pet.fur = choice or sprites.next_fur(pet.fur)
        _say(Reaction("trick", 1.4, f"new look: {sprites.FUR_LABELS.get(pet.fur, pet.fur)} ✦"), "")
    elif sub in ("on", "show"):
        _set_setting("enabled", True)
        _refresh_ui()
        console.print(f"[magenta]♥ {pet.name}[/] is back in the input box")
    elif sub in ("off", "hide"):
        _set_setting("enabled", False)
        _refresh_ui()
        console.print(f"[dim]{pet.name} is hidden — /pet on brings them back (the /pet card still works)[/]")
    elif sub in ("nudges", "reminders"):
        val = rest.lower()
        if val not in ("on", "off"):
            console.print("[dim]usage: /pet nudges on|off[/]")
            return True, None
        _set_setting("nudges", val == "on")
        console.print(f"[dim]{pet.name}'s nudges {'on' if val == 'on' else 'off'}[/]")
    else:
        console.print(f"[dim]{USAGE}[/]")
    return True, None


def _refresh_ui() -> None:
    """Show/hide the kitty now (TUI only; the hook applies visibility)."""
    pet_pkg.notify(Reaction("wave", 1.2))
