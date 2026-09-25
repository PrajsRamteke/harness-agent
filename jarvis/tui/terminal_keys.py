"""Key sequences terminals send that Textual doesn't map on its own.

VS Code, Cursor and Windsurf terminals can't report Shift+Enter, so the usual
fix — the keybinding Claude Code's ``/terminal-setup`` installs — makes
Shift+Enter send ESC + CR, i.e. Meta/Alt+Enter. macOS Terminal and iTerm send
the same for Option+Enter with "Use Option as Meta key" on.

Textual has no entry for that sequence: it waits out the escape delay, then
re-issues the CR as a *plain* Enter (the alt modifier is dropped for keys in
its sequence table), so the prompt was submitted instead of getting a new
line. Registering the sequences turns them into ``alt+enter``, which
``PromptArea`` treats as a newline.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class _Key:
    """Stands in for a ``textual.keys.Keys`` member (the parser reads ``.value``)."""

    value: str


EXTRA_SEQUENCES: dict[str, tuple[_Key, ...]] = {
    "\x1b\r": (_Key("alt+enter"),),  # Shift+Enter keybinding in VS Code/Cursor · Option+Enter
    "\x1b\n": (_Key("alt+enter"),),  # same, from bindings that send ESC + LF
}


def install() -> None:
    """Teach Textual's input parser the extra sequences (idempotent)."""
    try:
        from textual import _ansi_sequences
    except Exception:  # pragma: no cover - Textual internals moved
        return
    table = _ansi_sequences.ANSI_SEQUENCES_KEYS
    for seq, keys in EXTRA_SEQUENCES.items():
        if seq not in table:
            table[seq] = keys  # type: ignore[index]
