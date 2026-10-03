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


def set_pin_text(text: str) -> tuple[int, int]:
    """Replace all pinned context (the editor's save). Returns ``(lines, chars)``."""
    state.pinned_context = (text or "").strip()
    _persist()
    return pin_stats()


def pin_items(text: str | None = None) -> list[dict]:
    """One row per non-blank line: ``{"line": n, "text": s}`` (``n`` is 1-based)."""
    return [{"line": n, "text": s} for n, s in preview_lines(text) if s.strip()]


def _edit_line(line_no: int, expect: str | None, new: str | None) -> bool:
    """Replace (``new``) or drop (``None``) line ``line_no`` if it still reads ``expect``."""
    lines = pin_text().splitlines()
    i = int(line_no) - 1
    if not 0 <= i < len(lines):
        return False
    if expect is not None and lines[i].strip() != expect.strip():
        return False  # changed elsewhere meanwhile (terminal, another tab)
    if new is None:
        del lines[i]
    else:
        lines[i:i + 1] = [ln for ln in new.strip().splitlines() if ln.strip()] or []
    state.pinned_context = "\n".join(lines).strip()
    _persist()
    return True


def update_line(line_no: int, text: str, *, expect: str | None = None) -> bool:
    """Rewrite one pinned line (empty text removes it). False = it moved or changed."""
    return _edit_line(line_no, expect, (text or "").strip() or None)


def remove_line(line_no: int, *, expect: str | None = None) -> bool:
    return _edit_line(line_no, expect, None)


def public() -> dict:
    """What the web Pin dialog shows."""
    text = pin_text()
    _, chars = pin_stats(text)
    items = pin_items(text)
    return {
        "text": text,
        "items": items,
        "enabled": is_enabled(),
        "lines": len(items),  # non-blank lines: one pin each
        "chars": chars,
        "file": str(PIN_FILE),
    }
