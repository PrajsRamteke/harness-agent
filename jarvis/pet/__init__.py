"""Jarvis the pet — little companions that live in the TUI and grow with your work.

``model`` holds stats/levels/badges/persistence (a roster of pets),
``sprites`` the looks, ``events`` spots tests/git moments in tool output,
``session`` tracks this session for the ``/new`` recap. The UI
registers :func:`set_reaction_hook` so commands (``/pet feed`` …, which run
on a worker thread) can make the on-screen kitty react.
"""
from __future__ import annotations

from typing import Callable

from .model import (  # noqa: F401
    ACCESSORIES,
    ACCESSORY_LABELS,
    BADGE_INFO,
    BADGES,
    DEFAULT_NAME,
    LEVEL_TITLES,
    SNACKS,
    SPECIES,
    SPECIES_LABELS,
    Pet,
    Reaction,
    Roster,
    get_pet,
    get_roster,
    greeting,
    level_for_xp,
    level_title,
    load_pet,
    load_roster,
    reset_cache,
    save_pet,
    save_roster,
    xp_for_level,
)

_hook: Callable[[Reaction], None] | None = None
_action_hook: Callable[[str], None] | None = None


def set_reaction_hook(fn: Callable[[Reaction], None] | None) -> None:
    global _hook
    _hook = fn


def notify(reaction: Reaction | None) -> bool:
    """Forward a reaction to the UI (if one is listening). Returns True if shown."""
    if reaction is None or _hook is None:
        return False
    try:
        _hook(reaction)
        return True
    except Exception:
        return False


def set_action_hook(fn: Callable[[str], None] | None) -> None:
    global _action_hook
    _action_hook = fn


def request(action: str) -> bool:
    """Ask the UI to run a pen action (``fish`` · ``focus`` · ``card`` …)."""
    if _action_hook is None:
        return False
    try:
        _action_hook(action)
        return True
    except Exception:
        return False
