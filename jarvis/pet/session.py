"""What we did together this session — for the recap shown on ``/new``."""
from __future__ import annotations

import time
from dataclasses import dataclass, field


@dataclass
class SessionStats:
    t0: float = field(default_factory=time.time)
    files: set[str] = field(default_factory=set)
    added: int = 0
    removed: int = 0
    turns: int = 0
    tests_fixed: int = 0
    tests_pass: int = 0
    commits: int = 0

    def note_diff(self, path: str, added: int, removed: int) -> None:
        self.files.add(path)
        self.added += max(0, added)
        self.removed += max(0, removed)

    def note_event(self, event: str, *, fixed: bool = False) -> None:
        if event == "tests_pass":
            self.tests_pass += 1
            if fixed:
                self.tests_fixed += 1
        elif event == "commit":
            self.commits += 1

    @property
    def empty(self) -> bool:
        return not (self.turns or self.files or self.commits or self.tests_pass)

    def recap(self, name: str, now: float | None = None) -> str:
        """``✦ recap — 7 files · +312 −40 lines · 2 tests fixed · 38 min``"""
        mins = max(1, round(((now or time.time()) - self.t0) / 60))
        bits = []
        if self.files:
            n = len(self.files)
            bits.append(f"{n} file{'s' if n != 1 else ''} edited")
        if self.added or self.removed:
            bits.append(f"+{self.added} −{self.removed} lines")
        if self.tests_fixed:
            bits.append(f"{self.tests_fixed} test fix{'es' if self.tests_fixed != 1 else ''}")
        elif self.tests_pass:
            bits.append(f"{self.tests_pass} green test run{'s' if self.tests_pass != 1 else ''}")
        if self.commits:
            bits.append(f"{self.commits} commit{'s' if self.commits != 1 else ''}")
        if self.turns:
            bits.append(f"{self.turns} turn{'s' if self.turns != 1 else ''}")
        bits.append(f"{mins} min" if mins < 60 else f"{mins // 60}h{mins % 60:02d}m")
        return f"✦ {name}'s recap — " + " · ".join(bits)


_SESSION = SessionStats()


def current() -> SessionStats:
    return _SESSION


def reset() -> SessionStats:
    global _SESSION
    _SESSION = SessionStats()
    return _SESSION
