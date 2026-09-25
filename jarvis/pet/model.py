"""Jarvis the pet — stats, levels, care actions and persistence (no UI).

Everything is computed from wall-clock timestamps, so the pet keeps living
between sessions: :meth:`Pet.tick` applies the elapsed decay/recovery before
any read. Stats are gentle by design — nothing ever dies or scolds you;
neglect only makes a pet a little hungry, sleepy or lonely.

You can have several pets (a :class:`Roster`, one active): cats, dogs,
bunnies, and dragons that start as an egg and hatch after a few turns of
work together. Every action returns a :class:`Reaction` (an animation name,
how long it plays, and an optional speech line) that the UI turns into
motion; badges unlocked along the way ride on the reaction too.
"""
from __future__ import annotations

import json
import os
import pathlib
import random
import tempfile
import time
from dataclasses import asdict, dataclass, field
from datetime import date, datetime, timedelta
from typing import Any

from ..constants import CONFIG_DIR

PET_FILE = CONFIG_DIR / "pet.json"

DEFAULT_NAME = "Jarvis"
MAX_NAME = 16
MAX_PETS = 6

SPECIES = ("cat", "dog", "bunny", "dragon")
SPECIES_LABELS = {"cat": "kitty", "dog": "puppy", "bunny": "bunny", "dragon": "dragon egg"}
HATCH_TURNS = 10  # a dragon egg hatches after this many turns together

# Level titles per species (the last one repeats forever).
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
_TITLES = {
    "cat": LEVEL_TITLES,
    "dog": ("Puppy", "Good Pup", "Fetch Engineer", "Bug Sniffer", "Byte Retriever",
            "Terminal Terrier", "Kernel Corgi", "Senior Woofgineer", "Staff Barkitect",
            "Principal Pawgrammer", "Legendary Good Boy"),
    "bunny": ("Bunlet", "Curious Bun", "Hop Hacker", "Bug Nibbler", "Byte Bunny",
              "Terminal Thumper", "Kernel Hopper", "Senior Hopgineer", "Staff Carrotect",
              "Principal Bunnovator", "Legendary Bun"),
    "dragon": ("Hatchling", "Ember", "Spark Coder", "Bug Burner", "Byte Wyrm",
               "Terminal Drake", "Kernel Wyvern", "Senior Dragoneer", "Staff Flamecaster",
               "Principal Hoarder", "Elder Dragon"),
}

# Accessories unlock by level (worn automatically; take them off on the card).
ACCESSORIES = (("glasses", 3), ("party", 5), ("scarf", 7), ("crown", 10))
ACCESSORY_LABELS = {"glasses": "coding glasses", "party": "party hat",
                    "scarf": "cozy scarf", "crown": "crown"}

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
XP_TESTS = 5
XP_FIXED = 10
XP_SHIP = 5
XP_FOCUS = 15

BIG_DIFF = 100  # lines touched by one edit that earn a "whoa!"


def xp_for_level(level: int) -> int:
    """Total XP needed to reach ``level`` (1 → 0, 2 → 50, 3 → 150, …)."""
    level = max(1, int(level))
    return 25 * level * (level - 1)


def level_for_xp(xp: int) -> int:
    level = 1
    while xp >= xp_for_level(level + 1):
        level += 1
    return level


def level_title(level: int, species: str = "cat") -> str:
    titles = _TITLES.get(species, LEVEL_TITLES)
    return titles[min(max(level, 1), len(titles)) - 1]


def _day(ts: float | None = None) -> str:
    return datetime.fromtimestamp(ts or time.time()).strftime("%Y-%m-%d")


# ── badges ────────────────────────────────────────────────────────────────

