"""``/loop`` — run a prompt again and again; the agent can pace itself.

* ``/loop 5m check the deploy`` — fixed interval: re-runs every 5 minutes.
* ``/loop check if CI passed and fix failures`` — self-paced: after each run
  the agent calls ``schedule_wakeup(delay_seconds, prompt, reason, noop)`` to
  set the next run (60 s – 1 h), or ``schedule_wakeup(stop=true)`` once the
  job is done. A self-paced run that schedules nothing ends the loop.

``noop=true`` marks a quiet run ("nothing changed"); the TUI folds a streak
of quiet runs into one line. This module holds the state and the tool; the
TUI (``tui/mixins/loop.py``) owns the timer and starts each run.
"""
from __future__ import annotations

import re
import threading
import time
from dataclasses import dataclass, field

MIN_DELAY = 60          # schedule_wakeup clamp (seconds)
MAX_DELAY = 3600
MIN_INTERVAL = 30       # fixed /loop <interval> clamp
MAX_INTERVAL = 24 * 3600


@dataclass
class Loop:
    prompt: str
    interval: float | None            # None = self-paced (schedule_wakeup)
    started_at: float = field(default_factory=time.time)
    iteration: int = 0
    running: bool = False             # a run (turn) is in progress
    # set during a run by schedule_wakeup
    scheduled: bool = False
    next_delay: float | None = None
    stop_requested: bool = False
    noop: bool = False
    reason: str = ""
    # between runs
    next_at: float | None = None      # time.time() of the next run
    noop_streak: int = 0
    last_run_at: float | None = None

    @property
    def self_paced(self) -> bool:
        return self.interval is None


_active: Loop | None = None
_lock = threading.Lock()


def active() -> Loop | None:
    return _active


def start(prompt: str, interval: float | None = None) -> Loop:
    global _active
    with _lock:
        _active = Loop(prompt=prompt.strip(), interval=interval)
        return _active


def end_loop() -> Loop | None:
    """End the loop; returns the loop that was running (or None)."""
    global _active
    with _lock:
        old, _active = _active, None
        return old



def begin_run() -> str:
    """A run is starting: reset per-run flags, return the prompt to send."""
    loop = _active
    if loop is None:
        return ""
    with _lock:
        loop.iteration += 1
        loop.running = True
        loop.scheduled = False
        loop.next_delay = None
        loop.stop_requested = False
        loop.noop = False
        loop.reason = ""
        loop.next_at = None
        loop.last_run_at = time.time()
        return loop.prompt


def finish_run(*, cancelled: bool = False) -> tuple[str, float | str]:
    """The run's turn ended. Returns ``("schedule", delay_seconds)`` or
    ``("stop", why)`` — and ends the loop in the latter case."""
    loop = _active
    if loop is None:
        return ("stop", "no loop")
    with _lock:
        loop.running = False
        loop.noop_streak = loop.noop_streak + 1 if loop.noop else 0
    if cancelled:
        end_loop()
        return ("stop", "interrupted")
    if loop.stop_requested:
        end_loop()
        return ("stop", "done")
    if loop.self_paced and not loop.scheduled:
        end_loop()
        return ("stop", "no wakeup scheduled")
    delay = loop.next_delay if loop.next_delay is not None else float(loop.interval or MIN_DELAY)
    loop.next_at = time.time() + delay
    return ("schedule", delay)


# ── parsing ───────────────────────────────────────────────────────────────

_INTERVAL_RE = re.compile(r"^(?:(\d+)h)?(?:(\d+)m)?(?:(\d+)s)?$", re.I)


def parse_interval(text: str) -> float | None:
    """``"90s"`` / ``"5m"`` / ``"1h30m"`` / ``"2h"`` → seconds (clamped); None if not an interval."""
    t = (text or "").strip().lower()
    if not t or not any(t.endswith(u) for u in ("h", "m", "s")):
        return None
    m = _INTERVAL_RE.match(t)
    if not m or not any(m.groups()):
        return None
    h, mi, s = (int(g or 0) for g in m.groups())
    secs = h * 3600 + mi * 60 + s
    if secs <= 0:
        return None
    return float(max(MIN_INTERVAL, min(MAX_INTERVAL, secs)))


def parse_args(arg: str) -> tuple[float | None, str]:
    """``"5m check CI"`` → ``(300.0, "check CI")``; no interval → ``(None, arg)``."""
    arg = (arg or "").strip()
    head, _, rest = arg.partition(" ")
    secs = parse_interval(head)
    if secs is not None and rest.strip():
        return secs, rest.strip()
    return None, arg


