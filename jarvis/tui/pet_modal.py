"""The ``/pet`` card — your pet up close: stats, badges, wardrobe, and the roster.

Also the adoption picker (:class:`PetAdoptScreen`) used by the pen's ``pets``
button and the card's ``a`` key.
"""
from __future__ import annotations

import random
import time

from rich.text import Text
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import CenterMiddle, Horizontal, Vertical
from textual.widgets import OptionList, Static
from textual.widgets.option_list import Option

from ..pet import (ACCESSORIES, ACCESSORY_LABELS, BADGES, SPECIES, SPECIES_LABELS, Reaction,
                   get_pet, get_roster, save_pet)
from ..pet import sprites
from .modal_chrome import TUI_MODAL_CHROME_CSS, TuiModalScreen, hint_line, picker_row
from .mouse_toggle import disable_mouse, enable_mouse
from . import theme as ui

BAR_W = 18


def stat_bar(label: str, icon: str, value: float, color: str) -> Text:
    filled = round(max(0.0, min(100.0, value)) / 100 * BAR_W)
    out = Text(no_wrap=True)
    out.append(f"{icon} ", style=color)
    out.append(f"{label:<8}", style=ui.FG_MUTE)
    out.append("━" * filled, style=color)
    out.append("━" * (BAR_W - filled), style=ui.blend(ui.BG_1, ui.FG_DIM, 0.35))
    out.append(f" {round(value):>3}", style=ui.FG_DIM)
    return out


def _stat_color(value: float, good: str) -> str:
    if value < 25:
        return ui.ERR
    if value < 45:
        return ui.WARN
    return good


def stats_text(pet, *, buddy_on: bool = True) -> Text:
    """The right-hand column of the card (also used by tests)."""
    lvl = pet.level
    into, span = pet.level_progress()
    colors = sprites.fur_colors(pet.fur)
    out = Text()
    out.append(f"Lv {lvl}", style=f"bold {ui.ACCENT}")
    out.append(f"  {pet.title}\n", style=f"bold {colors['line']}")
    filled = round(into / max(1, span) * (BAR_W + 12))
    out.append("▰" * filled, style=ui.ACCENT_2)
    out.append("▱" * (BAR_W + 12 - filled), style=ui.blend(ui.BG_1, ui.FG_DIM, 0.35))
    out.append(f"  {into}/{span} xp\n\n", style=ui.FG_DIM)
    out.append(pet.mood_line() + "\n\n", style=f"italic {ui.FG_MUTE}")
    out.append_text(stat_bar("love", "♥", pet.happiness, _stat_color(pet.happiness, sprites.PINK)))
    out.append("\n")
    out.append_text(stat_bar("food", "◆", pet.fullness, _stat_color(pet.fullness, ui.OK)))
    out.append("\n")
    out.append_text(stat_bar("energy", "✦", pet.energy, _stat_color(pet.energy, sprites.GOLD)))
    out.append("\n\n")
    days = pet.age_days()
    age = "born today" if days < 1 else f"{int(days)} day{'s' if int(days) != 1 else ''} old"
    kind = SPECIES_LABELS["dragon"] if pet.is_egg else sprites.FUR_LABELS.get(pet.fur, pet.fur)
    if not pet.is_egg and pet.species != "cat":
        kind += f" {SPECIES_LABELS[pet.species]}"
    out.append(f"{age} · {kind}", style=ui.FG_DIM)
    if pet.streak:
        out.append(f" · {pet.streak}d streak", style=sprites.GOLD)
        if pet.best_streak > pet.streak:
            out.append(f" (best {pet.best_streak})", style=ui.FG_DIM)
    if not buddy_on:
        out.append(" · hidden", style=ui.FG_DIM)
    out.append("\n")
    bits = [(pet.count("turns"), "turns"), (pet.count("edits"), "edits"),
            (pet.count("commits"), "commits"), (pet.count("pats"), "pats"),
            (pet.count("fish"), "fish")]
    for i, (n, label) in enumerate(bits):
        if i:
            out.append(" · ", style=ui.FG_DIM)
        out.append(f"{n:,}", style=ui.FG_MUTE)
        out.append(f" {label}", style=ui.FG_DIM)
    return out