# (id, icon, name, how to earn it, counter, threshold)
BADGES = (
    ("first_turn", "✦", "Hello World", "finish a first turn together", "turns", 1),
    ("turns_100", "★", "Centurion", "100 turns together", "turns", 100),
    ("turns_1000", "✪", "Thousand Turns", "1,000 turns together", "turns", 1000),
    ("night_owl", "☾", "Night Owl", "finish a turn between midnight and 5am", "night_turns", 1),
    ("early_bird", "☼", "Early Bird", "finish a turn between 5 and 7am", "early_turns", 1),
    ("test_hero", "✓", "Green Machine", "25 passing test runs", "tests_pass", 25),
    ("bug_squasher", "✗", "Bug Squasher", "turn failing tests green 5 times", "tests_fixed", 5),
    ("shipper", "⇡", "Shipper", "10 commits", "commits", 10),
    ("big_diff", "≋", "Whoa!", f"one edit touching {BIG_DIFF}+ lines", "big_diffs", 1),
    ("lines_10k", "▤", "Ten Thousand Lines", "10,000 lines shipped", "lines", 10_000),
    ("best_friends", "♥", "Best Friends", "100 pats", "pats", 100),
    ("foodie", "◆", "Foodie", "50 snacks", "snacks", 50),
    ("fisher", "≻", "Master Fisher", "catch 100 fish", "fish", 100),
    ("focused", "◷", "Deep Focus", "complete 10 focus sessions", "focus", 10),
    ("streak_7", "✹", "On Fire", "a 7-day coding streak", "streak", 7),
    ("streak_30", "✺", "Unstoppable", "a 30-day coding streak", "streak", 30),
    ("hatched", "◉", "It Hatched!", "hatch a dragon egg", "hatched", 1),
)
BADGE_INFO = {b[0]: b for b in BADGES}


def badge_progress(pet, badge) -> tuple[int, int]:
    """(current, target) progress toward ``badge``'s counter threshold.

    ``badge`` is a row from :data:`BADGES` — ``(id, icon, name, how, counter, threshold)``.
    """
    _bid, _icon, _name, _how, counter, threshold = badge
    return min(pet.count(counter), threshold), threshold


@dataclass
class Reaction:
    """What the UI should show after an action or event."""

    anim: str = "idle"          # happy, love, eat, play, sleep, ouch, surprised, proud, trick,
    secs: float = 1.6           # wave, cheer, party, hide, worried, hatch …
    say: str = ""
    level_up: int = 0           # new level when this reaction levelled the pet up
    snack: str = ""             # what's being eaten (anim "eat")
    badges: list[str] = field(default_factory=list)  # badge ids unlocked just now


