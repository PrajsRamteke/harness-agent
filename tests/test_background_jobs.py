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
    yield
    bg.reset()


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


def test_router_offers_background_tools_for_slow_work(monkeypatch):
    import jarvis.tools.router as router

    def names(text):
        return {t["name"] for t in router.select_tools([{"role": "user", "content": text}])}

    monkeypatch.setattr(router, "_coding_agent_active", lambda: False)
    assert {"run_bg", "bg_output", "bg_kill"} <= names("run the full test suite")
    assert "run_bg" not in names("what's the capital of France")
    monkeypatch.setattr(router, "_coding_agent_active", lambda: True)
    assert "run_bg" in names("what's the capital of France")  # coding work: always


def test_tui_shows_jobs_in_sidebar_and_announces_finish(monkeypatch):
    import asyncio

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
    from jarvis.tui.app import JarvisTUI
    from jarvis.tui.sidebar import SidebarBody

    monkeypatch.setattr(JarvisTUI, "_warm_model_catalogs_background", lambda self: None)

    async def run() -> None:
        app = JarvisTUI()
        async with app.run_test(size=(170, 40)) as pilot:  # wide → sidebar shown
            await pilot.pause(0.3)
            out = await asyncio.to_thread(bg.run_bg, "sleep 1.2; echo built")
            jid = _job_id(out)
            body = app.query_one(SidebarBody)
            body.refresh()
            await pilot.pause(0.1)
            assert f"● #{jid} sleep 1.2; echo bui…" in body.render().plain  # clipped to fit
            for _ in range(60):
                await pilot.pause(0.1)
                if f"background job #{jid} finished" in app.query_one("#transcript").plain_text():
                    break
            assert f"background job #{jid} finished" in app.query_one("#transcript").plain_text()
            assert "done" in body.render().plain

    asyncio.run(run())