def badges_text(pet) -> Text:
    out = Text(no_wrap=True, overflow="ellipsis")
    out.append("badges ", style=f"bold {ui.FG_MUTE}")
    for bid, icon, _name, _how, _c, _n in BADGES:
        out.append(icon + " ", style=sprites.GOLD if bid in pet.badges else ui.blend(ui.BG_1, ui.FG_DIM, 0.35))
    out.append(f" {len(pet.badges)}/{len(BADGES)}", style=ui.FG_DIM)
    latest = sorted(pet.badges.items(), key=lambda kv: kv[1])[-1:] if pet.badges else []
    if latest:
        from ..pet import BADGE_INFO

        info = BADGE_INFO.get(latest[0][0])
        if info:
            out.append(f" · latest: {info[2]}", style=ui.FG_DIM)
    return out


def wardrobe_text(pet) -> Text:
    out = Text(no_wrap=True, overflow="ellipsis")
    out.append("wardrobe ", style=f"bold {ui.FG_MUTE}")
    for i, (name, lvl) in enumerate(ACCESSORIES):
        if i:
            out.append(" · ", style=ui.FG_DIM)
        label = ACCESSORY_LABELS[name]
        if pet.level < lvl:
            out.append(f"{label} (Lv {lvl})", style=ui.blend(ui.BG_1, ui.FG_DIM, 0.4))
        elif name in pet.off:
            out.append(f"{label} (off)", style=ui.FG_DIM)
        else:
            out.append(label, style=ui.FG)
    return out


def roster_text() -> Text:
    roster = get_roster()
    out = Text(no_wrap=True, overflow="ellipsis")
    out.append("pets ", style=f"bold {ui.FG_MUTE}")
    for i, p in enumerate(roster.pets):
        if i:
            out.append(" · ", style=ui.FG_DIM)
        mark = "● " if i == roster.active else ""
        out.append(mark + p.name, style=f"bold {sprites.fur_colors(p.fur)['line']}" if i == roster.active
                   else ui.FG_MUTE)
        kind = "egg" if p.is_egg else p.species
        out.append(f" {kind} Lv {p.level}", style=ui.FG_DIM)
    return out