@dataclass
class Pet:
    name: str = DEFAULT_NAME
    species: str = "cat"
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
    hatch_left: int = 0          # dragon eggs: turns until hatching
    streak: int = 0
    best_streak: int = 0
    last_day: str = ""           # last day a turn finished (YYYY-MM-DD)
    today: dict[str, Any] = field(default_factory=dict)   # {"day", "lines", "turns"}
    off: list[str] = field(default_factory=list)          # accessories taken off
    tests: str = ""              # last test result: pass | fail | ""
    fish_best: int = 0
    badges: dict[str, float] = field(default_factory=dict)
    counters: dict[str, int] = field(default_factory=dict)

    # ── derived ──────────────────────────────────────────────────────
    @property
    def level(self) -> int:
        return level_for_xp(self.xp)

    @property
    def title(self) -> str:
        if self.is_egg:
            return "Egg"
        return level_title(self.level, self.species)

    @property
    def is_egg(self) -> bool:
        return self.species == "dragon" and self.hatch_left > 0

    @property
    def stage(self) -> int:
        """Growth: 0 little (Lv 1–4) · 1 grown (Lv 5–9) · 2 grand (Lv 10+)."""
        lvl = self.level
        return 2 if lvl >= 10 else (1 if lvl >= 5 else 0)

    def level_progress(self) -> tuple[int, int]:
        """(xp into this level, xp this level spans)."""
        lo, hi = xp_for_level(self.level), xp_for_level(self.level + 1)
        return self.xp - lo, hi - lo

    def unlocked(self) -> list[str]:
        return [name for name, lvl in ACCESSORIES if self.level >= lvl]

    def outfit(self, *, working: bool = False) -> set[str]:
        """Accessories worn right now (glasses only while the agent works)."""
        wear = {a for a in self.unlocked() if a not in self.off}
        if "crown" in wear:
            wear.discard("party")
        if not working:
            wear.discard("glasses")
        if self.is_egg:
            return set()
        return wear

    def toggle_accessory(self, name: str) -> bool:
        """Take off / put on an unlocked accessory; returns True when worn."""
        if name in self.off:
            self.off.remove(name)
            return True
        self.off.append(name)
        return False

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
        if self.is_egg:
            left = self.hatch_left
            return f"wobbling… hatches in {left} turn{'s' if left != 1 else ''}"
        happy = {"cat": "happy and purring", "dog": "tail going a mile a minute",
                 "bunny": "happy little binkies", "dragon": "happy little smoke puffs"}
        return {
            "napping": "curled up, dreaming of snacks",
            "hungry": "tummy rumbling — a snack would help",
            "sleepy": "eyelids heavy — a nap would help",
            "lonely": "misses you — a pat would help",
            "ecstatic": "over the moon!",
            "happy": happy.get(self.species, "happy"),
            "content": "content, watching you type",
        }[self.mood(now)]

    def count(self, key: str) -> int:
        return int(self.counters.get(key, 0))

    def _bump(self, key: str, n: int = 1) -> None:
        self.counters[key] = self.count(key) + n

    def today_stat(self, key: str, now: float | None = None) -> int:
        if self.today.get("day") != _day(now):
            return 0
        return int(self.today.get(key, 0))

    def _bump_today(self, key: str, n: int, now: float | None = None) -> None:
        day = _day(now)
        if self.today.get("day") != day:
            self.today = {"day": day}
        self.today[key] = int(self.today.get(key, 0)) + n

    # ── time ─────────────────────────────────────────────────────────
    def tick(self, now: float | None = None, *, busy: bool = False) -> None:
        """Apply decay/recovery for the time since the last tick."""
        now = now or time.time()
        dt_h = max(0.0, now - self.last_tick) / 3600
        self.last_tick = now
        if dt_h <= 0:
            return
        away = dt_h > 0.5  # the app was closed / idle a long time: the pet slept
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
        if self.is_egg:
            self._bump("pats")
            return self._award(Reaction("wobble", 1.2, random.choice(_EGG_PATS)))
        if self.napping(now):
            self.napping_until = 0.0
            self._bump("pats")
            return self._award(Reaction("surprised", 1.2, random.choice(_WOKE_UP)))
        spam = now - self.last_pat < 8
        self.happiness = _clamp(self.happiness + (2 if spam else 7))
        self._bump("pats")
        first_pat_in_a_while = now - self.last_pat > 60
        self.last_pat = now
        r = Reaction("love", 1.8, random.choice(_PURRS[self.species]) if not spam else "")
        if first_pat_in_a_while:
            r.level_up = self._gain(XP_PAT)
        return self._award(r)

    def feed(self, snack: str | None = None, now: float | None = None) -> Reaction:
        now = now or time.time()
        if self.is_egg:
            return Reaction("wobble", 1.2, "eggs don't eat… keep coding to hatch me!")
        if snack not in SNACKS:
            snack = SNACK_ORDER[self.snack_i % len(SNACK_ORDER)]
            self.snack_i += 1
        fill, joy = SNACKS[snack]
        self.napping_until = 0.0
        if self.fullness >= 95:
            return Reaction("happy", 1.4, f"so full… maybe later? ({snack} saved)")
        self.fullness = _clamp(self.fullness + fill)
        self.happiness = _clamp(self.happiness + joy)
        self.last_fed = now
        self._bump("snacks")
        r = Reaction("eat", 2.4, random.choice(_YUMS).format(snack=snack), snack=snack)
        r.level_up = self._gain(XP_FEED)
        return self._award(r)

    def play(self, now: float | None = None) -> Reaction:
        if self.is_egg:
            return Reaction("wobble", 1.4, "*rolls around a little*")
        self.napping_until = 0.0
        if self.energy < 15:
            return Reaction("sleep", 2.0, "too sleepy to play… nap first?")
        self.happiness = _clamp(self.happiness + 14)
        self.energy = _clamp(self.energy - 9)
        self.fullness = _clamp(self.fullness - 4)
        self._bump("plays")
        r = Reaction("play", 3.2, random.choice(_PLAYS))
        r.level_up = self._gain(XP_PLAY)
        return self._award(r)

    def nap(self, minutes: float = 3.0, now: float | None = None) -> Reaction:
        now = now or time.time()
        self.napping_until = now + minutes * 60
        self._bump("naps")
        return Reaction("sleep", 2.0, "zzz… wake me with a pat")

    def wake(self) -> None:
        self.napping_until = 0.0

    def trick(self) -> Reaction:
        if self.is_egg:
            return Reaction("wobble", 1.6, "*wobble wobble*")
        self._bump("tricks")
        anim, line = random.choice(_TRICKS)
        return Reaction(anim, 2.4, line)

    def rename(self, name: str) -> str:
        clean = " ".join((name or "").split())[:MAX_NAME].strip()
        self.name = clean or DEFAULT_NAME
        return self.name

    # ── the agent's work feeds XP, streaks and badges ────────────────
    def on_turn_done(self, seconds: float, *, interrupted: bool = False,
                     now: float | None = None) -> Reaction:
        if interrupted:
            return Reaction("surprised", 1.4, "")
        now = now or time.time()
        self._bump("turns")
        self._bump_today("turns", 1, now)
        hour = datetime.fromtimestamp(now).hour
        if hour < 5:
            self._bump("night_turns")
        elif hour < 7:
            self._bump("early_turns")
        self._update_streak(now)
        self.happiness = _clamp(self.happiness + 1.5)
        if self.is_egg:
            self.hatch_left -= 1
            if self.hatch_left <= 0:
                self.hatch_left = 0
                self._bump("hatched")
                r = Reaction("hatch", 3.6, f"*crack* …hi!! I'm {self.name} ♥")
                r.level_up = self._gain(XP_TURN)
                return self._award(r)
            return self._award(Reaction("wobble", 1.6, "*wobble* (something moved!)"
                                        if self.hatch_left <= 3 else ""))
        r = Reaction("proud", 2.2, random.choice(_DONE_LONG) if seconds >= 45 else "")
        r.level_up = self._gain(XP_TURN)
        return self._award(r)

    def _update_streak(self, now: float) -> None:
        today = _day(now)
        if self.last_day == today:
            return
        yesterday = (date.fromisoformat(today) - timedelta(days=1)).isoformat()
        self.streak = self.streak + 1 if self.last_day == yesterday else 1
        self.best_streak = max(self.best_streak, self.streak)
        self.last_day = today
        self.counters["streak"] = max(self.count("streak"), self.streak)

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
        r = self._award(Reaction("proud", 1.0, "", level_up=lvl))
        return r if (lvl or r.badges) else None

    def on_work_event(self, event: str, detail: str = "") -> Reaction | None:
        """React to something the agent's tools did (see :mod:`jarvis.pet.events`)."""
        if event == "tests_pass":
            fixed = self.tests == "fail"
            self.tests = "pass"
            self._bump("tests_pass")
            if fixed:
                self._bump("tests_fixed")
                r = Reaction("cheer", 3.0, random.choice(_FIXED))
                r.level_up = self._gain(XP_FIXED)
            else:
                r = Reaction("cheer", 2.4, random.choice(_GREEN))
                r.level_up = self._gain(XP_TESTS)
            return self._award(r)
        if event == "tests_fail":
            self.tests = "fail"
            self._bump("tests_fail")
            return Reaction("hide", 2.4, random.choice(_RED))
        if event in ("commit", "push"):
            self._bump("commits" if event == "commit" else "pushes")
            r = Reaction("party", 3.0, random.choice(_COMMIT if event == "commit" else _PUSH))
            r.level_up = self._gain(XP_SHIP)
            return self._award(r)
        if event == "conflict":
            self._bump("conflicts")
            return Reaction("worried", 2.8, "uh oh… merge conflict. we got this ♥")
        return None

    def on_diff(self, added: int, removed: int, now: float | None = None) -> Reaction | None:
        lines = max(0, int(added)) + max(0, int(removed))
        if not lines:
            return None
        self._bump("lines", lines)
        self._bump_today("lines", lines, now)
        if lines >= BIG_DIFF:
            self._bump("big_diffs")
            return self._award(Reaction("surprised", 2.2, f"whoa! +{added} −{removed} lines"))
        r = self._award(Reaction("proud", 1.0))
        return r if r.badges else None

    def on_fish_round(self, caught: int) -> Reaction:
        self._bump("fish", caught)
        best = caught > self.fish_best
        self.fish_best = max(self.fish_best, caught)
        self.fullness = _clamp(self.fullness + min(20, caught * 2))
        self.happiness = _clamp(self.happiness + 8)
        line = (f"{caught} fish! new record ✦" if best and caught
                else f"we caught {caught} fish ♥" if caught else "they got away… next time!")
        r = Reaction("cheer" if caught else "happy", 2.6, line)
        r.level_up = self._gain(caught)
        return self._award(r)

    def on_focus_done(self) -> Reaction:
        self._bump("focus")
        r = Reaction("wave", 3.0, "focus done! ✦ break time — stretch & drink water ♥")
        r.level_up = self._gain(XP_FOCUS)
        return self._award(r)

    def _gain(self, n: int) -> int:
        """Add XP; return the new level if this crossed a threshold, else 0."""
        before = self.level
        self.xp += max(0, int(n))
        after = self.level
        if after > before:
            self.happiness = _clamp(self.happiness + 10)
            return after
        return 0

    def _award(self, r: Reaction) -> Reaction:
        """Attach any badges that just became earned."""
        now = time.time()
        for bid, _icon, _name, _how, counter, need in BADGES:
            if bid not in self.badges and self.count(counter) >= need:
                self.badges[bid] = now
                r.badges.append(bid)
        return r

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
        if pet.species not in SPECIES:
            pet.species = "cat"
        for stat in ("happiness", "fullness", "energy"):
            setattr(pet, stat, _clamp(getattr(pet, stat)))
        pet.counters = {str(k): int(v) for k, v in pet.counters.items()
                        if isinstance(v, (int, float))}
        pet.badges = {str(k): float(v) for k, v in pet.badges.items()
                      if isinstance(v, (int, float))}
        pet.off = [str(a) for a in pet.off if isinstance(a, str)]
        pet.hatch_left = max(0, pet.hatch_left)
        return pet


