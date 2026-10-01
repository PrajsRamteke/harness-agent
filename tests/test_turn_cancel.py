"""Stopping a turn: New chat / another session while a turn runs, Esc on a
shell command, and cancel reaching the right thread.

Bugs these pin down (seen live with the web remote):
  * web "New chat" while the model was thinking left the old turn running —
    it kept the busy flag, streamed into the new chat and every new prompt
    queued behind it forever;
  * a cancelled worker could still append its reply to the *new* session;
  * Esc on a long shell command didn't stop it: it held the shell lock for up
    to 60 s, so the next command (next turn) looked stuck;
  * cancel injected KeyboardInterrupt into whatever thread reused the id of
    the last streaming worker.
"""
from __future__ import annotations

import asyncio
import os
import threading
import time
import uuid
from types import SimpleNamespace

import pytest

from jarvis import state


# ─── TUI: New chat / resume while a turn runs ────────────────────────────


@pytest.fixture()
def hermetic_app(monkeypatch):
    monkeypatch.setenv("HARNESS_SKIP_UPDATE", "1")

    import jarvis.updater as updater
    import jarvis.mcp.registry as mcp_registry
    import jarvis.storage.sessions as sessions
    import jarvis.storage.settings as settings
    import jarvis.tui.prompt_history as prompt_history
    import jarvis.web.actions_api as actions_api

    monkeypatch.setattr(updater, "maybe_update_and_reexec", lambda: None)
    monkeypatch.setattr(mcp_registry, "auto_connect_servers", lambda console_print=None, **kw: None,
                        raising=False)
    monkeypatch.setattr(sessions, "db_init", lambda: None)
    monkeypatch.setattr(sessions, "db_create_session", lambda model: None)
    monkeypatch.setattr(sessions, "db_append_message", lambda *a, **k: None)
    monkeypatch.setattr(sessions, "db_set_title_if_empty", lambda *a, **k: None)
    monkeypatch.setattr(actions_api, "db_create_session", lambda model: 4242)
    monkeypatch.setattr(settings.Settings, "save", lambda self: None)
    monkeypatch.setattr(prompt_history.PromptHistory, "_save", lambda self: None)

    from jarvis.tui.app import JarvisTUI

    monkeypatch.setattr(JarvisTUI, "_warm_model_catalogs_background", lambda self: None)
    return JarvisTUI


class SlowModel:
    """Stands in for ``call_claude_stream``: "thinks" until told otherwise.

    ``release`` lets it return a finished reply even after the turn was
    cancelled (the race where a reply lands just as the user starts over).
    """

    def __init__(self, *, ignore_cancel: bool = False):
        self.started = threading.Event()
        self.release = threading.Event()
        self.exited = threading.Event()
        self.ignore_cancel = ignore_cancel

    def __call__(self):
        self.started.set()
        try:
            while not self.release.is_set():
                if not self.ignore_cancel and state.turn_cancelled():
                    raise KeyboardInterrupt()
                time.sleep(0.02)
            block = SimpleNamespace(type="text", text="the old reply")
            return SimpleNamespace(content=[block], stop_reason="end_turn",
                                   usage=SimpleNamespace(input_tokens=1, output_tokens=1))
        finally:
            self.exited.set()


def _patch_model(monkeypatch, model):
    import jarvis.repl.stream as stream

    monkeypatch.setattr(stream, "call_claude_stream", model)
    monkeypatch.setattr(state, "client", object())


async def _wait(pilot, cond, timeout=3.0):
    t = time.monotonic()
    while not cond() and time.monotonic() - t < timeout:
        await pilot.pause(0.05)
    return cond()


def _run_app(hermetic_app, model, body):
    """Run ``body(app, pilot)`` in a headless app. The fake model is always
    released at the end, so a regression fails the test instead of hanging
    it (the app can't exit while the old worker still "thinks")."""

    async def run():
        app = hermetic_app()
        async with app.run_test(size=(120, 40)) as pilot:
            try:
                await pilot.pause(0.3)
                await body(app, pilot)
            finally:
                if model is not None:
                    model.release.set()
                    await _wait(pilot, model.exited.is_set)

    asyncio.run(run())


def _fake_resume(monkeypatch, messages):
    import jarvis.tui.session_modal as sm

    def fake_resume(sid, console_print, preview=False, quiet=False):
        state.messages = list(messages)
        state.current_session_id = sid
        return True

    monkeypatch.setattr(sm, "resume_session_into_state", fake_resume)


@pytest.mark.parametrize("action", ["session_new", "session_resume"])
def test_web_session_change_stops_the_running_turn(hermetic_app, monkeypatch, action):
    model = SlowModel()
    _patch_model(monkeypatch, model)
    other = [{"role": "user", "content": "from the other session"}]
    _fake_resume(monkeypatch, other)

    async def body(app, pilot):
        app._begin_turn("think about it")
        assert await _wait(pilot, model.started.is_set)
        state.prompt_queue.append(("queued follow-up for the old chat", None))
        assert app._busy

        result = app._handle_web_action(action, {"session_id": 7})
        assert result["ok"]
        assert not app._busy, "the old turn must stop, not keep the new chat busy"
        assert await _wait(pilot, model.exited.is_set), "the worker must actually unwind"
        assert state.prompt_queue == [], "follow-ups for the old chat don't run in the new one"
        await pilot.pause(0.3)
        assert state.messages == ([] if action == "session_new" else other)
        assert "stopped the running reply" in result.get("stopped", "")
        assert "1 queued" in result["stopped"]

    _run_app(hermetic_app, model, body)


