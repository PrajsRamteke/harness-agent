"""Background jobs in the TUI (mixed into ``JarvisTUI``): announce and auto-wake.

``run_bg`` jobs run detached (``tools/background.py``). Nobody should have to
sit waiting on one: the agent starts it and keeps working or ends its turn.
When a job exits, this side prints the ✓/✗ notice and — once Jarvis is idle —
starts a turn (badged ``& job #N``) that hands the agent the job's output, so
it carries on (fix what failed, report the result) without being asked.

Ordering: the user's queued prompts run first, a job finishing mid-turn waits
for that turn, jobs finishing together share one wake-up turn, and a turn the
user just cancelled (Esc) isn't followed by one — the job stays listed as
unread in the system prompt instead.

Tabs: the wake-up turn runs in the tab whose chat started the job
(``background.owner_tab``) — never in whatever tab happens to be on screen.
A job whose chat was closed wakes nobody; it shows up as unread when that
chat is opened again.
"""
from __future__ import annotations

from rich.markup import escape as _rich_escape

from ... import prompt_queue, state, tabs
from .. import theme as ui

# Jobs that finish within this window share one wake-up turn.
WAKE_BATCH_SECS = 0.8


class BgJobsMixin:
    def _bg_init(self) -> None:
        self._bg_wake_timer = None

    def _bg_attach(self) -> None:
        from ...tools import background as bg

        bg.add_finish_hook(self._on_bg_job_finished)
        bg.set_auto_wake(True)

    def _bg_detach(self) -> None:
        from ...tools import background as bg

        bg.remove_finish_hook(self._on_bg_job_finished)
        bg.set_auto_wake(False)

    def _on_bg_job_finished(self, job) -> None:
        """Watcher thread: a run_bg job exited."""
        if not self.is_running:
            return
        try:
            self.call_from_thread(self._bg_job_finished_ui, job)
        except Exception:
            pass

    def _bg_job_finished_ui(self, job) -> None:
        from ...tools.background import auto_wake, fmt_secs

        if job.killed:
            mark, color, what = "✕", ui.FG_DIM, "stopped"
        elif job.code == 0:
            mark, color, what = "✓", ui.OK, "finished"
        else:
            mark, color, what = "✗", ui.ERR, f"failed · exit {job.code}"
        from ...tools.background import owner_tab

        owner = owner_tab(job) if job.tab_id is not None or job.session_id else tabs.active()
        if owner is None:
            return  # its chat isn't open any more: nothing to print into, nobody to wake
        cmd = " ".join(job.cmd.split())
        cmd = cmd if len(cmd) <= 70 else cmd[:69] + "…"
        try:
            with tabs.bound(owner):  # the notice goes to the job's own chat
                self._tui_console.print(
                    f"[{color}]{mark}[/] [{ui.FG_MUTE}]background job #{job.id} {what}[/] "
                    f"[{ui.FG_DIM}]· {fmt_secs(job.elapsed)} · {_rich_escape(cmd)}[/]"
                )
        except Exception:
            pass
        if not job.killed and auto_wake() and self._bg_wake_timer is None:
            self._bg_wake_timer = self.set_timer(WAKE_BATCH_SECS, self._bg_wake)

    def _bg_wake(self) -> bool:
        """Timer: wake every idle tab that has finished, unread jobs of its
        own. Returns True when the tab on screen started a turn."""
        self._bg_wake_timer = None
        shown = False
        for tab in tabs.all_tabs():
            with tabs.bound(tab):
                if self._bg_wake_tab() and tab.is_active():
                    shown = True
        return shown

    def _bg_wake_tab(self) -> bool:
        """Start a turn in the current tab with the output of *its* finished,
        unread jobs — only when that tab is idle. Returns True when one started.
        Re-run from ``_turn_done`` (bound to the tab whose turn ended)."""
        if self._busy or prompt_queue.pending():
            return False  # the current / queued work first; retried at turn end
        from ...tools import background as bg

        batch = bg.take_wake_batch(tabs.current())
        if not batch:
            return False
        prompt, display, badge = bg.wake_message(batch)
        self._begin_turn(prompt, display=display, badge=badge)
        return True