def new_pet(species: str, name: str = "", fur: str = "") -> Pet:
    from .sprites import default_fur

    species = species if species in SPECIES else "cat"
    pet = Pet(species=species, fur=fur or default_fur(species))
    pet.rename(name or {"cat": "Jarvis", "dog": "Biscuit", "bunny": "Mochi",
                        "dragon": "Ember"}[species])
    if species == "dragon":
        pet.hatch_left = HATCH_TURNS
    return pet


def _clamp(v: float) -> float:
    return max(0.0, min(100.0, float(v)))


# ── the roster ────────────────────────────────────────────────────────────

@dataclass
class Roster:
    pets: list[Pet] = field(default_factory=lambda: [Pet()])
    active: int = 0

    @property
    def pet(self) -> Pet:
        if not self.pets:
            self.pets.append(Pet())
        self.active = max(0, min(self.active, len(self.pets) - 1))
        return self.pets[self.active]

    def adopt(self, species: str, name: str = "", fur: str = "") -> Pet:
        if len(self.pets) >= MAX_PETS:
            raise ValueError(f"your pen is full ({MAX_PETS} pets)")
        pet = new_pet(species, name, fur)
        self.pets.append(pet)
        self.active = len(self.pets) - 1
        return pet

    def switch(self, step: int = 1) -> Pet:
        self.active = (self.active + step) % max(1, len(self.pets))
        pet = self.pet
        pet.tick()
        return pet

    def to_dict(self) -> dict[str, Any]:
        return {"active": self.active, "pets": [p.to_dict() for p in self.pets]}

    @classmethod
    def from_dict(cls, data: Any) -> "Roster":
        if isinstance(data, dict) and isinstance(data.get("pets"), list):
            pets = [Pet.from_dict(p) for p in data["pets"] if isinstance(p, dict)][:MAX_PETS]
            active = data.get("active") if isinstance(data.get("active"), int) else 0
            return cls(pets or [Pet()], active)
        if isinstance(data, dict):  # a single pet (older pet.json)
            return cls([Pet.from_dict(data)], 0)
        return cls()


