"""Jarvis the pet — stats, levels, care actions and persistence (no UI).

Everything is computed from wall-clock timestamps, so the pet keeps living
between sessions: :meth:`Pet.tick` applies the elapsed decay/recovery before
any read. Stats are gentle by design — nothing ever dies or scolds you;
neglect only makes Jarvis a little hungry, sleepy or lonely.

Every action returns a :class:`Reaction` (an animation name, how long it
plays, and an optional speech line) that the UI turns into motion.
"""
from __future__ import annotations

import json
import os
import pathlib
import random
import tempfile
import time
from dataclasses import asdict, dataclass, field
from typing import Any

from ..constants import CONFIG_DIR

PET_FILE = CONFIG_DIR / "pet.json"

DEFAULT_NAME = "Jarvis"
MAX_NAME = 16

# (title for each level; the last one repeats forever)
LEVEL_TITLES = (
    "Kitten",
    "Curious Kitten",
    "Keyboard Cat",
    "Bug Hunter",
    "Byte Buddy",
    "Terminal Tabby",
    "Kernel Kitty",
    "Senior Meowgineer",
    "Staff Purrgrammer",
    "Principal Pawthor",
    "Legendary Jarvis",
)

# Per-hour rates (awake / not napping unless noted).
FULLNESS_DECAY_H = 6.0       # full → empty in ~16h
HAPPINESS_DECAY_H = 3.0
ENERGY_RECOVER_H = 18.0      # resting while you're around
ENERGY_SLEEP_H = 60.0        # napping (and while the app is closed)
ENERGY_WORK_H = 14.0         # while the agent is busy
HAPPINESS_FLOOR = 20.0       # decay alone never goes below this

# snack → (fullness gained, happiness gained)
SNACKS = {
    "fish": (40, 6),
    "milk": (22, 8),
    "cookie": (28, 5),
}
SNACK_ORDER = ("fish", "milk", "cookie")

XP_TURN = 10
XP_TOOL = 1
XP_EDIT = 2
XP_PAT = 1
XP_FEED = 2
XP_PLAY = 3


def xp_for_level(level: int) -> int:
    """Total XP needed to reach ``level`` (1 → 0, 2 → 50, 3 → 150, …)."""
    level = max(1, int(level))
    return 25 * level * (level - 1)


def level_for_xp(xp: int) -> int:
    level = 1
    while xp >= xp_for_level(level + 1):
        level += 1
    return level


def level_title(level: int) -> str:
    return LEVEL_TITLES[min(max(level, 1), len(LEVEL_TITLES)) - 1]


@dataclass
class Reaction:
    """What the UI should show after an action or event."""

    anim: str = "idle"          # happy, love, eat, play, sleep, ouch, surprised, proud, trick, wave …
    secs: float = 1.6
    say: str = ""
    level_up: int = 0           # new level when this reaction levelled Jarvis up
    snack: str = ""             # what's being eaten (anim "eat")


