"""/pet — care for your pets from the prompt.

    /pet                        the pet card (TUI dialog · printed card in the REPL)
    /pet pat|feed|play|nap|wake|trick
    /pet feed fish|milk|cookie
    /pet fish · /pet focus      the fish game · a 25-minute focus timer (TUI)
    /pet adopt cat|dog|bunny|dragon [name]
    /pet switch [name]          change which pet is out in the pen
    /pet badges · /pet recap    badges earned · what we did this session
    /pet name <name>            rename
    /pet fur [name]             change fur (per species)
    /pet wear <item>            put on / take off glasses · party · scarf · crown
    /pet on|off                 show / hide the pet
    /pet nudges|notify on|off   gentle reminders · desktop notifications
"""
from __future__ import annotations

from rich.console import Group
from rich.table import Table
from rich.text import Text

from ..console import console
from .. import pet as pet_pkg
from ..pet import BADGES, Reaction, get_pet, get_roster, save_pet
from ..pet import session as pet_session
from ..pet import sprites

USAGE = ("usage: /pet [pat|feed [snack]|play|nap|wake|trick|fish|focus|adopt <species> [name]|"
         "switch [name]|badges|recap|stats|name <name>|fur [name]|wear <item>|on|off|"
         "nudges on|off|notify on|off]")


def card(pet=None) -> Group:
    """A static printable card (legacy REPL and ``/pet stats``)."""
    pet = pet or get_pet()
    species = "egg" if pet.is_egg else pet.species
    cat = Text("\n").join(sprites.portrait(species, "happy" if pet.happiness >= 60 else "idle",
                                           0.0, pet.fur, outfit=pet.outfit(), stage=pet.stage))
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
    info.append(f"\n{len(pet.badges)}/{len(BADGES)} badges", style="dim")
    if pet.streak:
        info.append(f" · {pet.streak}-day streak", style="dim")
    grid = Table.grid(padding=(0, 3))
    grid.add_row(cat, info)
    return Group(grid, Text("try /pet pat · /pet feed · /pet play · /pet trick · /pet badges", style="dim"))


def _say(r: Reaction, fallback: str) -> None:
    pet = get_pet()
    line = r.say or fallback
    if r.level_up:
        line += f"  ★ level up! Lv {r.level_up} · {pet.title}"
    for bid in r.badges:
        info = pet_pkg.BADGE_INFO[bid]
        line += f"  {info[1]} badge: {info[2]}"
    console.print(f"[magenta]♥ {pet.name}:[/] {line}")
    pet_pkg.notify(r)
    save_pet()


def _set_setting(key: str, on: bool) -> None:
    from ..storage.settings import get_settings

    get_settings().set(f"pet.{key}", on)