# ── speech ────────────────────────────────────────────────────────────────

_PURRS = {
    "cat": ("purr~", "prrrr ♥", "more pats please", "mrrp!", "*headbutts your hand*", "♥ purr purr ♥"),
    "dog": ("*tail wags furiously*", "woof! ♥", "best human ever", "*happy panting*", "more! more!"),
    "bunny": ("*happy nose wiggle*", "*binky!*", "♥ soft ♥", "*flops over contentedly*"),
    "dragon": ("*happy smoke puff*", "rawr ♥", "*purrs like a tiny volcano*", "*wings flutter*"),
}
_EGG_PATS = ("*wobble*", "*warm and cozy*", "*something tapped back!*")
_WOKE_UP = ("mrow?! oh — hi!", "*yawns* …I was dreaming of snacks", "awake! awake!")
_YUMS = ("yum, {snack}!", "nom nom… {snack}!", "*crunch* thank you ♥", "best {snack} ever")
_PLAYS = ("*pounces on the yarn*", "zoomies!!", "catch me if you can~", "*bats at the cursor*")
_TRICKS = (
    ("trick", "*does a little spin*"),
    ("wave", "*waves a paw* hi!"),
    ("proud", "*sits very politely*"),
    ("play", "*chases its own tail*"),
)
_DONE_LONG = ("all done ✦", "phew, that was a big one", "done! good teamwork ✦", "shipped it ✦")
_GREEN = ("tests green! ✦", "all passing ♥", "green is my favorite color ✦", "*happy dance* tests pass!")
_FIXED = ("you fixed it!! ✦✦", "red → green! best feeling ✦", "bug squashed! ✦")
_RED = ("eek… red tests (we'll fix it ♥)", "*peeks through paws* …failing?", "ouch, failing tests — you got this")
_COMMIT = ("committed! ship it ✦", "*party hop* commit!", "another one in the history books ✦")
_PUSH = ("pushed to the world ✦", "off it goes! ✦", "*waves at the remote* bye, commits!")