class PetCardScreen(TuiModalScreen[None]):
    DEFAULT_CSS = (
        TUI_MODAL_CHROME_CSS
        + """
    PetCardScreen #modal {
        width: 92%;
        max-width: 94;
    }
    PetCardScreen #pet_body {
        height: auto;
    }
    PetCardScreen #pet_left {
        width: 28;
        height: auto;
        margin: 0 3 0 0;
    }
    PetCardScreen #pet_sprite {
        height: 10;
        width: 26;
        margin: 0 0 0 1;
    }
    PetCardScreen #pet_say {
        height: 2;
        margin: 1 0 0 1;
        color: {ui.FG};
    }
    PetCardScreen #pet_stats {
        width: 1fr;
        height: auto;
    }
    PetCardScreen #pet_extra {
        height: auto;
        margin: 1 0 0 1;
    }
    """
    )

    BINDINGS = [
        Binding("escape", "close", "Close", show=True),
        Binding("enter", "pat", "Pat", show=False),
        Binding("space", "pat", "Pat", show=False),
        Binding("f", "feed", "Feed", show=False),
        Binding("p", "play", "Play", show=False),
        Binding("n", "nap", "Nap", show=False),
        Binding("t", "trick", "Trick", show=False),
        Binding("question_mark", "tip", "Tip", show=False),
        Binding("r", "rename", "Rename", show=False),
        Binding("c", "fur", "Fur", show=False),
        Binding("w", "wardrobe", "Wardrobe", show=False),
        Binding("a", "adopt", "Adopt", show=False),
        Binding("s", "switch", "Switch", show=False),
        Binding("h", "toggle_buddy", "Hide", show=False),
    ]

    def __init__(self) -> None:
        super().__init__()
        self._t0 = time.monotonic()
        self._anim: tuple[str, float, float, str] | None = None
        self._say = ""
        self._key: tuple | None = None
        self._wardrobe_i = 0

    def compose(self) -> ComposeResult:
        pet = get_pet()
        with CenterMiddle():
            with Vertical(id="modal"):
                yield Static(f"♥  {pet.name}", id="modal_title")
                with Horizontal(id="pet_body"):
                    with Vertical(id="pet_left"):
                        yield Static("", id="pet_sprite")
                        yield Static("", id="pet_say")
                    yield Static("", id="pet_stats")
                yield Static("", id="pet_extra")
                yield Static(
                    hint_line(("↵", "pat"), ("f", "feed"), ("p", "play"), ("n", "nap"),
                              ("t", "trick"), ("?", "tip"))
                    + "\n"
                    + hint_line(("r", "rename"), ("c", "fur"), ("w", "wear"), ("a", "adopt"),
                                ("s", "switch"), ("h", "hide")),
                    id="modal_hint",
                )

    def on_mount(self) -> None:
        enable_mouse()
        pet = get_pet()
        pet.tick(busy=bool(getattr(self.app, "_busy", False)))
        self._say = random.choice((f"hi! it's me, {pet.name} ♥", "*looks up at you*",
                                   "mrrp? ♥", "*slow blink* (that means I love you)"))
        self._refresh_all()
        self.set_interval(1 / 10, self._tick)

    def on_unmount(self) -> None:
        disable_mouse()
        save_pet()

    # ── rendering ────────────────────────────────────────────────────
    def _current(self) -> tuple[str, float, int, str]:
        now = time.monotonic()
        if self._anim is not None:
            name, t0, until, snack = self._anim
            if now < until:
                return name, now - t0, 0, snack
            self._anim = None
        t = now - self._t0
        if get_pet().napping():
            return "sleep", t, 0, ""
        cycle = t % 7.0
        look = -1 if 4.6 < cycle < 5.4 else (1 if 5.4 <= cycle < 6.2 else 0)
        return "idle", t, look, ""

    def _tick(self) -> None:
        anim, t, look, snack = self._current()
        pet = get_pet()
        left = pet.hatch_left
        cracks = 0 if left > 6 else 1 if left > 3 else 2 if left > 1 else 3
        species = "egg" if pet.is_egg else pet.species
        if pet.is_egg and anim not in ("wobble", "love", "surprised", "sleep", "hatch"):
            anim = "idle"
        lines = sprites.portrait(species, anim, t, pet.fur, outfit=pet.outfit(), stage=pet.stage,
                                 egg_cracks=cracks, look=look, snack=snack)
        key = tuple(line.plain for line in lines) + (anim, pet.fur, id(pet), ui.active_theme())
        if key == self._key:
            return
        self._key = key
        try:
            self.query_one("#pet_sprite", Static).update(Text("\n").join(lines))
        except Exception:
            pass

    def _refresh_all(self) -> None:
        pet = get_pet()
        try:
            buddy_on = bool(getattr(self.app, "_pet_enabled", lambda: True)())
            self.query_one("#pet_stats", Static).update(stats_text(pet, buddy_on=buddy_on))
            say = Text(self._say or "", style=ui.FG)
            if self._say:
                say = Text("“", style=ui.FG_DIM) + say + Text("”", style=ui.FG_DIM)
            self.query_one("#pet_say", Static).update(say)
            extra = Text("\n").join([badges_text(pet), wardrobe_text(pet), roster_text()])
            self.query_one("#pet_extra", Static).update(extra)
        except Exception:
            pass
        self._key = None
        self._tick()

    def _react(self, r: Reaction | None) -> None:
        if r is None:
            return
        now = time.monotonic()
        self._anim = (r.anim, now, now + r.secs, r.snack)
        pet = get_pet()
        if r.level_up:
            self._say = f"level up! ★ Lv {r.level_up} — {pet.title}"
        elif r.badges:
            from ..pet import BADGE_INFO

            info = BADGE_INFO[r.badges[0]]
            self._say = f"new badge! {info[1]} {info[2]}"
        elif r.say:
            self._say = r.say
        react = getattr(self.app, "_pet_react", None)
        if callable(react):
            react(r, bubble=False)
        save_pet()
        self._refresh_all()

    def _set_title(self, name: str) -> None:
        from rich.table import Table

        grid = Table.grid(expand=True)
        grid.add_column(ratio=1)
        grid.add_column(justify="right")
        grid.add_row(Text(f"♥  {name}"), Text("esc", style=ui.FG_DIM))
        try:
            self.query_one("#modal_title", Static).update(grid)
        except Exception:
            pass

    # ── actions ──────────────────────────────────────────────────────
    def action_close(self) -> None:
        self.dismiss(None)

    def action_pat(self) -> None:
        self._react(get_pet().pat())

    def action_feed(self) -> None:
        self._react(get_pet().feed())

    def action_play(self) -> None:
        self._react(get_pet().play())

    def action_nap(self) -> None:
        pet = get_pet()
        if pet.napping():
            pet.wake()
            self._react(Reaction("wave", 1.6, "*stretches* I'm up!"))
        else:
            self._react(pet.nap())

    def action_trick(self) -> None:
        self._react(get_pet().trick())

    def action_tip(self) -> None:
        from .pet_tips import random_tip

        self._react(Reaction("wave", 2.0, random_tip()))

    def action_fur(self) -> None:
        pet = get_pet()
        pet.fur = sprites.next_fur(pet.fur, pet.species)
        self._react(Reaction("trick", 1.4, f"new look: {sprites.FUR_LABELS.get(pet.fur, pet.fur)} ✦"))

    def action_wardrobe(self) -> None:
        pet = get_pet()
        owned = pet.unlocked()
        if not owned:
            self._say = f"nothing to wear yet — glasses unlock at Lv {ACCESSORIES[0][1]} ✦"
            self._refresh_all()
            return
        name = owned[self._wardrobe_i % len(owned)]
        self._wardrobe_i += 1
        on = pet.toggle_accessory(name)
        label = ACCESSORY_LABELS[name]
        self._react(Reaction("happy" if on else "wave", 1.4,
                             f"{label} on ✦" + (" (only while working)" if name == "glasses" else "")
                             if on else f"{label} off"))

    def action_switch(self) -> None:
        roster = get_roster()
        if len(roster.pets) < 2:
            self._say = "just me for now — press a to adopt a friend ♥"
            self._refresh_all()
            return
        pet = roster.switch()
        self._set_title(pet.name)
        self._t0 = time.monotonic()
        self._react(Reaction("wave", 1.6, f"hi, it's {pet.name}! ♥"))

    def action_adopt(self) -> None:
        adopt = getattr(self.app, "_pet_adopt_dialog", None)
        if callable(adopt):
            self.dismiss(None)
            adopt()

    def action_toggle_buddy(self) -> None:
        toggle = getattr(self.app, "_pet_set_enabled", None)
        enabled = getattr(self.app, "_pet_enabled", None)
        if not callable(toggle) or not callable(enabled):
            return
        on = not enabled()
        toggle(on)
        self._say = ("back where you can see me ♥" if on
                     else "I'll wait here — /pet whenever you miss me")
        self._refresh_all()

    def action_rename(self) -> None:
        from .text_input_modal import TextInputScreen

        pet = get_pet()

        def after(name: str | None) -> None:
            if name is None or not name.strip():
                return
            old = pet.name
            new = pet.rename(name)
            self._set_title(new)
            self._react(Reaction("happy", 1.8, f"{new}? I love it ♥" if new != old else "same as before ♥"))

        self.app.push_screen(
            TextInputScreen("Rename your pet", body=f"Currently {pet.name}.",
                            placeholder="a cute name…"),
            after,
        )


