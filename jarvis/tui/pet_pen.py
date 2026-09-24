"""Jarvis's home: a little pen docked at the bottom of the sidebar.

The kitty lives here all the time — it wanders, sits and grooms, hops, naps
when things are quiet, and paces while the agent works. Everything is one
click away:

* click the cat → pat it (hearts) · click the floor → it runs there
* ``pat · feed · play · nap · trick`` buttons under the stage
* click the name row → the full ``/pet`` card (rename, fur, stats)

Layout (``ROWS`` lines, sidebar width): name/level · speech (2 rows) ·
stage (8 rows) · floor · love/food/energy bars · buttons.
"""
from __future__ import annotations

import random
import time

from rich.text import Text
from textual.events import Click, MouseMove
from textual.widget import Widget

from ..pet import get_pet
from ..pet import sprites
from . import theme as ui

HEADER, SPEECH, STAGE0 = 0, 1, 3  # speech wraps onto two rows
FLOOR = STAGE0 + sprites.STAGE_ROWS
BARS = FLOOR + 1
BUTTONS = BARS + 1
ROWS = BUTTONS + 1

BUTTON_LABELS = ("pat", "feed", "play", "nap", "trick")
WALK_SPEED = 4.0     # columns / second
RUN_SPEED = 10.0
PACE_SPEED = 2.5     # while the agent works
DOZE_AFTER = 300.0