GREETINGS = ("hi! I'm {name} ♥", "ready when you are ♥", "*stretches* let's code", "hello again ♥")


def greeting(pet: Pet) -> str:
    if pet.is_egg:
        return f"*wobble* ({pet.hatch_left} turns until I hatch)"
    return random.choice(GREETINGS).format(name=pet.name)


# ── load / save ───────────────────────────────────────────────────────────

_ROSTER: Roster | None = None


def get_roster() -> Roster:
    """The process-wide roster, loaded (and caught up on elapsed time) once."""
    global _ROSTER
    if _ROSTER is None:
        _ROSTER = load_roster()
        for pet in _ROSTER.pets:
            pet.tick()
    return _ROSTER


def get_pet() -> Pet:
    """The active pet."""
    return get_roster().pet


def reset_cache() -> None:
    global _ROSTER
    _ROSTER = None


def load_roster(path: pathlib.Path | None = None) -> Roster:
    path = path or PET_FILE
    try:
        return Roster.from_dict(json.loads(path.read_text(encoding="utf-8")))
    except (OSError, ValueError):
        return Roster()


def load_pet(path: pathlib.Path | None = None) -> Pet:
    return load_roster(path).pet


def save_roster(roster: Roster | None = None, path: pathlib.Path | None = None) -> None:
    roster = roster or get_roster()
    path = path or PET_FILE
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=path.parent,
                                         delete=False, suffix=".tmp") as tmp:
            json.dump(roster.to_dict(), tmp, indent=2, ensure_ascii=False)
            tmp_path = tmp.name
        os.replace(tmp_path, path)
    except OSError:
        pass


def save_pet(pet: Pet | None = None, path: pathlib.Path | None = None) -> None:
    """Save the roster (or, for a pet outside it, a roster of just that pet)."""
    roster = get_roster() if _ROSTER is not None or pet is None else None
    if pet is not None and (roster is None or not any(p is pet for p in roster.pets)):
        roster = Roster([pet], 0)
    save_roster(roster, path)
