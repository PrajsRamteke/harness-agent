"""run_bg / bg_output / bg_kill — background shell jobs."""
import os
import time

import pytest

from jarvis import state
from jarvis.tools import background as bg


@pytest.fixture(autouse=True)
def _clean(monkeypatch, tmp_path):
    monkeypatch.setattr(state, "auto_approve", True)
    monkeypatch.setattr(bg, "LOG_DIR", tmp_path / "bg")
    bg.reset()
    bg.set_auto_wake(False)
    yield
    bg.reset()
    bg.set_auto_wake(False)


def _wait_done(timeout=10.0):
    deadline = time.monotonic() + timeout
    while any(j.running for j in bg.jobs()) and time.monotonic() < deadline:
        time.sleep(0.05)


def _job_id(out: str) -> int:
    assert out.startswith("started background job #"), out
    return int(out.split("#", 1)[1].split(" ", 1)[0])


def test_fast_command_answers_immediately():
    out = bg.run_bg("echo hello-bg")
    assert "exit=0" in out and "hello-bg" in out
    assert bg.prompt_block() == ""  # nothing left for the model to collect


def test_long_job_runs_in_background_and_is_collected():
    t0 = time.monotonic()
    jid = _job_id(bg.run_bg("sleep 1.5; echo tests-done; exit 3"))
    assert time.monotonic() - t0 < 1.4  # returned while the job still runs
    assert f"#{jid} running" in bg.prompt_block()
    assert "running for" in bg.bg_output(jid)

    out = bg.bg_output(jid, wait=10)
    assert f"background job #{jid} finished · exit 3" in out
    assert "tests-done" in out
    assert bg.prompt_block() == ""  # read → no longer nagging the model


def test_finished_unread_job_is_flagged_in_system_prompt():
    jid = _job_id(bg.run_bg("sleep 1.2; echo ok"))
    deadline = time.monotonic() + 10
    while bg.jobs()[0].running and time.monotonic() < deadline:
        time.sleep(0.05)
    block = bg.prompt_block()
    assert f"#{jid} FINISHED (exit 0" in block and f"bg_output(job_id={jid})" in block

    from jarvis.repl.system import _background_jobs_block

    assert _background_jobs_block() == block


def test_new_only_returns_just_fresh_output():
    jid = _job_id(bg.run_bg("echo one; sleep 1.3; echo two; sleep 0.4"))

    def output(text: str) -> str:  # the part after the headline (which echoes the cmd)
        return text.split(" ---\n", 1)[1]

    first = output(bg.bg_output(jid, new_only=True))
    assert first == "one"
    second = output(bg.bg_output(jid, wait=10, new_only=True))
    assert second == "two"


def test_kill_stops_the_whole_process_group():
    jid = _job_id(bg.run_bg("sleep 30 & sleep 30; wait"))
    job = bg.jobs()[0]
    out = bg.bg_kill(jid)
    assert out.startswith(f"stopped background job #{jid}")
    assert job.status == "killed"
    with pytest.raises(ProcessLookupError):
        os.killpg(job.proc.pid, 0)  # no process of the group survives
    assert "already killed" in bg.bg_kill(jid)


def test_denied_and_invalid_calls(monkeypatch):
    import jarvis.tools.shell as shell

    monkeypatch.setattr(state, "auto_approve", False)
    monkeypatch.setattr(shell.console, "prompt_shell_approval", lambda cmd: "n", raising=False)
    assert bg.run_bg("sleep 5") == "USER DENIED"
    assert bg.jobs() == []
    assert bg.run_bg("rm -rf / --no-preserve-root") == "BLOCKED: dangerous command"
    assert bg.bg_output(99).startswith("ERROR: no background job #99")
    assert bg.bg_output() .startswith("No background jobs")


def test_finish_hook_and_listing():
    seen = []
    bg.add_finish_hook(seen.append)
    try:
        jid = _job_id(bg.run_bg("sleep 1.1; false"))
        bg.bg_output(jid, wait=10)
        deadline = time.monotonic() + 2
        while not seen and time.monotonic() < deadline:
            time.sleep(0.02)
    finally:
        bg.remove_finish_hook(seen.append)
    assert [j.id for j in seen] == [jid] and seen[0].code == 1
    assert f"✗ #{jid} exit 1" in bg.bg_output()


