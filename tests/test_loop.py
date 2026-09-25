"""/loop + schedule_wakeup: self-paced and fixed-interval repeats, quiet-run folding."""
import asyncio
import re
import threading
import time

import pytest

from jarvis import loop as L


@pytest.fixture(autouse=True)
def _no_loop():
    L.end_loop()
    yield
    L.end_loop()


# ── state + tool ──────────────────────────────────────────────────────────


def test_parse_args_and_intervals():
    assert L.parse_args("5m check the deploy") == (300.0, "check the deploy")
    assert L.parse_args("1h30m nightly report") == (5400.0, "nightly report")
    assert L.parse_args("check if CI passed") == (None, "check if CI passed")
    assert L.parse_args("5m") == (None, "5m")  # an interval alone is the task, not a schedule
    assert L.parse_interval("5s") == L.MIN_INTERVAL  # clamped
    assert L.parse_interval("fix") is None


def test_schedule_wakeup_needs_a_loop_and_clamps():
    assert L.schedule_wakeup(300).startswith("ERROR: schedule_wakeup only works while a /loop")
    L.start("check CI")
    L.begin_run()
    out = L.schedule_wakeup(10, reason="CI running", noop=True)
    assert out.startswith("Next run in 1m (clamped from 10s)") and "CI running" in out
    assert L.finish_run() == ("schedule", 60)
    assert L.active().noop_streak == 1
    L.begin_run()
    assert "required" in L.schedule_wakeup()  # self-paced loops need a delay


def test_self_paced_run_without_schedule_ends_the_loop():
    L.start("watch it")
    L.begin_run()
    assert L.finish_run() == ("stop", "no wakeup scheduled")
    assert L.active() is None


def test_fixed_interval_repeats_and_stop_ends():
    L.start("ping", interval=300)
    L.begin_run()
    assert L.finish_run() == ("schedule", 300.0)
    L.begin_run()
    assert "stop after this run" in L.schedule_wakeup(stop=True)
    assert L.finish_run() == ("stop", "done")
    L.start("ping", interval=300)
    L.begin_run()
    assert L.finish_run(cancelled=True) == ("stop", "interrupted")


def test_prompt_block_explains_the_protocol():
    L.start("check CI")
    L.begin_run()
    block = L.prompt_block()
    assert "LOOP MODE (self-paced /loop, run #1)" in block and "schedule_wakeup" in block
    L.schedule_wakeup(600)
    L.finish_run()
    waiting = L.prompt_block()
    assert "a /loop is waiting (next run in 10m" in waiting and "normal message" in waiting
    assert "Only call schedule_wakeup if the user asks" in waiting
    # Stop from a normal chat turn ends it right away.
    assert L.schedule_wakeup(stop=True) == "Loop stopped."
    assert L.active() is None and L.prompt_block() == ""


# ── TUI ───────────────────────────────────────────────────────────────────


@pytest.fixture()
def app_with_fake_agent(monkeypatch):
    """JarvisTUI whose turns run a scripted 'agent' instead of a model."""
    monkeypatch.setenv("HARNESS_SKIP_UPDATE", "1")
    import jarvis.mcp.registry as mcp_registry
    import jarvis.storage.sessions as sessions
    import jarvis.storage.settings as settings
    import jarvis.tui.prompt_history as prompt_history
    import jarvis.updater as updater
    from jarvis import state

    monkeypatch.setattr(updater, "maybe_update_and_reexec", lambda: None)
    monkeypatch.setattr(mcp_registry, "auto_connect_servers", lambda console_print=None, **kw: None,
                        raising=False)
    monkeypatch.setattr(sessions, "db_init", lambda: None)
    monkeypatch.setattr(sessions, "db_create_session", lambda model: None)
    monkeypatch.setattr(settings.Settings, "save", lambda self: None)
    monkeypatch.setattr(prompt_history.PromptHistory, "_save", lambda self: None)
    monkeypatch.setattr(state, "save_trace_config", lambda: None)
    from jarvis.tui.app import JarvisTUI

    monkeypatch.setattr(JarvisTUI, "_warm_model_catalogs_background", lambda self: None)
    script: list = []
    seen: list[str] = []

    def fake_run_turn(self, inp, turn_id=None):
        def go():
            seen.append(inp)
            step = script.pop(0) if script else None
            if step:
                step()
            self._tui_console.print(f"checked: {inp}")
            time.sleep(0.05)
            self.call_from_thread(self._turn_done, turn_id)

        threading.Thread(target=go, daemon=True).start()

    monkeypatch.setattr(JarvisTUI, "_run_turn", fake_run_turn)
    return JarvisTUI, script, seen


