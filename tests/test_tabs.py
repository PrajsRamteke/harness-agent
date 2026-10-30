"""Chat tabs: several sessions open at once (jarvis/tabs.py + tui/mixins/tabs.py)."""
from __future__ import annotations

import asyncio
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace

import pytest

from jarvis import state, tabs


@pytest.fixture(autouse=True)
def _one_tab():
    tabs.reset()
    yield
    tabs.reset()


# ─── core: state follows the thread's tab ───────────────────────────────

def test_each_tab_has_its_own_conversation():
    first = tabs.active()
    state.messages.append({"role": "user", "content": "one"})
    state.plan_mode = True
    state.total_out = 42

    second = tabs.create()
    assert tabs.active() is second
    assert state.messages == [] and state.plan_mode is False and state.total_out == 0
    state.messages.append({"role": "user", "content": "two"})

    tabs.activate(first)
    assert [m["content"] for m in state.messages] == ["one"]
    assert state.plan_mode is True and state.total_out == 42
    assert second.session["messages"][0]["content"] == "two"


def test_a_bound_thread_keeps_its_tab_while_another_is_shown():
    first = tabs.active()
    seen: list[str] = []
    started, go = threading.Event(), threading.Event()

    def worker():
        with tabs.bound(first):
            started.set()
            go.wait(5)
            state.messages.append({"role": "assistant", "content": "reply for tab 1"})
            seen.append(str(len(state.messages)))

    t = threading.Thread(target=worker)
    t.start()
    started.wait(5)
    tabs.create()  # the user opened a new tab meanwhile
    go.set()
    t.join(5)
    assert state.messages == []  # the shown (new) tab is untouched
    assert first.session["messages"] == [{"role": "assistant", "content": "reply for tab 1"}]


def test_tool_pool_and_subagent_threads_follow_the_turns_tab():
    first = tabs.active()
    second = tabs.create()
    tabs.activate(first)  # tab 1 on screen; the turn below works for tab 2
    out: dict[str, int] = {}

    def turn():
        with tabs.bound(second):
            state.messages.append({"role": "user", "content": "x"})
            with ThreadPoolExecutor(2) as ex:  # parallel tool calls
                out["pool"] = list(ex.map(tabs.carry(lambda _: len(state.messages)), [1, 2]))[0]
            parent = threading.get_ident()

            def sub():
                state.link_thread(threading.get_ident(), parent)  # a subagent
                try:
                    out["sub"] = len(state.messages)
                    out["sub_cancel"] = state.turn_cancelled()
                finally:
                    state.unlink_thread(threading.get_ident())

            state.cancel_thread(parent)
            th = threading.Thread(target=sub)
            th.start()
            th.join(5)
            state.release_thread(parent)

    t = threading.Thread(target=turn)
    t.start()
    t.join(5)
    assert out == {"pool": 1, "sub": 1, "sub_cancel": True}
    assert first.session["messages"] == []


def test_close_shows_the_neighbour_and_never_the_last_tab():
    a = tabs.active()
    b = tabs.create()
    c = tabs.create()
    assert [t.id for t in tabs.all_tabs()] == [a.id, b.id, c.id]
    tabs.activate(b)
    assert tabs.close(b) is c  # the one to its right, like a browser
    assert tabs.close(c) is a
    assert tabs.close(a) is None and tabs.count() == 1


def test_find_session_and_public_rows():
    tab = tabs.active()
    state.current_session_id = 7
    state.messages.append({"role": "user", "content": "fix   the\nlogin bug"})
    assert tabs.find_session(7) is tab
    row = tabs.public()[0]
    assert row["title"] == "fix the login bug" and row["active"] and not row["busy"]


def test_cancelling_one_tabs_stream_does_not_stop_the_others():
    from jarvis.repl import stream

    a_thread, b_thread = 9_000_001, 9_000_002
    closed: list[str] = []
    stream._thread_streams[a_thread] = SimpleNamespace(close=lambda: closed.append("a"))
    stream._thread_streams[b_thread] = SimpleNamespace(close=lambda: closed.append("b"))
    try:
        state.cancel_requested.clear()
        stream.cancel_current_stream(thread_id=a_thread)
        assert closed == ["a"]
        assert not state.cancel_requested.is_set()  # tab B's turn keeps going
    finally:
        stream._thread_streams.pop(a_thread, None)
        stream._thread_streams.pop(b_thread, None)
        state.release_thread(a_thread)