def test_output_is_cleaned_of_ansi_and_progress_frames():
    raw = "\x1b[32mPASSED\x1b[0m\r\nDownloading  10%\rDownloading  55%\rDownloading 100%\n"
    assert bg.clean_output(raw) == "PASSED\nDownloading 100%\n"


def test_auto_wake_tells_the_model_not_to_wait_and_caps_waits(monkeypatch):
    bg.set_auto_wake(True)
    monkeypatch.setattr(bg, "WAKE_MAX_WAIT", 0.3)
    out = bg.run_bg("sleep 3; echo late")
    jid = _job_id(out)
    assert "Don't wait for it" in out and "end your turn" in out
    assert "wait=120" not in out
    assert "wakes you with its output" in bg.prompt_block()

    t0 = time.monotonic()
    polled = bg.bg_output(jid, wait=120)  # asked for 2 minutes…
    assert time.monotonic() - t0 < 2.0    # …but a turn never hangs on it
    assert "waits are capped" in polled and "Jarvis wakes you" in polled


def test_without_auto_wake_the_old_polling_advice_stays():
    out = bg.run_bg("sleep 2")
    assert "add wait=120" in out
    assert "wakes you" not in bg.prompt_block()


def test_wake_batch_takes_finished_unread_jobs_once():
    ok = _job_id(bg.run_bg("sleep 1.1; echo all-green"))
    bad = _job_id(bg.run_bg("sleep 1.1; echo boom; exit 2"))
    read = _job_id(bg.run_bg("sleep 1.1; echo seen"))
    stopped = _job_id(bg.run_bg("sleep 30"))
    bg.bg_kill(stopped)
    bg.bg_output(read, wait=10)  # the model already collected this one
    _wait_done()

    batch = bg.take_wake_batch()
    assert [j.id for j in batch] == [ok, bad]  # not the read or the killed one
    assert bg.take_wake_batch() == []           # handed over once
    assert bg.prompt_block() == ""              # no longer "unread"

    prompt, display, badge = bg.wake_message(batch)
    assert prompt.startswith("[Automatic message from Jarvis — not typed by the user.")
    assert f"Background job #{ok} finished after" in prompt and "all-green" in prompt
    assert f"Background job #{bad} failed · exit 2 after" in prompt and "boom" in prompt
    assert "$ sleep 1.1; echo boom; exit 2" in prompt
    assert badge == f"& job #{ok}, #{bad}"
    assert display == f"#{ok} finished · #{bad} failed · exit 2"


def test_router_offers_background_tools_for_slow_work(monkeypatch):
    import jarvis.tools.router as router

    def names(text):
        return {t["name"] for t in router.select_tools([{"role": "user", "content": text}])}

    monkeypatch.setattr(router, "_coding_agent_active", lambda: False)
    assert {"run_bg", "bg_output", "bg_kill"} <= names("run the full test suite")
    assert "run_bg" not in names("what's the capital of France")
    monkeypatch.setattr(router, "_coding_agent_active", lambda: True)
    assert "run_bg" in names("what's the capital of France")  # coding work: always


@pytest.fixture()
def tui(monkeypatch):
    """JarvisTUI whose turns run a scripted fake agent (no model calls)."""
    import threading

    monkeypatch.setenv("HARNESS_SKIP_UPDATE", "1")
    import jarvis.mcp.registry as mcp_registry
    import jarvis.storage.sessions as sessions
    import jarvis.storage.settings as settings
    import jarvis.tui.prompt_history as prompt_history
    import jarvis.updater as updater

    monkeypatch.setattr(updater, "maybe_update_and_reexec", lambda: None)
    monkeypatch.setattr(mcp_registry, "auto_connect_servers", lambda console_print=None, **kw: None,
                        raising=False)
    monkeypatch.setattr(sessions, "db_init", lambda: None)
    monkeypatch.setattr(sessions, "db_create_session", lambda model: None)
    monkeypatch.setattr(settings.Settings, "save", lambda self: None)
    monkeypatch.setattr(prompt_history.PromptHistory, "_save", lambda self: None)
    monkeypatch.setattr(state, "save_trace_config", lambda: None)
    monkeypatch.setattr(state, "prompt_queue", [])
    from jarvis.tui.app import JarvisTUI

    monkeypatch.setattr(JarvisTUI, "_warm_model_catalogs_background", lambda self: None)
    seen: list[str] = []
    script: list = []  # per-turn step, run on the worker thread (e.g. a sleep)

    def fake_run_turn(self, inp, turn_id=None):
        def go():
            seen.append(inp)
            step = script.pop(0) if script else None
            if step:
                step(self)
            time.sleep(0.05)
            self.call_from_thread(self._turn_done, turn_id)

        threading.Thread(target=go, daemon=True).start()

    monkeypatch.setattr(JarvisTUI, "_run_turn", fake_run_turn)
    return JarvisTUI, seen, script


