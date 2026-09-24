"""Collapse big pastes in the composer into ``[Pasted text #1 +120 lines]``.

The chip keeps the prompt box readable; ``expand_chips`` swaps the full
text back in when the message is sent.
"""
from __future__ import annotations

import re

_MIN_LINES = 8
_MIN_CHARS = 800

_CHIP_RE = re.compile(r"\[Pasted text #(\d+) \+\d+ lines?\]")
_store: dict[int, str] = {}
_counter = 0


def should_collapse(text: str) -> bool:
    text = text or ""
    return text.count("\n") + 1 >= _MIN_LINES or len(text) >= _MIN_CHARS


def make_chip(text: str) -> str:
    """Store ``text`` and return its chip token."""
    global _counter
    _counter += 1
    _store[_counter] = text
    lines = text.count("\n") + 1
    return f"[Pasted text #{_counter} +{lines} line{'s' if lines != 1 else ''}]"


def expand_chips(text: str) -> str:
    """Replace chip tokens with the pasted text they stand for."""
    if not text or "[Pasted text #" not in text:
        return text
    return _CHIP_RE.sub(lambda m: _store.get(int(m.group(1)), m.group(0)), text)


def reset() -> None:
    """Forget stored pastes (after a message is sent)."""
    _store.clear()
