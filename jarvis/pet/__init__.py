"""Jarvis the pet — a tiny kitty that lives in the TUI and grows with your work.

``model`` holds stats/levels/persistence, ``sprites`` the looks. The UI
registers :func:`set_reaction_hook` so commands (``/pet feed`` …, which run
on a worker thread) can make the on-screen kitty react.
"""
from __future__ import annotations

from typing import Callable

from .model import (  # noqa: F401
    DEFAULT_NAME,
    LEVEL_TITLES,
    SNACKS,
    Pet,
    Reaction,
    get_pet,
    greeting,
    level_for_xp,
    level_title,
    load_pet,
    reset_cache,
    save_pet,
    xp_for_level,
)

_hook: Callable[[Reaction], None] | None = None


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