class PetPen(Widget):
    DEFAULT_CSS = """
    PetPen {
        dock: bottom;
        height: auto;
        width: 1fr;
        margin: 1 0 0 0;
        background: $jv-bg-1;
    }
    PetPen.hidden {
        display: none;
    }
    """

    can_focus = False

    def __init__(self, *, id: str | None = None) -> None:
        super().__init__(id=id)
        now = time.monotonic()
        self._t0 = now
        self._last = now
        self.x = 6.0
        self.facing = -1
        self.target: float | None = None
        self.speed = WALK_SPEED
        self.sit_until = now + 2.0
        self.hop_until = 0.0
        self.anim: tuple[str, float, float, str] | None = None  # (name, t0, until, snack)
        self.ball: list[float] | None = None                     # [x, vx]
        self.say_text = ""
        self.say_until = 0.0
        self._active_at = now
        self._hover_cat = False
        self._hover_button: int | None = None
        self._buttons: list[tuple[int, int, str]] = []
        self._key: tuple | None = None

    def on_mount(self) -> None:
        self.set_interval(1 / 10, self._tick)

    def get_content_height(self, container, viewport, width: int) -> int:
        return ROWS

    # ── geometry ─────────────────────────────────────────────────────
    @property
    def stage_w(self) -> int:
        return max(sprites.MINI_W + 2, int(self.content_region.width or self.size.width or 34))

    @property
    def max_x(self) -> float:
        return float(self.stage_w - sprites.MINI_W)

    def cat_span(self) -> tuple[int, int]:
        x = int(round(self.x))
        return x, x + sprites.MINI_W

    # ── inputs from the app ──────────────────────────────────────────
    def play(self, anim: str, secs: float = 1.6, snack: str = "") -> None:
        now = time.monotonic()
        self.anim = (anim, now, now + max(0.3, secs), snack)
        self._active_at = now
        self.target = None
        if anim == "play":
            start = 0.0 if self.x > self.max_x / 2 else self.max_x + sprites.MINI_W - 2
            self.ball = [start, 9.0 if start == 0.0 else -9.0]
            self.anim = (anim, now, now + max(4.0, secs), snack)
        elif anim in ("proud", "trick", "love", "happy", "wave"):
            self.hop_until = now + 0.45
        self._tick()

    def say(self, text: str, secs: float = 4.0) -> None:
        self.say_text = " ".join((text or "").split())
        self.say_until = time.monotonic() + secs
        self._key = None

    def poke(self) -> None:
        self._active_at = time.monotonic()

    @property
    def anim_active(self) -> bool:
        return self.anim is not None and time.monotonic() < self.anim[2]

    def run_to(self, col: int) -> None:
        self.anim = None
        self.target = max(0.0, min(self.max_x, col - sprites.MINI_W / 2))
        self.speed = RUN_SPEED
        self._active_at = time.monotonic()

    @property
    def dozing(self) -> bool:
        return (not getattr(self.app, "_busy", False)
                and time.monotonic() - self._active_at > DOZE_AFTER)

    # ── behaviour ────────────────────────────────────────────────────
    def _step(self, now: float, dt: float) -> tuple[str, float, bool, str]:
        """Advance the simulation; return (anim, t, walking, snack)."""
        busy = bool(getattr(self.app, "_busy", False))
        pet = get_pet()
        t = now - self._t0
        walking = False

        def walk_toward(goal: float, speed: float) -> bool:
            nonlocal walking
            d = goal - self.x
            if abs(d) < 0.35:
                self.x = goal
                return True
            self.facing = 1 if d > 0 else -1
            self.x += max(-speed * dt, min(speed * dt, d))
            walking = True
            return False

        if self.anim is not None:
            name, t0, until, snack = self.anim
            if now >= until:
                self.anim, self.ball = None, None
                self.sit_until = now + random.uniform(1.5, 3.5)
            else:
                if name == "play" and self.ball is not None:
                    bx, vx = self.ball
                    bx += vx * dt
                    right = self.stage_w - 2
                    if bx <= 0 or bx >= right:
                        vx, bx = -vx, max(0.0, min(float(right), bx))
                    cx = self.x + sprites.MINI_W / 2
                    if abs((bx + 1) - cx) < 3:  # caught up with it → kick it away
                        speed = min(16.0, abs(vx) * 1.1 + 1.0)
                        vx = speed if bx + 1 >= cx else -speed
                    self.ball = [bx, vx]
                    walk_toward(max(0.0, min(self.max_x, bx - sprites.MINI_W / 2 + 1)), RUN_SPEED)
                return name, now - t0, walking, snack

        if self._hover_cat:
            return "happy", t, False, ""
        if self.target is not None:
            if walk_toward(self.target, self.speed):
                if self.speed == RUN_SPEED:
                    self.hop_until = now + 0.4  # a happy little landing hop
                self.target = None
                self.speed = WALK_SPEED
                self.sit_until = now + random.uniform(2.0, 6.0)
            return ("work" if busy else "idle"), t, walking, ""
        if busy:
            # Pace slowly while the agent thinks.
            if now >= self.sit_until:
                lo, hi = self.max_x * 0.15, self.max_x * 0.85
                self.target = hi if self.x < self.max_x / 2 else lo
                self.speed = PACE_SPEED
            return "work", t, False, ""
        if pet.napping() or self.dozing:
            return "sleep", t, False, ""
        if now >= self.sit_until:
            roll = random.random()
            if roll < 0.62:
                self.target = random.uniform(0, self.max_x)
                self.speed = WALK_SPEED
            elif roll < 0.74:
                self.hop_until = now + 0.45
                self.sit_until = now + random.uniform(1.5, 3.0)
            else:
                self.sit_until = now + random.uniform(2.0, 5.0)
        return "idle", t, False, ""

    def _look(self, anim: str, t: float) -> int:
        if anim != "idle":
            return 0
        cycle = t % 6.5
        return -1 if 3.8 < cycle < 4.6 else (1 if 4.6 <= cycle < 5.4 else 0)

    def _tick(self) -> None:
        if not self.display:
            return
        now = time.monotonic()
        dt = min(0.3, max(0.0, now - self._last))
        self._last = now
        self.x = max(0.0, min(self.max_x, self.x))
        self._frame = self._step(now, dt)
        key = self._frame_key(now)
        if key != self._key:
            self._key = key
            self.refresh()

    def _frame_key(self, now: float) -> tuple:
        anim, t, walking, snack = self._frame
        return (anim, int(t * 8), round(self.x), self.facing, walking, self._lift(now, anim, t),
                self._speech(now), self._hover_button, ui.active_theme(), get_pet().fur,
                self.ball and round(self.ball[0]), round(get_pet().happiness), round(get_pet().fullness),
                round(get_pet().energy), get_pet().level, get_pet().name)

    def _lift(self, now: float, anim: str, t: float) -> int:
        if now < self.hop_until:
            return 2 if (self.hop_until - now) > 0.15 else 1
        if anim == "trick":
            return 2 if int(t * 5) % 4 in (1, 2) else 0
        if anim == "play":
            return int(t * 6) % 2
        return 0

    def _speech(self, now: float) -> str:
        if self.say_text and now < self.say_until:
            return self.say_text
        return ""

    # ── rendering ────────────────────────────────────────────────────
    def render(self) -> Text:
        now = time.monotonic()
        frame = getattr(self, "_frame", None) or ("idle", 0.0, False, "")
        anim, t, walking, snack = frame
        pet = get_pet()
        w = self.stage_w
        colors = sprites.fur_colors(pet.fur)
        lines: list[Text] = []

        head = Text(no_wrap=True, overflow="ellipsis")
        head.append("♥ ", style=sprites.PINK)
        head.append(pet.name, style=f"bold {colors['line']}")
        head.append(f"  Lv {pet.level}", style=f"bold {ui.ACCENT}")
        head.append(f" · {pet.title}", style=ui.FG_DIM)
        head.truncate(w, overflow="ellipsis")
        lines.append(head)

        said = self._speech(now)
        lines.extend(_wrap2(said or pet.mood_line(), w,
                            ui.FG if said else f"italic {ui.FG_DIM}"))

        cat_x = int(round(self.x))
        bowl_x = None
        if anim == "eat":
            # Centered under the face (the head sits on the side away from the tail).
            bowl_x = cat_x + (7 if self.facing > 0 else 4)
        lines.extend(sprites.pen_stage(
            w, cat_x, anim, t, pet.fur, walking=walking, facing=self.facing,
            look=self.facing if walking else self._look(anim, t), lift=self._lift(now, anim, t),
            ball_x=int(round(self.ball[0])) if self.ball else None, bowl_x=bowl_x, snack=snack,
            deco=self._deco(anim, t, cat_x, w),
        ))

        lines.append(Text("▔" * w, style=ui.blend(ui.BG_1, ui.FG_DIM, 0.45)))
        lines.append(self._bars(pet, w))
        lines.append(self._button_row(w))
        return Text("\n").join(lines)

    def _deco(self, anim: str, t: float, cat_x: int, w: int) -> list[tuple[int, int, str, str]]:
        """Floating glyphs around the cat (hearts, z's, sparkles, dots)."""
        side = cat_x + sprites.MINI_W if cat_x + sprites.MINI_W + 3 <= w else cat_x - 3
        step = int(t * 3)
        out: list[tuple[int, int, str, str]] = []
        if anim == "love":
            for k in range(3):
                y = 5 - (step + k * 2) % 6
                out.append((side + (k + step) % 3, y, "♥", sprites.PINK))
        elif anim == "sleep":
            for k, glyph in enumerate(("z", "Z", "z")):
                y = 4 - (step + k) % 5
                out.append((side + (step + k) % 3, y, glyph, "#9aa3b5"))
        elif anim in ("proud", "trick"):
            for k, y in enumerate((0, 2, 1)):
                if (step + k) % 3 != 2:
                    out.append((side + k, y, "✦" if (step + k) % 2 else "✧", sprites.GOLD))
        elif anim == "work":
            out.append((side, 1, "." * (1 + step % 3), "#9aa3b5"))
        elif anim == "surprised":
            out.append((side, 0, "!", sprites.GOLD))
        elif anim == "ouch":
            out.append((side, 1, ";", "#7fb4ff"))
        elif anim == "wave":
            out.append((side, 2, (")", "))")[step % 2], "#9aa3b5"))
        return out

    def _bars(self, pet, w: int) -> Text:
        bar_w = max(3, (w - 12) // 3)
        out = Text(no_wrap=True)
        for i, (icon, value, good) in enumerate((
            ("♥", pet.happiness, sprites.PINK),
            ("◆", pet.fullness, ui.OK),
            ("✦", pet.energy, sprites.GOLD),
        )):
            color = ui.ERR if value < 25 else (ui.WARN if value < 45 else good)
            if i:
                out.append("  ")
            out.append(f"{icon} ", style=color)
            filled = round(max(0.0, min(100.0, value)) / 100 * bar_w)
            out.append("━" * filled, style=color)
            out.append("━" * (bar_w - filled), style=ui.blend(ui.BG_1, ui.FG_DIM, 0.3))
        return out

    def _button_row(self, w: int) -> Text:
        out = Text(no_wrap=True)
        self._buttons = []
        for i, label in enumerate(BUTTON_LABELS):
            if i:
                out.append(" · ", style=ui.FG_DIM)
            start = out.cell_len
            hot = i == self._hover_button
            out.append(label, style=f"bold underline {ui.ACCENT}" if hot else ui.FG_MUTE)
            self._buttons.append((start, out.cell_len, label))
        return out

    # ── mouse ────────────────────────────────────────────────────────
    def _button_at(self, x: int) -> int | None:
        for i, (start, end, _label) in enumerate(self._buttons):
            if start <= x < end:
                return i
        return None

    def _on_cat(self, x: int, y: int) -> bool:
        a, b = self.cat_span()
        return STAGE0 + 1 <= y < FLOOR and a <= x < b

    def on_mouse_move(self, event: MouseMove) -> None:
        hover_btn = self._button_at(event.x) if event.y == BUTTONS else None
        hover_cat = self._on_cat(event.x, event.y)
        if (hover_btn, hover_cat) != (self._hover_button, self._hover_cat):
            self._hover_button, self._hover_cat = hover_btn, hover_cat
            self._key = None
            self._tick()

    def on_leave(self) -> None:
        self._hover_button, self._hover_cat = None, False
        self._key = None

    def on_click(self, event: Click) -> None:
        event.stop()
        app = self.app
        x, y = event.x, event.y
        if y == BUTTONS:
            i = self._button_at(x)
            if i is not None:
                action = getattr(app, "_pet_action", None)
                if callable(action):
                    action(BUTTON_LABELS[i])
            return
        if y == HEADER:
            opener = getattr(app, "_open_pet_card", None)
            if callable(opener):
                opener()
            return
        if self._on_cat(x, y):
            pat = getattr(app, "_pet_pat", None)
            if callable(pat):
                pat()
            return
        if STAGE0 <= y <= FLOOR:
            self.run_to(x)
            self._tick()

    @property
    def speaking(self) -> str:
        return self._speech(time.monotonic())


def _wrap2(text: str, width: int, style: str) -> list[Text]:
    """Word-wrap ``text`` into exactly two rows (ellipsis if it overflows)."""
    words, rows, cur = text.split(), [], ""
    for word in words:
        cand = f"{cur} {word}".strip()
        if len(cand) <= width or not cur:
            cur = cand
        else:
            rows.append(cur)
            cur = word
    if cur:
        rows.append(cur)
    if len(rows) > 2:
        rows = [rows[0], " ".join(rows[1:])]
    out = []
    for row in (rows + ["", ""])[:2]:
        line = Text(row, style=style, no_wrap=True, overflow="ellipsis")
        line.truncate(width, overflow="ellipsis")
        out.append(line)
    return out

