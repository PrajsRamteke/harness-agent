"""``/loop`` in the TUI (mixed into ``JarvisTUI``).

State and the ``schedule_wakeup`` tool live in ``jarvis/loop.py``. This side
owns the timer: it starts each run as a normal turn (badged ``⟳ loop #N``),
re-arms from the agent's schedule when the turn ends, folds streaks of quiet
runs (``noop=true``) into one clickable ``LoopQuietBlock``, and shows the
countdown on the activity line above the composer.

    /loop check if CI passed and fix failures   self-paced (agent picks the delay)
    /loop 5m check the deploy                   fixed interval
    /loop                                       status
    /loop stop                                  end it
"""
from __future__ import annotations

import time

from rich.markup import escape

from ... import loop as loop_state
from ... import state
from .. import theme as ui

_USAGE = (
    "usage: [bold]/loop <task>[/] (Jarvis paces itself) · [bold]/loop 5m <task>[/] "
    "(fixed interval) · [bold]/loop stop[/]"
)


class LoopMixin:
    def _loop_init(self) -> None:
        self._loop_timer = None
        self._loop_timer_at = 0.0
        self._loop_countdown = None
        self._loop_pending = False
        self._loop_run_turn: int | None = None  # turn id of the running loop run
        self._loop_run_start = 0                # transcript index where that run began
        self._loop_quiet = None                 # LoopQuietBlock of the current quiet streak

    # ── command ──────────────────────────────────────────────────────
    def _loop_command(self, arg: str) -> None:
        arg = (arg or "").strip()
        con = self._tui_console
        if arg.lower() in ("", "status"):
            loop = loop_state.active()
            if loop is None:
                con.print(f"[{ui.FG_DIM}]⟳ no loop running — {_USAGE}[/]")
            else:
                con.print(f"[{ui.ACCENT_3}]⟳[/] [{ui.FG_MUTE}]{escape(self._loop_status_text())}[/]"
                          f"\n[{ui.FG_DIM}]  task: {escape(loop.prompt)}[/]")
            return
        if arg.lower() in ("stop", "off", "end", "cancel"):
            if loop_state.active() is None:
                con.print(f"[{ui.FG_DIM}]⟳ no loop running[/]")
            else:
                self._loop_end("stopped")
            return
        interval, prompt = loop_state.parse_args(arg)
        if not prompt:
            con.print(f"[{ui.FG_DIM}]{_USAGE}[/]")
            return
        replaced = loop_state.active() is not None
        self._loop_cancel_timer()
        loop_state.start(prompt, interval)
        self._loop_quiet = None
        mode = (f"every {loop_state.fmt_delay(interval)}" if interval
                else "self-paced — Jarvis decides when to check again")
        con.print(
            f"[{ui.ACCENT_3}]⟳[/] [{ui.FG_MUTE}]loop {'replaced' if replaced else 'started'}[/] "
            f"[{ui.FG_DIM}]· {mode} · /loop stop to end[/]"
        )
        self._loop_fire()

    # ── runs ─────────────────────────────────────────────────────────
    def _loop_fire(self) -> None:
        """Timer fired (or the loop just started): run the task now if idle."""
        self._loop_timer = None
        loop = loop_state.active()
        if loop is None:
            return
        if self._busy or state.prompt_queue:
            self._loop_pending = True  # the user's turn first; runs right after
            return
        self._loop_pending = False
        self._loop_stop_countdown()
        prompt = loop_state.begin_run()
        self._loop_run_start = len(self._transcript().children)
        self._begin_turn(prompt, badge=f"⟳ loop #{loop.iteration}")
        self._loop_run_turn = self._turn_id

    def _loop_after_turn(self, cancelled: bool) -> None:
        """From ``_turn_done``: schedule the next run, fold quiet ones, or end."""
        if self._loop_run_turn is not None and self._loop_run_turn == self._turn_id:
            self._loop_run_turn = None
            loop = loop_state.active()
            quiet = bool(loop and loop.noop)
            reason = loop.reason if loop else ""
            runs = loop.iteration if loop else 0
            action, value = loop_state.finish_run(cancelled=cancelled)
            next_at = time.time() + float(value) if action == "schedule" else None
            if quiet and not cancelled:
                self._loop_fold_quiet(reason, next_at)
            else:
                if self._loop_quiet is not None:
                    self._loop_quiet.set_next(None)  # streak over — its "next" is stale
                self._loop_quiet = None
            if action == "schedule":
                self._loop_arm(float(value))
            elif value != "no loop":
                self._loop_end(str(value), already_stopped=True, runs=runs)
            return
        self._loop_reconcile()
        if self._loop_pending:
            self.call_later(self._loop_fire)

    def _loop_reconcile(self) -> None:
        """A normal turn may have changed the loop via schedule_wakeup."""
        loop = loop_state.active()
        if loop is None:
            if self._loop_timer is not None:
                self._loop_end("stopped", already_stopped=True)
            return
        if not loop.running and loop.next_at and abs(loop.next_at - self._loop_timer_at) > 1:
            self._loop_arm(max(1.0, loop.next_at - time.time()))

    def _loop_end(self, why: str, *, already_stopped: bool = False, runs: int | None = None) -> None:
        loop = loop_state.active()
        if runs is None:
            runs = loop.iteration if loop else 0
        if not already_stopped:
            loop_state.end_loop()
        self._loop_cancel_timer()
        self._loop_pending = False
        if self._loop_quiet is not None:
            self._loop_quiet.set_next(None, ended="loop ended")
        self._loop_quiet = None
        text = {
            "done": "loop finished",
            "no wakeup scheduled": "loop ended — Jarvis didn't schedule another run",
            "interrupted": "loop stopped (interrupted)",
            "stopped": "loop stopped",
        }.get(why, f"loop ended ({why})")
        suffix = f" after {runs} run{'s' if runs != 1 else ''}" if runs else ""
        self._tui_console.print(
            f"[{ui.ACCENT_3}]⟳[/] [{ui.FG_MUTE}]{escape(text)}{suffix}[/] "
            f"[{ui.FG_DIM}]· /loop <task> starts a new one[/]"
        )
        self._refresh_activity_widgets()

    # ── quiet runs ───────────────────────────────────────────────────
    def _loop_fold_quiet(self, reason: str, next_at: float | None) -> None:
        from ..transcript import LoopQuietBlock

        t = self._transcript()
        kids = list(t.children)
        blocks = [b for b in kids[self._loop_run_start:] if not isinstance(b, LoopQuietBlock)]
        if not blocks:
            return
        qb = self._loop_quiet
        contiguous = False
        if qb is not None and qb.parent is t and qb in kids:
            between = kids[kids.index(qb) + 1:kids.index(blocks[0])]
            contiguous = all(not b.display for b in between)
        if not contiguous:
            qb = LoopQuietBlock()
            t.add(qb, before=blocks[0])
            self._loop_quiet = qb
        qb.add_run(blocks, reason, next_at)

    # ── timer + countdown ────────────────────────────────────────────
    def _loop_arm(self, delay: float) -> None:
        self._loop_cancel_timer()
        self._loop_timer = self.set_timer(delay, self._loop_fire)
        self._loop_timer_at = time.time() + delay
        if self._loop_countdown is None:
            self._loop_countdown = self.set_interval(1.0, self._refresh_activity_widgets)
        self._refresh_activity_widgets()

    def _loop_cancel_timer(self) -> None:
        if self._loop_timer is not None:
            self._loop_timer.stop()
            self._loop_timer = None
        self._loop_timer_at = 0.0
        self._loop_stop_countdown()

    def _loop_stop_countdown(self) -> None:
        if self._loop_countdown is not None:
            self._loop_countdown.stop()
            self._loop_countdown = None

    def _loop_status_text(self) -> str:
        """``loop #3 · next run in 4m 12s · waiting for CI · 2 quiet · /loop stop``"""
        loop = loop_state.active()
        if loop is None or loop.running:
            return ""
        bits = [f"loop #{loop.iteration}"]
        if self._loop_pending:
            bits.append("runs after this turn")
        elif self._loop_timer_at:
            bits.append(f"next run in {loop_state.fmt_delay(self._loop_timer_at - time.time())}")
        if loop.reason:
            bits.append(loop.reason if len(loop.reason) <= 50 else loop.reason[:49] + "…")
        if loop.noop_streak:
            bits.append(f"{loop.noop_streak} quiet")
        bits.append("/loop stop")
        return " · ".join(bits)