async def _until(pilot, cond, secs=8.0):
    deadline = time.monotonic() + secs
    while not cond() and time.monotonic() < deadline:
        await pilot.pause(0.1)
    return cond()


def test_tui_wakes_the_agent_with_the_output_when_a_job_finishes(tui):
    """The job runs while nothing waits on it; when it exits, Jarvis starts a
    turn with its output (badged) — the agent never blocked on bg_output."""
    import asyncio

    from jarvis.tui.sidebar import SidebarBody
    from jarvis.tui.transcript import UserBlock

    JarvisTUI, seen, _script = tui

    async def run() -> None:
        app = JarvisTUI()
        async with app.run_test(size=(170, 40)) as pilot:  # wide → sidebar shown
            await pilot.pause(0.3)
            assert bg.auto_wake()
            out = await asyncio.to_thread(bg.run_bg, "sleep 1.2; echo built")
            jid = _job_id(out)
            assert "Don't wait for it" in out
            body = app.query_one(SidebarBody)
            body.refresh()
            await pilot.pause(0.1)
            assert f"● #{jid} sleep 1.2; echo bui…" in body.render().plain  # clipped to fit
            assert not app._busy  # nothing is blocked on the job

            assert await _until(pilot, lambda: bool(seen))
            assert "Automatic message from Jarvis" in seen[0] and "built" in seen[0]
            t = app.query_one("#transcript").plain_text()
            assert f"background job #{jid} finished" in t
            wake = list(app.query(UserBlock))[-1]
            assert wake.badge == f"& job #{jid}"
            assert wake.text.startswith(f"background job #{jid} finished · ")
            assert await _until(pilot, lambda: not app._busy)
            assert "done" in body.render().plain
            assert seen == [seen[0]]  # woken once, not again

    asyncio.run(run())


def test_tui_job_finishing_mid_turn_waits_for_the_turn_and_the_queue(tui):
    import asyncio

    JarvisTUI, seen, script = tui
    script.append(lambda app: time.sleep(1.8))  # the user's turn is still running

    async def run() -> None:
        app = JarvisTUI()
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause(0.3)
            await asyncio.to_thread(bg.run_bg, "sleep 1.1; echo mid-turn")
            app._begin_turn("refactor the parser")
            app._stash_prompt("and then update the docs")  # typed while busy
            assert await _until(pilot, lambda: len(seen) >= 3, secs=10)
            assert seen[:2] == ["refactor the parser", "and then update the docs"]
            assert "mid-turn" in seen[2] and "Automatic message" in seen[2]
            await _until(pilot, lambda: not app._busy)
            assert len(seen) == 3

    asyncio.run(run())


def test_tui_no_wake_right_after_esc(tui):
    import asyncio

    JarvisTUI, seen, script = tui

    def cancelled_turn(app):
        time.sleep(1.6)  # the job finishes during this turn…
        app._turn_cancelled = True  # …which the user then stops with Esc

    script.append(cancelled_turn)

    async def run() -> None:
        app = JarvisTUI()
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause(0.3)
            await asyncio.to_thread(bg.run_bg, "sleep 1.1; echo quiet")
            app._begin_turn("long task")
            await _until(pilot, lambda: not app._busy and len(seen) == 1, secs=10)
            await pilot.pause(1.5)
            assert seen == ["long task"]  # no automatic turn after the user's Esc
            assert "FINISHED" in bg.prompt_block()  # still offered to the model

    asyncio.run(run())
