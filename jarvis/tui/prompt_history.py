"""Composer prompt history (↑ / ↓ at the first / last line), persisted.

Stored as a JSON list of strings in ``HIST_FILE`` (newest last, capped).
Navigation keeps the unsent draft so ↓ past the newest entry restores it.
"""
from __future__ import annotations

import json

_MAX = 500


class PromptHistory:
    def __init__(self, path=None) -> None:
        if path is None:
            try:
                from ..constants import HIST_FILE

                path = HIST_FILE
            except Exception:
                path = None
        self._path = path
        self.entries: list[str] = self._load()
        self._idx: int | None = None  # None = editing the draft
        self._draft = ""

    def _load(self) -> list[str]:
        if self._path is None:
            return []
        try:
            data = json.loads(self._path.read_text())
        except Exception:
            return []
        if not isinstance(data, list):
            return []
        return [s for s in data if isinstance(s, str) and s.strip()][-_MAX:]

    def _save(self) -> None:
        if self._path is None:
            return
        try:
            from ..utils.io import _secure_write

            _secure_write(self._path, json.dumps(self.entries[-_MAX:], ensure_ascii=False))
        except Exception:
            pass

    def add(self, text: str) -> None:
        text = (text or "").strip()
        self._idx = None
        self._draft = ""
        if not text:
            return
        if self.entries and self.entries[-1] == text:
            return
        self.entries.append(text)
        del self.entries[:-_MAX]
        self._save()

    @property
    def browsing(self) -> bool:
        return self._idx is not None

    def reset(self) -> None:
        self._idx = None

    def prev(self, current: str) -> str | None:
        """Older entry, or None when there is nothing older."""
        if not self.entries:
            return None
        if self._idx is None:
            self._draft = current
            self._idx = len(self.entries) - 1
        elif self._idx > 0:
            self._idx -= 1
        else:
            return None
        return self.entries[self._idx]

    def next(self) -> str | None:
        """Newer entry; the saved draft after the newest; None when not browsing."""
        if self._idx is None:
            return None
        if self._idx < len(self.entries) - 1:
            self._idx += 1
            return self.entries[self._idx]
        self._idx = None
        return self._draft
