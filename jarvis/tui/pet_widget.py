"""Jarvis in the TUI: the kitty at the end of the input box + its speech bubble.

``PetBuddy`` renders :func:`jarvis.pet.sprites.buddy_lines` at ~8 fps and only
repaints when the frame actually changes. Its resting pose follows the app
(working while a turn runs, glancing at your text while you type, dozing
after a few quiet minutes); one-shot reactions (love, eat, proud, …) play on
top via :meth:`PetBuddy.play`.

``PetBubble`` is a one-row overlay drawn in the blank row right above the
composer (the transcript's bottom padding / the activity line's margin), so
speaking never shifts the layout.
"""
from __future__ import annotations

import time

from rich.text import Text
from textual.events import Click
from textual.widget import Widget

from ..pet import get_pet
from ..pet import sprites
from . import theme as ui

DOZE_AFTER = 300.0   # seconds of quiet before Jarvis dozes off
LOOK_SECS = 1.4      # keeps glancing at your text this long after a keystroke


class PetBuddy(Widget):
    """The composer kitty. Click to pet · double-click for the card."""

    DEFAULT_CSS = """
    PetBuddy {
        width: 10;
        height: 3;
        margin: 0 0 0 1;
        background: transparent;
    }
    PetBuddy.hidden {
        display: none;
    }
    """

    can_focus = False

    def __init__(self, *, id: str | None = None, classes: str | None = None) -> None:
        super().__init__(id=id, classes=classes)
        self._t0 = time.monotonic()
        self._anim: tuple[str, float, float, str] | None = None  # (name, t0, until, snack)
        self._typed_at = 0.0
        self._active_at = time.monotonic()
        self._hover = False
        self._key: tuple | None = None
        self.tooltip = "Jarvis ♥ click to pet · double-click (or /pet) for the pet card"

    def on_mount(self) -> None:
        self.set_interval(1 / 8, self._tick)

    # ── inputs from the app ──────────────────────────────────────────
    def play(self, anim: str, secs: float = 1.6, snack: str = "") -> None:
        now = time.monotonic()
        self._anim = (anim, now, now + max(0.2, secs), snack)
        self._active_at = now
        self._tick()

    def typed(self) -> None:
        now = time.monotonic()
        self._typed_at = now
        self._active_at = now

    def poke(self) -> None:
        """Any sign of life (turn start/end) — resets the doze timer."""
        self._active_at = time.monotonic()

    @property
    def anim_active(self) -> bool:
        return self._anim is not None and time.monotonic() < self._anim[2]

    @property
    def dozing(self) -> bool:
        return (not getattr(self.app, "_busy", False)
                and time.monotonic() - self._active_at > DOZE_AFTER)

    # ── frame selection ──────────────────────────────────────────────
    def current(self) -> tuple[str, float, int, str]:
        """(anim, t, look, snack) for right now."""
        now = time.monotonic()
        if self._anim is not None:
            name, t0, until, snack = self._anim
            if now < until:
                return name, now - t0, 0, snack
            self._anim = None
        pet = get_pet()
        if getattr(self.app, "_busy", False):
            return "work", now - self._t0, 0, ""
        if pet.napping() or self.dozing:
            return "sleep", now - self._t0, 0, ""
        if self._hover:
            return "happy", now - self._t0, 0, ""
        look = -1 if now - self._typed_at < LOOK_SECS else 0
        return "idle", now - self._t0, look, ""

    def _lines(self) -> list[Text]:
        anim, t, look, _snack = self.current()
        pet = get_pet()
        mood = "sleepy" if pet.mood() == "sleepy" else "content"
        species = "egg" if pet.is_egg else pet.species
        return sprites.buddy_lines(anim, t, pet.fur, look=look, mood=mood, species=species)

    def _tick(self) -> None:
        if not self.display:
            return
        lines = self._lines()
        key = (tuple(line.plain for line in lines), ui.active_theme(), get_pet().fur, id(get_pet()))
        if key != self._key:
            self._key = key
            self._cached = lines
            self.refresh()

    def render(self) -> Text:
        lines = getattr(self, "_cached", None) or self._lines()
        return Text("\n").join(lines)

    # ── mouse ────────────────────────────────────────────────────────
    def on_enter(self) -> None:
        self._hover = True
        self._tick()

    def on_leave(self) -> None:
        self._hover = False
        self._tick()

    def on_click(self, event: Click) -> None:
        event.stop()
        app = self.app
        if getattr(event, "chain", 1) >= 2:
            opener = getattr(app, "_open_pet_card", None)
        else:
            opener = getattr(app, "_pet_pat", None)
        if callable(opener):
            opener()


class PetBubble(Widget):
    """One-row speech bubble floating just above the kitty."""

    DEFAULT_CSS = """
    PetBubble {
        layer: overlay;
        position: absolute;
        width: auto;
        height: 1;
        background: transparent;
        display: none;
    }
    """

    can_focus = False

    def __init__(self, *, id: str | None = None) -> None:
        super().__init__(id=id)
        self._text = ""
        self._timer = None

    def say(self, text: str, secs: float, anchor: Widget, above: Widget) -> None:
        """Show ``text`` right-aligned over ``anchor``, one row above ``above``."""
        text = " ".join((text or "").split())
        if not text:
            return
        max_w = max(12, min(64, (self.app.size.width or 80) - 10))
        if len(text) > max_w - 4:
            text = text[: max_w - 5].rstrip() + "…"
        self._text = text
        width = self._bubble().cell_len
        try:
            right = anchor.region.right
            y = above.region.y - 1
        except Exception:
            return
        if y < 0 or not anchor.region.width:
            return
        self.styles.offset = (max(0, right - width), y)
        self.styles.width = width
        self.display = True
        self.refresh(layout=True)
        if self._timer is not None:
            self._timer.stop()
        self._timer = self.set_timer(max(1.0, secs), self.hide)

    def hide(self) -> None:
        self._timer = None
        self.display = False

    @property
    def text(self) -> str:
        return self._text if self.display else ""

    def _bubble(self) -> Text:
        bg = ui.blend(ui.BG_2, ui.ACCENT, 0.18)
        out = Text(no_wrap=True)
        out.append("▐", style=bg)
        out.append(f" {self._text} ", style=f"{ui.FG} on {bg}")
        out.append("▌", style=bg)
        out.append("◣", style=bg)
        return out

    def render(self) -> Text:
        return self._bubble()