def fmt_delay(secs: float) -> str:
    secs = max(0, int(round(secs)))
    if secs < 60:
        return f"{secs}s"
    m, s = divmod(secs, 60)
    if m < 60:
        return f"{m}m" + (f" {s:02d}s" if s and m < 10 else "")
    h, m = divmod(m, 60)
    return f"{h}h" + (f" {m:02d}m" if m else "")


def _clock(ts: float) -> str:
    return time.strftime("%H:%M", time.localtime(ts))


# ── tool ──────────────────────────────────────────────────────────────────


def schedule_wakeup(delay_seconds: float | None = None, prompt: str = "", reason: str = "",
                    noop: bool = False, stop: bool = False) -> str:
    """Set when the current /loop runs next (or end it)."""
    loop = _active
    if loop is None:
        return ("ERROR: schedule_wakeup only works while a /loop is active — the user starts one "
                "with `/loop <task>` (self-paced) or `/loop 5m <task>`.")
    if stop:
        with _lock:
            loop.stop_requested = True
            loop.scheduled = True
            loop.reason = (reason or "").strip()
        if not loop.running:  # asked from a normal chat turn: end it now
            end_loop()
            return "Loop stopped."
        return "Loop will stop after this run — give the user the final result now."

    if delay_seconds is None:
        if loop.self_paced:
            return "ERROR: delay_seconds is required (60–3600) — or pass stop=true to end the loop."
        delay = float(loop.interval or MIN_DELAY)
    else:
        try:
            delay = float(delay_seconds)
        except (TypeError, ValueError):
            return f"ERROR: delay_seconds must be a number, got {delay_seconds!r}"
    clamped = max(MIN_DELAY, min(MAX_DELAY, delay))
    with _lock:
        loop.scheduled = True
        loop.next_delay = clamped
        loop.noop = bool(noop)
        loop.reason = " ".join((reason or "").split())[:200]
        if prompt and prompt.strip():
            loop.prompt = prompt.strip()
        if not loop.running:
            # Changed from a normal chat turn — the TUI re-arms its timer.
            loop.next_at = time.time() + clamped
    note = f" (clamped from {fmt_delay(delay)})" if clamped != delay else ""
    at = _clock(time.time() + clamped)
    quiet = " · quiet run" if noop else ""
    why = f" — {loop.reason}" if loop.reason else ""
    return f"Next run in {fmt_delay(clamped)}{note}, around {at}{quiet}{why}."


# ── system prompt ─────────────────────────────────────────────────────────


def prompt_block() -> str:
    loop = _active
    if loop is None:
        return ""
    task = loop.prompt if len(loop.prompt) <= 400 else loop.prompt[:399] + "…"
    if not loop.running:
        nxt = f"next run in {fmt_delay(loop.next_at - time.time())}" if loop.next_at else "waiting"
        return (
            f"\n\nLOOP: a /loop is waiting ({nxt}) with task: {task}\n"
            "This turn is a normal message from the user, not a loop run. Only call "
            "schedule_wakeup if the user asks to change the timing or stop the loop."
        )
    if loop.self_paced:
        return (
            f"\n\nLOOP MODE (self-paced /loop, run #{loop.iteration}). Task: {task}\n"
            "- Do the task once, then call schedule_wakeup(delay_seconds, reason, noop) to run it "
            "again later. If you don't call it, the loop ends after this run.\n"
            "- Pick delay_seconds (60–3600) from how fast the thing you're watching changes: a CI "
            "run that takes ~8 minutes deserves one ~480 s check, not eight 60 s ones; idle "
            "watching with nothing specific to wait for → 1200–1800 s.\n"
            "- noop=true when nothing changed since the last run (quiet runs are folded together "
            "in the UI); noop=false when you did something or found something worth reporting.\n"
            "- When the goal is reached — or clearly can't be — call schedule_wakeup(stop=true) "
            "and give the final result. Keep quiet runs short: report changes, not waiting."
        )
    return (
        f"\n\nLOOP MODE (/loop every {fmt_delay(loop.interval or 0)}, run #{loop.iteration}). "
        f"Task: {task}\n"
        "It re-runs automatically. Call schedule_wakeup(stop=true) when the goal is reached; "
        "schedule_wakeup(noop=true, reason=…) marks a run where nothing changed. Keep quiet runs short."
    )