def test_frozen_system_prompt_is_kept_per_session(monkeypatch):
    from jarvis.repl import system

    builds: list[int] = []
    monkeypatch.setattr(system, "_build_system_now", lambda: builds.append(1) or f"prompt {len(builds)}")
    system._turn_prompts.clear()
    first = tabs.active()
    state.current_session_id = 101
    state.messages[:] = [{"role": "user", "content": "a"}]
    second = tabs.create()
    state.current_session_id = 102
    state.messages[:] = [{"role": "user", "content": "b"}]
    with tabs.bound(first):
        p1 = system.build_system()
    with tabs.bound(second):
        p2 = system.build_system()
    with tabs.bound(first):  # alternating requests: each keeps its frozen prompt
        assert system.build_system() == p1
    with tabs.bound(second):
        assert system.build_system() == p2
    assert len(builds) == 2


# ─── TUI ────────────────────────────────────────────────────────────────

@pytest.fixture()
def tui(monkeypatch):
    monkeypatch.setenv("HARNESS_SKIP_UPDATE", "1")
    import jarvis.mcp.registry as mcp_registry
    import jarvis.storage.sessions as sessions
    import jarvis.storage.settings as settings
    import jarvis.tui.prompt_history as prompt_history
    import jarvis.updater as updater

    ids = iter(range(500, 600))
    monkeypatch.setattr(updater, "maybe_update_and_reexec", lambda: None)
    monkeypatch.setattr(mcp_registry, "auto_connect_servers", lambda console_print=None, **kw: None,
                        raising=False)
    monkeypatch.setattr(sessions, "db_init", lambda: None)
    monkeypatch.setattr(sessions, "db_create_session", lambda model: next(ids))
    monkeypatch.setattr(sessions, "db_append_message", lambda *a, **k: None)
    monkeypatch.setattr(sessions, "db_set_title_if_empty", lambda *a, **k: None)
    monkeypatch.setattr(sessions, "db_get_title", lambda sid: "")
    monkeypatch.setattr(settings.Settings, "save", lambda self: None)
    monkeypatch.setattr(prompt_history.PromptHistory, "_save", lambda self: None)
    monkeypatch.setattr(state, "save_trace_config", lambda: None)
    from jarvis.tui.app import JarvisTUI

    monkeypatch.setattr(JarvisTUI, "_warm_model_catalogs_background", lambda self: None)
    return JarvisTUI


async def _until(pilot, cond, secs=8.0):
    deadline = time.monotonic() + secs
    while not cond() and time.monotonic() < deadline:
        await pilot.pause(0.05)
    return cond()


@pytest.fixture()
def slow_model(monkeypatch):
    """The real turn loop against a fake model that answers when told to."""
    import jarvis.repl.render as render
    import jarvis.repl.stream as stream

    monkeypatch.setattr(state, "client", object())
    gates: dict[str, threading.Event] = {}
    asked = threading.Event()

    def fake_call():
        prompt = next(m["content"] for m in reversed(state.messages) if m["role"] == "user")
        gate = gates.setdefault(prompt, threading.Event())
        asked.set()
        deadline = time.monotonic() + 10
        while not gate.is_set():
            if state.turn_cancelled():
                raise KeyboardInterrupt()
            if time.monotonic() > deadline:
                break
            time.sleep(0.02)
        return SimpleNamespace(stop_reason="end_turn", content=[{"type": "text", "text": f"answer to {prompt}"}])

    def fake_render(resp):
        state.last_assistant_text = resp.content[0]["text"]
        return False

    monkeypatch.setattr(stream, "call_claude_stream", fake_call)
    monkeypatch.setattr(render, "render_assistant", fake_render)
    monkeypatch.setattr(render, "classify_empty_turn", lambda resp, n: None)
    return SimpleNamespace(gates=gates, asked=asked,
                           release=lambda p: gates.setdefault(p, threading.Event()).set())


