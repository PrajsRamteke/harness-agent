"""Little tips Jarvis the pet shares when asked (``?`` on the /pet card)."""
from __future__ import annotations

import random

_PET_TIPS = (
    "psst: click me to pet me, double-click for this card",
    "I earn XP whenever we finish a turn together ✦",
    "/pet feed · /pet play · /pet nap work from the prompt too",
    "⌃Y copies the last reply as clean text",
    "⇧⇥ plan mode: research first, change things after you approve",
    "@ attaches a file — type to search",
    "long session? I'll remind you to stretch ♥",
)


def random_tip() -> str:
    tips = list(_PET_TIPS)
    try:
        from .app import _TIPS

        tips += [t.removeprefix("Tip: ") for t in _TIPS]
    except Exception:
        pass
    return random.choice(tips)