@dataclass
class Pet:
    name: str = DEFAULT_NAME
    fur: str = "ginger"
    born: float = field(default_factory=time.time)
    xp: int = 0
    happiness: float = 75.0
    fullness: float = 70.0
    energy: float = 80.0
    napping_until: float = 0.0
    last_tick: float = field(default_factory=time.time)
    last_pat: float = 0.0
    last_fed: float = 0.0
    snack_i: int = 0
    counters: dict[str, int] = field(default_factory=dict)

    # ── derived ──────────────────────────────────────────────────────
    @property
    def level(self) -> int:
        return level_for_xp(self.xp)

    @property
    def title(self) -> str:
        return level_title(self.level)

    def level_progress(self) -> tuple[int, int]:
        """(xp into this level, xp this level spans)."""
        lo, hi = xp_for_level(self.level), xp_for_level(self.level + 1)
        return self.xp - lo, hi - lo

    def napping(self, now: float | None = None) -> bool:
        return (now or time.time()) < self.napping_until

    def age_days(self, now: float | None = None) -> float:
        return max(0.0, ((now or time.time()) - self.born) / 86400)

    def mood(self, now: float | None = None) -> str:
        """napping · hungry · sleepy · lonely · ecstatic · happy · content"""
        if self.napping(now):
            return "napping"
        if self.fullness < 25:
            return "hungry"
        if self.energy < 20:
            return "sleepy"
        if self.happiness < 30:
            return "lonely"
        if self.happiness >= 85:
            return "ecstatic"
        if self.happiness >= 60:
            return "happy"
        return "content"

    def mood_line(self, now: float | None = None) -> str:
        return {
            "napping": "curled up, dreaming of fish",
            "hungry": "tummy rumbling — a snack would help",
            "sleepy": "eyelids heavy — a nap would help",
            "lonely": "misses you — a pat would help",
            "ecstatic": "over the moon, purring loudly",
            "happy": "happy and purring",
            "content": "content, watching you type",
        }[self.mood(now)]

    def count(self, key: str) -> int:
        return int(self.counters.get(key, 0))

    def _bump(self, key: str, n: int = 1) -> None:
        self.counters[key] = self.count(key) + n

    # ── time ─────────────────────────────────────────────────────────
    def tick(self, now: float | None = None, *, busy: bool = False) -> None:
        """Apply decay/recovery for the time since the last tick."""
        now = now or time.time()
        dt_h = max(0.0, now - self.last_tick) / 3600
        self.last_tick = now
        if dt_h <= 0:
            return
        away = dt_h > 0.5  # the app was closed / idle a long time: Jarvis slept
        hungry_mult = 2.0 if self.fullness < 20 else 1.0
        self.fullness = _clamp(self.fullness - FULLNESS_DECAY_H * dt_h)
        new_h = self.happiness - HAPPINESS_DECAY_H * hungry_mult * dt_h
        self.happiness = max(min(self.happiness, HAPPINESS_FLOOR), _clamp(new_h))
        if busy:
            self.energy = _clamp(self.energy - ENERGY_WORK_H * dt_h)
        elif away or self.napping(now):
            self.energy = _clamp(self.energy + ENERGY_SLEEP_H * dt_h)
        else:
            self.energy = _clamp(self.energy + ENERGY_RECOVER_H * dt_h)

    # ── care ─────────────────────────────────────────────────────────
    def pat(self, now: float | None = None) -> Reaction:
        now = now or time.time()
        if self.napping(now):
            self.napping_until = 0.0
            self._bump("pats")
            return Reaction("surprised", 1.2, random.choice(_WOKE_UP))
        spam = now - self.last_pat < 8
        self.happiness = _clamp(self.happiness + (2 if spam else 7))
        self._bump("pats")
        first_pat_in_a_while = now - self.last_pat > 60
        self.last_pat = now
        r = Reaction("love", 1.8, random.choice(_PURRS) if not spam else "")
        if first_pat_in_a_while:
            r.level_up = self._gain(XP_PAT)
        return r

    def feed(self, snack: str | None = None, now: float | None = None) -> Reaction:
        now = now or time.time()
        if snack not in SNACKS:
            snack = SNACK_ORDER[self.snack_i % len(SNACK_ORDER)]
            self.snack_i += 1
        fill, joy = SNACKS[snack]
        label = snack
        self.napping_until = 0.0
        if self.fullness >= 95:
            return Reaction("happy", 1.4, f"so full… maybe later? ({label} saved)")
        self.fullness = _clamp(self.fullness + fill)
        self.happiness = _clamp(self.happiness + joy)
        self.last_fed = now
        self._bump("snacks")
        r = Reaction("eat", 2.4, random.choice(_YUMS).format(snack=label))
        r.snack = snack
        r.level_up = self._gain(XP_FEED)
        return r

    def play(self, now: float | None = None) -> Reaction:
        now = now or time.time()
        self.napping_until = 0.0
        if self.energy < 15:
            return Reaction("sleep", 2.0, "too sleepy to play… nap first? (n)")
        self.happiness = _clamp(self.happiness + 14)
        self.energy = _clamp(self.energy - 9)
        self.fullness = _clamp(self.fullness - 4)
        self._bump("plays")
        r = Reaction("play", 3.2, random.choice(_PLAYS))
        r.level_up = self._gain(XP_PLAY)
        return r

    def nap(self, minutes: float = 3.0, now: float | None = None) -> Reaction:
        now = now or time.time()
        self.napping_until = now + minutes * 60
        self._bump("naps")
        return Reaction("sleep", 2.0, "zzz… wake me with a pat")

    def wake(self) -> None:
        self.napping_until = 0.0

    def trick(self) -> Reaction:
        self._bump("tricks")
        anim, line = random.choice(_TRICKS)
        return Reaction(anim, 2.4, line)

    def rename(self, name: str) -> str:
        clean = " ".join((name or "").split())[:MAX_NAME].strip()
        self.name = clean or DEFAULT_NAME
        return self.name

    # ── the agent's work feeds Jarvis's XP ───────────────────────────
    def on_turn_done(self, seconds: float, *, interrupted: bool = False) -> Reaction:
        if interrupted:
            return Reaction("surprised", 1.4, "")
        self._bump("turns")
        self.happiness = _clamp(self.happiness + 1.5)
        r = Reaction("proud", 2.2, random.choice(_DONE_LONG) if seconds >= 45 else "")
        r.level_up = self._gain(XP_TURN)
        return r

    def on_tool_done(self, name: str, *, error: bool) -> Reaction | None:
        if error:
            self._bump("oops")
            return Reaction("ouch", 1.3, "")
        self._bump("tools")
        gain = XP_TOOL
        if name in ("edit_file", "write_file", "multi_edit"):
            self._bump("edits")
            gain += XP_EDIT
        lvl = self._gain(gain)
        return Reaction("proud", 1.0, "", level_up=lvl) if lvl else None

    def _gain(self, n: int) -> int:
        """Add XP; return the new level if this crossed a threshold, else 0."""
        before = self.level
        self.xp += max(0, int(n))
        after = self.level
        if after > before:
            self.happiness = _clamp(self.happiness + 10)
            return after
        return 0

    # ── persistence ──────────────────────────────────────────────────
    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Pet":
        pet = cls()
        if not isinstance(data, dict):
            return pet
        for key, default in asdict(pet).items():
            if key not in data:
                continue
            val = data[key]
            if isinstance(default, bool) or not isinstance(val, type(default)):
                if isinstance(default, float) and isinstance(val, int):
                    val = float(val)
                elif isinstance(default, int) and isinstance(val, float):
                    val = int(val)
                else:
                    continue
            setattr(pet, key, val)
        pet.name = pet.name[:MAX_NAME] or DEFAULT_NAME
        for stat in ("happiness", "fullness", "energy"):
            setattr(pet, stat, _clamp(getattr(pet, stat)))
        pet.counters = {str(k): int(v) for k, v in pet.counters.items()
                        if isinstance(v, (int, float))}
        return pet


