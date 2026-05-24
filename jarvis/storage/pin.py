"""Pinned context helpers — shared by slash commands and the TUI modal."""
from __future__ import annotations

from .. import state
from ..constants import PIN_FILE
from .prefs import save_pin


def pin_text() -> str:
    """Return trimmed pinned context."""
    return state.pinned_context.strip()


def is_enabled() -> bool:
    """Return whether pinned context is injected into the system prompt."""
    return bool(state.pin_enabled)


def set_enabled(enabled: bool) -> bool:
    """Enable or disable pin injection (text is preserved)."""
    state.pin_enabled = bool(enabled)
    _persist_enabled()
    return state.pin_enabled


def toggle_enabled() -> bool:
    """Flip pin injection on/off. Returns the new enabled state."""
    return set_enabled(not is_enabled())


def injection_text() -> str:
    """Pinned text for the system prompt — empty when disabled."""
    return pin_text() if is_enabled() else ""


def injection_cache_key() -> str:
    """Cache key fragment for system prompt rebuilds."""
    text = pin_text()
    if not text:
        return ""
    return f"{'1' if is_enabled() else '0'}|{text}"


def pin_stats(text: str | None = None) -> tuple[int, int]:
    """Return ``(line_count, char_count)`` for pinned text."""
    body = (text if text is not None else pin_text()).strip()
    if not body:
        return 0, 0
    return len(body.splitlines()), len(body)


def append_pin(text: str) -> tuple[int, int]:
    """Append ``text`` to pinned context. Returns updated ``(lines, chars)``."""
    chunk = (text or "").strip()
    if not chunk:
        return pin_stats()
    if state.pinned_context.strip():
        state.pinned_context = (state.pinned_context.rstrip() + "\n" + chunk).strip()
    else:
        state.pinned_context = chunk
    _persist()
    return pin_stats()


def clear_pin() -> None:
    """Remove all pinned context."""
    state.pinned_context = ""
    _persist()


def _persist() -> None:
    save_pin()
    _invalidate_system_cache()


def _persist_enabled() -> None:
    from ..state import save_pin_config

    save_pin_config()
    _invalidate_system_cache()


def _invalidate_system_cache() -> None:
    from ..repl.system import invalidate_system_cache

    invalidate_system_cache()


def preview_lines(text: str | None = None, *, max_lines: int | None = None) -> list[tuple[int, str]]:
    """Return numbered preview rows ``(line_no, content)``."""
    body = (text if text is not None else pin_text()).strip()
    if not body:
        return []
    rows = [(i, line) for i, line in enumerate(body.splitlines(), 1)]
    if max_lines is not None and len(rows) > max_lines:
        return rows[:max_lines]
    return rows