def _badges() -> None:
    pet = get_pet()
    t = Table.grid(padding=(0, 2))
    for bid, icon, name, how, _c, _n in BADGES:
        got = bid in pet.badges
        t.add_row(Text(icon, style="bold yellow" if got else "dim"),
                  Text(name, style="bold" if got else "dim"),
                  Text(how, style="dim"))
    console.print(Text(f"{pet.name}'s badges — {len(pet.badges)}/{len(BADGES)}", style="bold magenta"))
    console.print(t)


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
    elif sub in ("fish", "focus", "game"):
        action = "fish" if sub in ("fish", "game") else "focus"
        if not pet_pkg.request(action):
            console.print("[dim]the fish game and focus timer live in the TUI's sidebar pen[/]")
    elif sub == "adopt":
        bits = rest.split(maxsplit=1)
        species = bits[0].lower() if bits else ""
        species = {"kitty": "cat", "puppy": "dog", "rabbit": "bunny", "egg": "dragon"}.get(species, species)
        if species not in pet_pkg.SPECIES:
            console.print(f"[dim]adopt one of: {', '.join(pet_pkg.SPECIES)} — /pet adopt dog Biscuit[/]")
            return True, None
        try:
            new = get_roster().adopt(species, bits[1] if len(bits) > 1 else "")
        except ValueError as e:
            console.print(f"[red]{e}[/]")
            return True, None
        line = (f"an egg! it hatches after {new.hatch_left} turns together ✦" if new.is_egg
                else f"welcome home, {new.name}! ♥")
        _say(Reaction("wobble" if new.is_egg else "hatch", 2.6, line), "")
    elif sub == "switch":
        roster = get_roster()
        if len(roster.pets) < 2:
            console.print("[dim]just one pet — /pet adopt cat|dog|bunny|dragon [name][/]")
            return True, None
        if rest:
            idx = next((i for i, p in enumerate(roster.pets) if p.name.lower() == rest.lower()), None)
            if idx is None:
                console.print(f"[red]no pet named '{rest}'[/] — {', '.join(p.name for p in roster.pets)}")
                return True, None
            roster.active = idx
            new = roster.pet
        else:
            new = roster.switch()
        _say(Reaction("wave", 1.8, f"hi, it's {new.name}! ♥"), "")
    elif sub == "badges":
        _badges()
    elif sub == "recap":
        stats = pet_session.current()
        console.print(f"[magenta]{stats.recap(pet.name)}[/]" if not stats.empty
                      else "[dim]nothing to recap yet — let's build something ✦[/]")
    elif sub in ("name", "rename"):
        if not rest:
            console.print(f"[dim]{pet.name} — rename with /pet name <new name>[/]")
            return True, None
        new_name = pet.rename(rest)
        _say(Reaction("happy", 1.8, f"{new_name}? I love it ♥"), "")
    elif sub in ("fur", "color", "colour", "coat"):
        choice = rest.lower()
        furs = sprites.SPECIES_FURS.get(pet.species, sprites.FUR_ORDER)
        if choice and choice not in furs:
            console.print(f"[red]unknown fur '{rest}'[/] — one of: {', '.join(furs)}")
            return True, None
        pet.fur = choice or sprites.next_fur(pet.fur, pet.species)
        _say(Reaction("trick", 1.4, f"new look: {sprites.FUR_LABELS.get(pet.fur, pet.fur)} ✦"), "")
    elif sub in ("wear", "outfit", "wardrobe"):
        item = {"hat": "party", "party hat": "party", "glass": "glasses"}.get(rest.lower(), rest.lower())
        names = [a for a, _ in pet_pkg.ACCESSORIES]
        if item not in names:
            owned = ", ".join(pet.unlocked()) or "nothing yet (glasses at Lv 3)"
            console.print(f"[dim]/pet wear glasses|party|scarf|crown — unlocked: {owned}[/]")
            return True, None
        if item not in pet.unlocked():
            lvl = dict(pet_pkg.ACCESSORIES)[item]
            console.print(f"[dim]{pet_pkg.ACCESSORY_LABELS[item]} unlocks at Lv {lvl} ✦[/]")
            return True, None
        on = pet.toggle_accessory(item)
        _say(Reaction("happy", 1.4, f"{pet_pkg.ACCESSORY_LABELS[item]} {'on ✦' if on else 'off'}"), "")
    elif sub in ("on", "show"):
        _set_setting("enabled", True)
        _refresh_ui()
        console.print(f"[magenta]♥ {pet.name}[/] is back")
    elif sub in ("off", "hide"):
        _set_setting("enabled", False)
        _refresh_ui()
        console.print(f"[dim]{pet.name} is hidden — /pet on brings them back (the /pet card still works)[/]")
    elif sub in ("nudges", "reminders", "notify", "notifications"):
        key = "nudges" if sub in ("nudges", "reminders") else "notify"
        val = rest.lower()
        if val not in ("on", "off"):
            console.print(f"[dim]usage: /pet {key} on|off[/]")
            return True, None
        _set_setting(key, val == "on")
        console.print(f"[dim]{pet.name}'s {'nudges' if key == 'nudges' else 'desktop notifications'} "
                      f"{val}[/]")
    else:
        console.print(f"[dim]{USAGE}[/]")
    return True, None


def _refresh_ui() -> None:
    """Show/hide the pet now (TUI only; the hook applies visibility)."""
    pet_pkg.notify(Reaction("wave", 1.2))