def test_new_tab_while_a_reply_runs_and_it_lands_in_its_own_tab(tui, slow_model):
    from jarvis.tui.tab_bar import TabBar

    async def run() -> None:
        app = tui()
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause(0.3)
            first = tabs.active()
            app._begin_turn("refactor auth")
            assert await _until(pilot, slow_model.asked.is_set)
            assert first.busy

            app.tab_action("new")
            second = tabs.active()
            assert second is not first and tabs.count() == 2
            assert not app._busy  # the shown tab is idle: the composer sends, not queues
            assert first.busy and first.transcript.display is False and second.transcript.display
            assert "New chat" in app.query_one("#tabbar", TabBar).render().plain

            # A turn in the new tab runs alongside the first one.
            app._begin_turn("write the docs")
            assert await _until(pilot, lambda: "write the docs" in slow_model.gates)
            assert first.busy and second.busy
            slow_model.release("write the docs")
            assert await _until(pilot, lambda: not second.busy)
            slow_model.release("refactor auth")
            assert await _until(pilot, lambda: not first.busy)

            texts = lambda t: [m["content"] if isinstance(m["content"], str) else m["content"][0]["text"]
                               for m in t.session["messages"]]
            assert texts(first) == ["refactor auth", "answer to refactor auth"]
            assert texts(second) == ["write the docs", "answer to write the docs"]
            assert first.unread and not second.unread  # finished while not shown

            app.tab_action("tab", first.id)
            await pilot.pause(0.1)
            assert tabs.active() is first and not first.unread
            assert "answer to refactor auth" not in second.transcript.plain_text()

    asyncio.run(run())


def test_esc_stops_only_the_shown_tabs_turn(tui, slow_model):
    async def run() -> None:
        app = tui()
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause(0.3)
            first = tabs.active()
            app._begin_turn("long job")
            assert await _until(pilot, slow_model.asked.is_set)
            app.tab_action("new")
            second = tabs.active()
            app._begin_turn("quick question")
            assert await _until(pilot, lambda: "quick question" in slow_model.gates)

            await pilot.press("escape")  # tab 2 is shown
            assert await _until(pilot, lambda: not second.busy)
            assert second.turn_cancelled
            await pilot.pause(0.3)
            assert first.busy and not first.turn_cancelled
            slow_model.release("long job")
            assert await _until(pilot, lambda: not first.busy)
            assert first.session["messages"][-1]["role"] == "assistant"

    asyncio.run(run())


def test_close_tab_and_tab_command(tui):
    async def run() -> None:
        app = tui()
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause(0.3)
            first = tabs.active()
            app._try_tab_command("/tab new")
            app._try_tab_command("/tab new")
            assert tabs.count() == 3
            app._try_tab_command("/tab 1")
            assert tabs.active() is first
            app._try_tab_command("/tab close 2")
            await pilot.pause(0.1)  # the closed tab's transcript is removed on the next frame
            assert tabs.count() == 2 and len(list(app.query("Transcript"))) == 2
            app.action_close_tab()  # closes tab 1, the neighbour shows
            assert tabs.count() == 1 and tabs.active() is not first
            last = tabs.active()
            last.session["messages"].append({"role": "user", "content": "hi"})
            app.action_close_tab()  # the last tab: a fresh chat in it instead
            assert tabs.count() == 1 and tabs.active() is last and last.session["messages"] == []

    asyncio.run(run())


def test_resume_a_session_open_in_another_tab_switches_to_it(tui):
    async def run() -> None:
        app = tui()
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause(0.3)
            first = tabs.active()
            sid = first.session_id
            app.tab_action("new")
            assert app._resume_session(sid)
            assert tabs.active() is first and tabs.count() == 2

    asyncio.run(run())


def test_composer_draft_is_kept_per_tab(tui):
    async def run() -> None:
        app = tui()
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause(0.3)
            first = tabs.active()
            prompt = app.query_one("#prompt")
            prompt.text = "half-written thought"
            app.tab_action("new")
            assert prompt.text == ""
            app.tab_action("tab", first.id)
            assert prompt.text == "half-written thought"

    asyncio.run(run())


def test_web_tabs_endpoint_and_state_fields(tui):
    from jarvis.web.state_api import state_fields

    async def run() -> None:
        app = tui()
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause(0.3)
            first = tabs.active()
            r = app._handle_web_action("tabs", {"op": "new"})
            assert r["ok"] and len(r["tabs"]) == 2 and r["tabs"][1]["active"]
            fields = state_fields()
            assert [t["active"] for t in fields["tabs"]] == [False, True]
            assert fields["session_id"] == tabs.active().session_id
            r = app._handle_web_action("tabs", {"op": "switch", "id": first.id})
            assert r["ok"] and tabs.active() is first
            assert not app._handle_web_action("tabs", {"op": "switch", "id": 999})["ok"]
            r = app._handle_web_action("tabs", {"op": "close", "id": first.id})
            assert r["ok"] and tabs.count() == 1

    asyncio.run(run())