async def _send(pilot, app, text):
    prompt = app.query_one("#prompt")
    prompt.text = text
    await pilot.press("enter")


async def _idle(pilot, app, secs=3.0):
    end = time.monotonic() + secs
    await pilot.pause(0.1)
    while app._busy and time.monotonic() < end:
        await pilot.pause(0.05)
    await pilot.pause(0.1)


def test_self_paced_loop_folds_quiet_runs_and_stops(app_with_fake_agent):
    from jarvis.tui.transcript import LoopQuietBlock, UserBlock

    JarvisTUI, script, seen = app_with_fake_agent
    script.extend([
        lambda: L.schedule_wakeup(480, reason="CI still running", noop=True),
        lambda: L.schedule_wakeup(480, reason="CI still running", noop=True),
        lambda: L.schedule_wakeup(300, reason="fixed the failing test", noop=False),
        lambda: L.schedule_wakeup(stop=True, reason="CI green"),
    ])

    async def run() -> None:
        app = JarvisTUI()
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause(0.3)
            await _send(pilot, app, "/loop check if CI passed and fix failures")
            await _idle(pilot, app)
            t = app.query_one("#transcript")
            assert "loop started" in t.plain_text()
            blocks = list(app.query(UserBlock))
            assert blocks[-1].badge == "⟳ loop #1" and blocks[-1].has_class("-loop")
            assert not blocks[-1].display  # quiet run folded away
            quiet = list(app.query(LoopQuietBlock))
            assert len(quiet) == 1 and quiet[0].count == 1
            assert app._loop_timer is not None
            status = app._loop_status_text()
            assert re.match(r"loop #1 · next run in (8m|7m 5\ds)", status), status
            assert "CI still running" in status
            assert "1 quiet" in status and "/loop stop" in status

            app._loop_fire()  # the timer firing
            await _idle(pilot, app)
            assert len(list(app.query(LoopQuietBlock))) == 1 and quiet[0].count == 2
            assert "2 quiet checks" in quiet[0].plain_text()

            app._loop_fire()  # run #3 did real work → shown, streak broken
            await _idle(pilot, app)
            third = list(app.query(UserBlock))[-1]
            assert third.badge == "⟳ loop #3" and third.display
            assert "next" not in quiet[0].plain_text()  # streak over, no stale time

            app._loop_fire()  # run #4 stops the loop
            await _idle(pilot, app)
            assert L.active() is None and app._loop_timer is None
            assert "loop finished after 4 runs" in t.plain_text()
            assert seen == ["check if CI passed and fix failures"] * 4

            # Unfold the quiet streak.
            quiet[0].on_click()
            await pilot.pause(0.1)
            assert all(b.display for b in quiet[0].hidden_blocks)

    asyncio.run(run())


def test_loop_without_wakeup_ends_and_fixed_interval_keeps_going(app_with_fake_agent):
    JarvisTUI, script, _seen = app_with_fake_agent

    async def run() -> None:
        app = JarvisTUI()
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause(0.3)
            t = app.query_one("#transcript")
            await _send(pilot, app, "/loop watch the logs")
            await _idle(pilot, app)
            assert "loop ended — Jarvis didn't schedule another run after 1 run" in t.plain_text()
            assert L.active() is None

            await _send(pilot, app, "/loop 5m ping the server")
            await _idle(pilot, app)
            assert L.active() is not None and app._loop_timer is not None
            assert re.match(r"loop #1 · next run in (5m|4m 5\ds)", app._loop_status_text())
            await _send(pilot, app, "/loop stop")
            await pilot.pause(0.1)
            assert L.active() is None and app._loop_timer is None
            assert "loop stopped after 1 run" in t.plain_text()

    asyncio.run(run())


def test_loop_waits_for_the_users_turn(app_with_fake_agent):
    JarvisTUI, script, seen = app_with_fake_agent
    script.extend([lambda: L.schedule_wakeup(600), None, lambda: L.schedule_wakeup(600)])

    async def run() -> None:
        app = JarvisTUI()
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause(0.3)
            await _send(pilot, app, "/loop check CI")
            await _idle(pilot, app)
            app._busy = True          # the user is mid-turn when the timer fires
            app._loop_fire()
            assert app._loop_pending and "runs after this turn" in app._loop_status_text()
            app._busy = False
            await _send(pilot, app, "what's 2+2?")
            await _idle(pilot, app)
            await _idle(pilot, app)
            assert seen == ["check CI", "what's 2+2?", "check CI"]
            assert not app._loop_pending

    asyncio.run(run())