def test_web_new_chat_when_idle_changes_nothing_else(hermetic_app):
    async def body(app, pilot):
        state.prompt_queue.clear()
        result = app._handle_web_action("session_new", {})
        assert result["ok"] and "stopped" not in result
        assert state.current_session_id == 4242

    _run_app(hermetic_app, None, body)


def test_a_cancelled_reply_never_lands_in_the_new_chat(hermetic_app, monkeypatch):
    """The model finishes *after* the user started over: drop that reply."""
    model = SlowModel(ignore_cancel=True)  # a stream that doesn't notice cancel
    _patch_model(monkeypatch, model)

    async def body(app, pilot):
        app._begin_turn("think about it")
        assert await _wait(pilot, model.started.is_set)
        app._handle_web_action("session_new", {})
        model.release.set()  # the old reply arrives now
        assert await _wait(pilot, model.exited.is_set)
        await pilot.pause(0.4)
        assert state.messages == [], "the old turn's reply leaked into the new chat"
        assert not app._busy

    _run_app(hermetic_app, model, body)


def test_terminal_session_picker_stops_the_running_turn(hermetic_app, monkeypatch):
    model = SlowModel()
    _patch_model(monkeypatch, model)
    _fake_resume(monkeypatch, [])

    async def body(app, pilot):
        app._begin_turn("think about it")
        assert await _wait(pilot, model.started.is_set)
        app._resume_session(9)
        assert not app._busy
        assert await _wait(pilot, model.exited.is_set)
        assert state.current_session_id == 9

    _run_app(hermetic_app, model, body)


# ─── cancel reaches the right thread ─────────────────────────────────────


def test_cancel_interrupts_the_given_thread_not_a_stale_id(monkeypatch):
    import jarvis.repl.stream as stream

    hits = []
    monkeypatch.setattr(stream, "_raise_in_thread", lambda tid, exc: hits.append(tid) or True)
    monkeypatch.setattr(stream, "_worker_thread_id", 0)
    stream.cancel_current_stream(thread_id=1234)
    assert hits == [1234]
    hits.clear()
    stream.cancel_current_stream()  # no stream running, no thread given
    assert hits == [], "must not interrupt a thread that may have reused an old id"
    state.cancel_requested.clear()


def test_stream_worker_id_is_cleared_when_the_call_ends(monkeypatch):
    import jarvis.repl.stream as stream

    class Client:
        class messages:
            @staticmethod
            def stream(**_kw):
                raise RuntimeError("boom")

    monkeypatch.setattr(state, "client", Client())
    monkeypatch.setattr(state, "messages", [{"role": "user", "content": "hi"}])
    monkeypatch.setattr(stream, "build_system", lambda: "sys")
    with pytest.raises(RuntimeError):
        stream.call_claude_stream()
    assert stream._worker_thread_id == 0


# ─── Esc on a running shell command ──────────────────────────────────────


def _alive(marker: str) -> bool:
    import subprocess

    out = subprocess.run(["ps", "-A", "-o", "command="], capture_output=True, text=True).stdout
    return any(marker in line and "ps -A" not in line for line in out.splitlines())


@pytest.mark.skipif(os.name != "posix", reason="process trees via ps")
def test_cancelling_a_turn_kills_its_shell_command_and_frees_the_lock(monkeypatch, tmp_path):
    from jarvis.tools import shell

    monkeypatch.setattr(state, "auto_approve", True)
    monkeypatch.setattr(shell, "CWD", tmp_path)
    marker = f"jarvis-cancel-{uuid.uuid4().hex[:8]}"
    # A shell with a child: killing only the shell would orphan the sleeps.
    cmd = f"sh -c 'sleep 30; echo {marker}' & sh -c 'sleep 30; echo {marker}' ; wait"
    result: dict = {}

    def worker():
        result["out"] = shell.run_bash(cmd)

    t = threading.Thread(target=worker)
    t.start()
    time.sleep(0.6)
    assert _alive(marker)
    t0 = time.monotonic()
    state.cancel_thread(t.ident)
    try:
        t.join(timeout=5)
        assert not t.is_alive(), "run_bash must return soon after the turn is cancelled"
        assert time.monotonic() - t0 < 4
        assert "cancelled" in result["out"].lower()
        deadline = time.monotonic() + 3
        while _alive(marker) and time.monotonic() < deadline:
            time.sleep(0.1)
        assert not _alive(marker), "the command's child processes must be gone too"
        # The lock is free: the next command runs right away.
        t1 = time.monotonic()
        assert "exit=0" in shell.run_bash("echo next")
        assert time.monotonic() - t1 < 3
    finally:
        state.release_thread(t.ident)


def test_run_bash_output_and_exit_code_unchanged(monkeypatch, tmp_path):
    from jarvis.tools import shell

    monkeypatch.setattr(state, "auto_approve", True)
    monkeypatch.setattr(shell, "CWD", tmp_path)
    out = shell.run_bash("echo hello; echo oops >&2; exit 3")
    assert out.splitlines()[0] == "$ echo hello; echo oops >&2; exit 3"
    assert "exit=3" in out and "hello" in out and "[stderr]\noops" in out


def test_run_bash_timeout_unchanged(monkeypatch, tmp_path):
    from jarvis.tools import shell

    monkeypatch.setattr(state, "auto_approve", True)
    monkeypatch.setattr(shell, "CWD", tmp_path)
    t0 = time.monotonic()
    assert shell.run_bash("sleep 10", timeout=1) == "TIMEOUT after 1s"
    assert time.monotonic() - t0 < 4
