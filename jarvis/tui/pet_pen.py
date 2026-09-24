"""The pets' home: a little pen docked at the bottom of the sidebar.

The active pet lives here all the time. It wanders, sits and grooms, hops,
naps when things are quiet, and paces while the agent works. Sometimes a toy
shows up on its own (a cardboard box to nap in, a butterfly to chase, a cup
on a shelf that just *has* to be knocked off). The scenery follows the clock
(clouds by day, moon and stars at night, snow in December, a pumpkin in
October).

Everything is one click away:

* hover the floor → a laser dot the pet chases · click the pet → pat it ·
  click the floor → it runs there
* ``pat · feed · play · nap · trick`` and ``fish · focus · pets · card``
* ``fish`` starts a 20-second game: click the falling fish before they land
* ``focus`` starts a 25-minute focus timer (the pet naps on your keyboard)

Layout (``ROWS`` lines, sidebar width): name/level · speech (2) · stage (10)
· floor · love/food/energy bars · status (today / focus / game) · buttons (2).
"""
from __future__ import annotations

import math
import random
import time
from datetime import datetime

from rich.text import Text
from textual.events import Click, MouseMove
from textual.widget import Widget

from ..pet import get_pet, get_roster
from ..pet import sprites
from . import theme as ui

STAGE_ROWS = 10
HEADER, SPEECH, STAGE0 = 0, 1, 3  # speech wraps onto two rows
FLOOR = STAGE0 + STAGE_ROWS
BARS = FLOOR + 1
STATUS = BARS + 1
BUTTONS = STATUS + 1
BUTTONS2 = BUTTONS + 1
ROWS = BUTTONS2 + 1