def _clamp(v: float) -> float:
    return max(0.0, min(100.0, float(v)))


# ── speech ────────────────────────────────────────────────────────────────

_PURRS = ("purr~", "prrrr ♥", "more pats please", "mrrp!", "*headbutts your hand*", "♥ purr purr ♥")
_WOKE_UP = ("mrow?! oh — hi!", "*yawns* …I was dreaming of fish", "awake! awake!")
_YUMS = ("yum, {snack}!", "nom nom… {snack}!", "*crunch* thank you ♥", "best {snack} ever")
_PLAYS = ("*pounces on the yarn*", "zoomies!!", "catch me if you can~", "*bats at the cursor*")
_TRICKS = (
    ("trick", "*does a little spin*"),
    ("wave", "*waves a paw* hi!"),
    ("proud", "*sits very politely*"),
    ("play", "*chases its own tail*"),
)
_DONE_LONG = ("all done ✦", "phew, that was a big one", "done! good teamwork ✦", "shipped it ✦")

GREETINGS = ("hi! I'm {name} ♥", "meow — ready when you are", "*stretches* let's code", "hello again ♥")


def greeting(pet: Pet) -> str:
    return random.choice(GREETINGS).format(name=pet.name)


# ── load / save ───────────────────────────────────────────────────────────

_PET: Pet | None = None


def get_pet() -> Pet:
    """The process-wide pet, loaded (and caught up on elapsed time) once."""
    global _PET
    if _PET is None:
        _PET = load_pet()
        _PET.tick()
    return _PET


def reset_cache() -> None:
    global _PET
    _PET = None


def load_pet(path: pathlib.Path | None = None) -> Pet:
    path = path or PET_FILE
    try:
        return Pet.from_dict(json.loads(path.read_text(encoding="utf-8")))
    except (OSError, ValueError):
        return Pet()


def save_pet(pet: Pet | None = None, path: pathlib.Path | None = None) -> None:
    pet = pet or get_pet()
    path = path or PET_FILE
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=path.parent,
                                         delete=False, suffix=".tmp") as tmp:
            json.dump(pet.to_dict(), tmp, indent=2, ensure_ascii=False)
            tmp_path = tmp.name
        os.replace(tmp_path, path)
    except OSError:
        pass