_ADOPT_BLURBS = {
    "cat": "curious, purrs a lot, knocks cups off shelves",
    "dog": "loyal, very excited about every commit",
    "bunny": "soft, zoomy, loves a cardboard box",
    "dragon": "an egg — hatches after 10 turns together",
}


class PetAdoptScreen(TuiModalScreen[str | None]):
    """Pick a species to adopt."""

    DEFAULT_CSS = (
        TUI_MODAL_CHROME_CSS
        + """
    PetAdoptScreen #modal {
        width: 70%;
        max-width: 72;
    }
    PetAdoptScreen OptionList {
        height: auto;
        max-height: 12;
    }
    """
    )

    BINDINGS = [Binding("escape", "cancel", "Cancel", show=True)]

    def compose(self) -> ComposeResult:
        with CenterMiddle():
            with Vertical(id="modal"):
                yield Static("✿  Adopt a pet", id="modal_title")
                yield Static(f"{len(get_roster().pets)} in your pen · they share the sidebar, "
                             "one at a time", id="modal_subtitle")
                yield OptionList(id="adopt_list")
                yield Static(hint_line(("↑↓", "choose"), ("↵", "adopt"), ("esc", "cancel")),
                             id="modal_hint")

    def on_mount(self) -> None:
        enable_mouse()
        opts = self.query_one("#adopt_list", OptionList)
        for species in SPECIES:
            opts.add_option(Option(picker_row(SPECIES_LABELS[species], detail=_ADOPT_BLURBS[species]),
                                   id=species))
        opts.highlighted = 0
        opts.focus()

    def on_unmount(self) -> None:
        disable_mouse()

    def on_option_list_option_selected(self, event: OptionList.OptionSelected) -> None:
        self.dismiss(event.option.id)

    def action_cancel(self) -> None:
        self.dismiss(None)