BUTTON_LABELS = ("pat", "feed", "play", "nap", "trick")
BUTTON_LABELS2 = ("fish", "focus", "pets", "card")
WALK_SPEED = 4.0     # columns / second
RUN_SPEED = 10.0
PACE_SPEED = 2.5     # while the agent works
DOZE_AFTER = 300.0
GAME_SECS = 20.0
TOY_EVERY = (45.0, 120.0)
H_PX = STAGE_ROWS * 2


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
        self.laser: tuple[int, int] | None = None                # (col, y px)
        self._laser_at = 0.0
        self._pounce_at = 0.0
        self.toy: dict | None = None
        self.next_toy_at = now + random.uniform(*TOY_EVERY)
        self.game: dict | None = None
        self.say_text = ""
        self.say_until = 0.0
        self._active_at = now
        self._hover_cat = False
        self._hover_button: tuple[int, int] | None = None
        self._buttons: dict[int, list[tuple[int, int, str]]] = {}
        self._key: tuple | None = None
        self._walking = False
        self._frame = ("idle", 0.0, False, "")

    def on_mount(self) -> None:
        self.set_interval(1 / 10, self._tick)

    def get_content_height(self, container, viewport, width: int) -> int:
        return ROWS

    # ── geometry ─────────────────────────────────────────────────────
    @property
    def stage_w(self) -> int:
        return max(sprites.SPRITE_W + 4, int(self.content_region.width or self.size.width or 34))

    @property
    def pet_w(self) -> int:
        return sprites.SPRITE_W + get_pet().stage

    @property
    def max_x(self) -> float:
        return float(max(0, self.stage_w - self.pet_w))

    def cat_span(self) -> tuple[int, int]:
        if get_pet().is_egg:
            a = (self.stage_w - sprites.SPRITE_W) // 2
            return a, a + sprites.SPRITE_W
        x = int(round(self.x))
        return x, x + self.pet_w

    # ── inputs from the app ──────────────────────────────────────────
    def play(self, anim: str, secs: float = 1.6, snack: str = "") -> None:
        now = time.monotonic()
        self.anim = (anim, now, now + max(0.3, secs), snack)
        self._active_at = now
        self.target = None
        if anim == "play":
            start = 0.0 if self.x > self.max_x / 2 else float(self.stage_w - 2)
            self.ball = [start, 9.0 if start == 0.0 else -9.0]
            self.anim = (anim, now, now + max(4.0, secs), snack)
        elif anim in ("proud", "trick", "love", "happy", "wave", "cheer", "party", "hatch"):
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
        if get_pet().is_egg:
            self.play("wobble", 0.8)
            return
        self.anim = None
        self.target = max(0.0, min(self.max_x, col - self.pet_w / 2))
        self.speed = RUN_SPEED
        self._active_at = time.monotonic()

    @property
    def dozing(self) -> bool:
        return (not getattr(self.app, "_busy", False)
                and time.monotonic() - self._active_at > DOZE_AFTER)

    def _focus(self) -> tuple[str, float] | None:
        fn = getattr(self.app, "_pet_focus_state", None)
        return fn() if callable(fn) else None

    # ── the fish game ────────────────────────────────────────────────
    def start_game(self) -> None:
        now = time.monotonic()
        self.anim, self.toy, self.target, self.ball = None, None, None, None
        self.game = {"t0": now, "until": now + GAME_SECS, "fish": [], "you": 0, "pet": 0,
                     "next": now + 0.4, "pops": []}
        self.say("catch the fish! click them before they land ✦", 3.0)
        self._key = None

    def stop_game(self) -> None:
        if self.game is None:
            return
        caught = self.game["you"] + self.game["pet"]
        self.game = None
        done = getattr(self.app, "_pet_fish_done", None)
        if callable(done):
            done(caught)

    def _step_game(self, now: float, dt: float, walk_toward) -> tuple[str, float, bool, str]:
        g = self.game
        assert g is not None
        if now >= g["until"]:
            self.stop_game()
            return "happy", now - self._t0, False, ""
        if now >= g["next"]:
            g["next"] = now + random.uniform(0.6, 1.1)
            g["fish"].append({"x": random.randint(0, self.stage_w - 6), "y": 0.0,
                              "vy": random.uniform(4.0, 7.5), "kind": random.choice(("fish", "fish2"))})
        head_top = H_PX - sprites.SPRITE_H + 5
        cx0, cx1 = self.cat_span()
        for fish in list(g["fish"]):
            fish["y"] += fish["vy"] * dt
            if fish["y"] >= H_PX - 3:
                g["fish"].remove(fish)
            elif fish["y"] >= head_top - 2 and cx0 + 2 <= fish["x"] + 3 <= cx1 - 3:
                g["fish"].remove(fish)
                g["pet"] += 1
                self.hop_until = now + 0.3
        g["pops"] = [p for p in g["pops"] if now - p[2] < 0.6]
        if g["fish"]:
            low = max(g["fish"], key=lambda f: f["y"])
            walk_toward(max(0.0, min(self.max_x, low["x"] + 3 - self.pet_w / 2)), RUN_SPEED)
        chomp = self.hop_until > now
        return ("eat" if chomp else "happy"), now - self._t0, self._walking, ""

    def _catch_at(self, col: int, row: int) -> bool:
        g = self.game
        if g is None:
            return False
        py = (row - STAGE0) * 2
        for fish in g["fish"]:
            if fish["x"] - 1 <= col <= fish["x"] + 6 and abs(fish["y"] + 1 - py) <= 2.5:
                g["fish"].remove(fish)
                g["you"] += 1
                g["pops"].append((col, row - STAGE0, time.monotonic()))
                self._key = None
                return True
        return False

    # ── toys that show up on their own ───────────────────────────────
    def start_toy(self, kind: str | None = None, now: float | None = None) -> None:
        now = now or time.monotonic()
        kind = kind or random.choice(("box", "butterfly", "cup"))
        w = self.stage_w
        if kind == "box":
            self.toy = {"kind": "box", "x": random.randint(0, max(0, w - 12)), "phase": "go"}
        elif kind == "butterfly":
            x0 = random.choice((0.0, w - 4.0))
            self.toy = {"kind": "butterfly", "t0": now, "x0": x0, "vx": 2.2 if x0 == 0.0 else -2.2}
        else:
            # The shelf hangs at the right wall; the pet stops beside it (not under it).
            self.toy = {"kind": "cup", "sx": max(0, w - 9), "phase": "walk", "cy": 2.0, "vy": 0.0}
        self.toy["started"] = now

    def _step_toy(self, now: float, dt: float, walk_toward) -> tuple[str, float, bool, str] | None:
        toy = self.toy
        if toy is None:
            return None
        t = now - self._t0
        kind = toy["kind"]
        if now - toy["started"] > 40:
            self.toy = None
            return None
        if kind == "box":
            goal = max(0.0, min(self.max_x, toy["x"] - 3 - get_pet().stage / 2))
            if toy["phase"] == "go":
                if walk_toward(goal, WALK_SPEED):
                    toy["phase"], toy["in_at"] = "in", now
                    self.hop_until = now + 0.45
                    get_pet()._bump("boxes")
                    self.say(random.choice(("if I fits, I sits ✦", "*hops in the box*", "my box now ♥")), 3.5)
                return "idle", t, self._walking, ""
            inside = now - toy.get("in_at", now)
            if inside > 16:
                self.toy = None
                self.hop_until = now + 0.45
                self.sit_until = now + 2.0
                return "happy", t, False, ""
            return ("happy" if inside < 4 else "sleep"), t, False, ""
        if kind == "butterfly":
            age = now - toy["t0"]
            bx = toy["x0"] + toy["vx"] * age + 2.5 * math.sin(age * 1.7)
            toy["bx"] = bx
            toy["by"] = 3 + 2.5 * math.sin(age * 2.3) - max(0.0, age - 12) * 3
            if age > 16 or not (-4 < bx < self.stage_w + 1):
                self.toy = None
                get_pet()._bump("butterflies")
                return None
            walk_toward(max(0.0, min(self.max_x, bx - self.pet_w / 2)), RUN_SPEED * 0.7)
            if abs((self.x + self.pet_w / 2) - bx) < 3 and now - self._pounce_at > 1.4:
                self._pounce_at = now
                self.hop_until = now + 0.45
            return "happy", t, self._walking, ""
        if kind == "cup":
            phase = toy["phase"]
            if phase == "walk":
                if walk_toward(max(0.0, min(self.max_x, toy["sx"] - 13.0 - get_pet().stage)), WALK_SPEED):
                    toy["phase"], toy["at"] = "look", now
                return "idle", t, self._walking, ""
            if phase == "look":
                if now - toy["at"] > 1.4:
                    toy["phase"] = "fall"
                    self.hop_until = now + 0.45
                return "surprised", now - toy["at"], False, ""
            if phase == "fall":
                toy["vy"] += 40 * dt
                toy["cy"] += toy["vy"] * dt
                if toy["cy"] >= H_PX - 3:
                    toy["phase"], toy["at"] = "crash", now
                    get_pet()._bump("cups")
                    self.say(random.choice(("*crash* …it was like that when I got here",
                                            "oops ✦", "gravity: confirmed")), 4.0)
                return "happy", t, False, ""
            if now - toy["at"] > 3.5:
                self.toy = None
                self.sit_until = now + 2.0
                return None
            return "proud", now - toy["at"], False, ""
        self.toy = None
        return None

    # ── behaviour ────────────────────────────────────────────────────
    def _step(self, now: float, dt: float) -> tuple[str, float, bool, str]:
        """Advance the simulation; return (anim, t, walking, snack)."""
        busy = bool(getattr(self.app, "_busy", False))
        pet = get_pet()
        t = now - self._t0
        self._walking = False

        def walk_toward(goal: float, speed: float) -> bool:
            if pet.is_egg:
                return True
            d = goal - self.x
            if abs(d) < 0.35:
                self.x = goal
                return True
            self.facing = 1 if d > 0 else -1
            self.x += max(-speed * dt, min(speed * dt, d))
            self._walking = True
            return False

        if self.game is not None:
            return self._step_game(now, dt, walk_toward)

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
                    cx = self.x + self.pet_w / 2
                    if abs((bx + 1) - cx) < 3:  # caught up with it → kick it away
                        speed = min(16.0, abs(vx) * 1.1 + 1.0)
                        vx = speed if bx + 1 >= cx else -speed
                    self.ball = [bx, vx]
                    walk_toward(max(0.0, min(self.max_x, bx - self.pet_w / 2 + 1)), RUN_SPEED)
                return name, now - t0, self._walking, snack

        if pet.is_egg:
            return ("sleep" if pet.napping() else "idle"), t, False, ""
        if self._hover_cat:
            return "happy", t, False, ""
        if self.laser is not None and now - self._laser_at < 3.0:
            lx, _ly = self.laser
            if walk_toward(max(0.0, min(self.max_x, lx - self.pet_w / 2)), RUN_SPEED):
                if now - self._pounce_at > 1.2:
                    self._pounce_at = now
                    self.hop_until = now + 0.45
            return "happy", t, self._walking, ""
        if self.target is not None:
            if walk_toward(self.target, self.speed):
                if self.speed == RUN_SPEED:
                    self.hop_until = now + 0.4  # a happy little landing hop
                self.target = None
                self.speed = WALK_SPEED
                self.sit_until = now + random.uniform(2.0, 6.0)
            return ("work" if busy else "idle"), t, self._walking, ""
        focus = self._focus()
        if focus and focus[0] == "focus":
            walk_toward(self.max_x / 2, WALK_SPEED)
            if self._walking:
                return "idle", t, True, ""
            return ("work" if busy else "sleep"), t, False, ""
        if busy:
            if now >= self.sit_until:
                lo, hi = self.max_x * 0.15, self.max_x * 0.85
                self.target = hi if self.x < self.max_x / 2 else lo
                self.speed = PACE_SPEED
            return "work", t, False, ""
        toy = self._step_toy(now, dt, walk_toward)
        if toy is not None:
            return toy
        if pet.napping() or self.dozing:
            return "sleep", t, False, ""
        if now >= self.next_toy_at and self.toy is None:
            self.next_toy_at = now + random.uniform(*TOY_EVERY)
            self.start_toy(now=now)
            return "idle", t, False, ""
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
        anim, t, walking, _snack = self._frame
        pet = get_pet()
        toy = self.toy or {}
        g = self.game or {}
        return (anim, int(t * 8), round(self.x), self.facing, walking, self._lift(now, anim, t),
                self._speech(now), self._hover_button, ui.active_theme(), pet.fur, pet.name,
                self.ball and round(self.ball[0]), round(pet.happiness), round(pet.fullness),
                round(pet.energy), pet.level, pet.species, pet.hatch_left, id(pet),
                self.laser, toy.get("phase"), round(toy.get("bx", 0)), round(toy.get("cy", 0)),
                tuple((f["x"], int(f["y"])) for f in g.get("fish", ())), g.get("you"),
                int(now) if (self._focus() or g) else 0, datetime.now().hour)

    def _lift(self, now: float, anim: str, t: float) -> int:
        if now < self.hop_until:
            return 2 if (self.hop_until - now) > 0.15 else 1
        if anim == "trick":
            return 2 if int(t * 5) % 4 in (1, 2) else 0
        if anim in ("play", "cheer", "party"):
            return int(t * 6) % 2
        return 0

    def _speech(self, now: float) -> str:
        if self.say_text and now < self.say_until:
            return self.say_text
        return ""

    # ── rendering ────────────────────────────────────────────────────
    def render(self) -> Text:
        now = time.monotonic()
        anim, t, walking, snack = self._frame
        pet = get_pet()
        w = self.stage_w
        busy = bool(getattr(self.app, "_busy", False))
        focus = self._focus()
        colors = sprites.fur_colors(pet.fur)
        lines: list[Text] = []

        head = Text(no_wrap=True, overflow="ellipsis")
        head.append("♥ ", style=sprites.PINK)
        head.append(pet.name, style=f"bold {colors['line']}")
        head.append(f"  Lv {pet.level}", style=f"bold {ui.ACCENT}")
        head.append(f" · {pet.title}", style=ui.FG_DIM)
        roster = get_roster()
        if len(roster.pets) > 1:
            tag = f" {roster.active + 1}/{len(roster.pets)}"
            head.truncate(max(1, w - len(tag)), overflow="ellipsis")
            head.pad_right(w - len(tag) - head.cell_len)
            head.append(tag, style=ui.FG_DIM)
        head.truncate(w, overflow="ellipsis")
        lines.append(head)

        said = self._speech(now)
        lines.extend(_wrap2(said or pet.mood_line(), w, ui.FG if said else f"italic {ui.FG_DIM}"))

        lines.extend(self._stage(now, anim, t, walking, snack, pet, busy, focus, w))
        lines.append(Text("▔" * w, style=ui.blend(ui.BG_1, ui.FG_DIM, 0.45)))
        lines.append(self._bars(pet, w))
        lines.append(self._status(pet, focus, w))
        lines.append(self._button_row(0, BUTTON_LABELS, w))
        labels2 = ("stop" if self.game else "fish",
                   "stop focus" if focus and focus[0] == "focus" else "focus",
                   "pets", "card")
        lines.append(self._button_row(1, labels2, w))
        return Text("\n").join(lines)

    def _stage(self, now, anim, t, walking, snack, pet, busy, focus, w) -> list[Text]:
        cat_x = int(round(self.x))
        species = "egg" if pet.is_egg else pet.species
        left = pet.hatch_left
        cracks = 0 if left > 6 else 1 if left > 3 else 2 if left > 1 else 3
        if pet.is_egg:
            cat_x = self.cat_span()[0]
            anim = anim if anim in ("wobble", "love", "surprised", "sleep", "hatch") else "idle"
        props, sky_deco = sprites.sky(datetime.now(), w, now - self._t0)
        front: list = []
        pixels: list = []
        deco = list(sky_deco)
        bowl_x = None
        if anim == "eat" and not self.game:
            bowl_x = cat_x + (7 if self.facing > 0 else 4)
        toy = self.toy or {}
        if toy.get("kind") == "box":
            front.append(("box", toy["x"], -1))
        elif toy.get("kind") == "butterfly" and "bx" in toy:
            props.append(("butterfly", int(toy["bx"]), int(toy["by"])))
        elif toy.get("kind") == "cup":
            props.append(("shelf", toy["sx"], 6))
            if toy["phase"] in ("walk", "look", "fall"):
                props.append(("cup", toy["sx"] + 3, int(toy["cy"]) + (1 if toy["phase"] != "fall" else 0)))
            else:
                front.append(("shards", toy["sx"] + 2, -1))
        if focus and focus[0] == "focus" and not walking:
            front.append(("keyboard", cat_x + 2 + pet.stage // 2, -1))
        if self.game:
            for fish in self.game["fish"]:
                front.append((fish["kind"], fish["x"], int(fish["y"])))
            for col, row, _t in self.game["pops"]:
                deco.append((col, row, "✦", sprites.GOLD))
        if self.laser is not None and now - self._laser_at < 3.0 and not self.game:
            lx, ly = self.laser
            pixels.append((lx, ly, sprites.LASER))
        deco += self._deco(anim, t, cat_x, w, pet)
        return sprites.pen_stage(
            w, cat_x, anim, t, pet.fur, species=species, walking=walking, facing=self.facing,
            look=self.facing if walking else self._look(anim, t), lift=self._lift(now, anim, t),
            outfit=pet.outfit(working=busy or bool(focus and focus[0] == "focus")), stage=pet.stage,
            egg_cracks=cracks, rows=STAGE_ROWS,
            ball_x=int(round(self.ball[0])) if self.ball else None, bowl_x=bowl_x, snack=snack,
            props=props, front=front, pixels=pixels, deco=deco,
            confetti=1.0 if anim in ("cheer", "party", "hatch") else 0.0,
        )

    def _deco(self, anim: str, t: float, cat_x: int, w: int, pet) -> list[tuple[int, int, str, str]]:
        """Floating glyphs around the pet (hearts, z's, sparkles, dots)."""
        pw = self.pet_w
        side = cat_x + pw if cat_x + pw + 3 <= w else cat_x - 3
        step = int(t * 3)
        out: list[tuple[int, int, str, str]] = []
        top = STAGE_ROWS - (sprites.SPRITE_H + pet.stage) // 2 + 2  # around the head
        if anim == "love":
            for k in range(3):
                out.append((side + (k + step) % 3, top + 3 - (step + k * 2) % 6, "♥", sprites.PINK))
        elif anim == "sleep":
            for k, glyph in enumerate(("z", "Z", "z")):
                out.append((side + (step + k) % 3, top + 2 - (step + k) % 5, glyph, "#9aa3b5"))
        elif anim in ("proud", "trick", "cheer", "party", "hatch"):
            for k, y in enumerate((0, 2, 1)):
                if (step + k) % 3 != 2:
                    out.append((side + k, top + y, "✦" if (step + k) % 2 else "✧", sprites.GOLD))
        elif anim == "work":
            out.append((side, top + 1, "." * (1 + step % 3), "#9aa3b5"))
        elif anim == "surprised":
            out.append((side, top, "!", sprites.GOLD))
        elif anim in ("ouch", "worried"):
            out.append((side, top + 1, ";", "#7fb4ff"))
        elif anim == "hide":
            out.append((side, top + 1, "…", "#9aa3b5"))
        elif anim == "wave":
            out.append((side, top + 2, (")", "))")[step % 2], "#9aa3b5"))
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

    def _status(self, pet, focus, w: int) -> Text:
        out = Text(no_wrap=True, overflow="ellipsis")
        if self.game:
            left = max(0, int(self.game["until"] - time.monotonic()))
            out.append("≻ fish ", style=f"bold {ui.ACCENT}")
            out.append(f"0:{left:02d}", style=ui.FG)
            out.append(f" · you {self.game['you']} · {pet.name} {self.game['pet']}", style=ui.FG_MUTE)
        elif focus:
            kind, secs = focus
            m, s = divmod(max(0, int(secs)), 60)
            out.append(f"◷ {kind} ", style=f"bold {ui.ACCENT if kind == 'focus' else ui.OK}")
            out.append(f"{m}:{s:02d}", style=ui.FG)
            out.append(" · keeping your keys warm" if kind == "focus" else " · stretch & sip water ♥",
                       style=ui.FG_DIM)
        else:
            lines_today = pet.today_stat("lines")
            turns_today = pet.today_stat("turns")
            out.append("today ", style=ui.FG_DIM)
            out.append(f"+{lines_today:,}", style=ui.OK if lines_today else ui.FG_DIM)
            out.append(" lines", style=ui.FG_DIM)
            if turns_today:
                out.append(f" · {turns_today} turn{'s' if turns_today != 1 else ''}", style=ui.FG_DIM)
            if pet.streak > 1:
                out.append(f" · {pet.streak}d streak", style=sprites.GOLD)
        out.truncate(w, overflow="ellipsis")
        return out

    def _button_row(self, row: int, labels: tuple[str, ...], w: int) -> Text:
        out = Text(no_wrap=True)
        spans: list[tuple[int, int, str]] = []
        for i, label in enumerate(labels):
            if i:
                out.append(" · ", style=ui.FG_DIM)
            start = out.cell_len
            hot = self._hover_button == (row, i)
            out.append(label, style=f"bold underline {ui.ACCENT}" if hot else ui.FG_MUTE)
            spans.append((start, out.cell_len, (BUTTON_LABELS, BUTTON_LABELS2)[row][i]))
        self._buttons[row] = spans
        out.truncate(w)
        return out

    # ── mouse ────────────────────────────────────────────────────────
    def _button_at(self, x: int, y: int) -> tuple[int, int] | None:
        row = {BUTTONS: 0, BUTTONS2: 1}.get(y)
        if row is None:
            return None
        for i, (start, end, _label) in enumerate(self._buttons.get(row, ())):
            if start <= x < end:
                return (row, i)
        return None

    def _on_cat(self, x: int, y: int) -> bool:
        a, b = self.cat_span()
        return STAGE0 + 3 <= y < FLOOR and a + 1 <= x < b - 2

    def on_mouse_move(self, event: MouseMove) -> None:
        now = time.monotonic()
        hover_btn = self._button_at(event.x, event.y)
        hover_cat = self._on_cat(event.x, event.y) and self.game is None
        if STAGE0 <= event.y < FLOOR and not hover_cat and self.game is None:
            self.laser = (event.x, (event.y - STAGE0) * 2 + 1)
            self._laser_at = now
            self._active_at = now
        else:
            self.laser = None
        if (hover_btn, hover_cat) != (self._hover_button, self._hover_cat):
            self._hover_button, self._hover_cat = hover_btn, hover_cat
            self._key = None
        self._tick()

    def on_leave(self) -> None:
        self._hover_button, self._hover_cat, self.laser = None, False, None
        self._key = None

    def on_click(self, event: Click) -> None:
        event.stop()
        app = self.app
        x, y = event.x, event.y
        hit = self._button_at(x, y)
        if hit is not None:
            row, i = hit
            label = (BUTTON_LABELS, BUTTON_LABELS2)[row][i]
            action = getattr(app, "_pet_action", None)
            if callable(action):
                action(label)
            return
        if y == HEADER:
            opener = getattr(app, "_open_pet_card", None)
            if callable(opener):
                opener()
            return
        if self.game is not None and STAGE0 <= y < FLOOR:
            self._catch_at(x, y)
            return
        if self._on_cat(x, y):
            pat = getattr(app, "_pet_pat", None)
            if callable(pat):
                pat()
            return
        if STAGE0 <= y <= FLOOR:
            self.laser = None
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
