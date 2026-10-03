"""Queued messages: ids, edit, remove, "send now" (steer) — model, TUI bar, turn loop."""
from __future__ import annotations

import asyncio
import threading
import time
from types import SimpleNamespace

import pytest

from jarvis import prompt_queue as pq
from jarvis import state


@pytest.fixture(autouse=True)
def _empty_queue(monkeypatch):
    monkeypatch.setattr(state, "prompt_queue", [])
    yield


# ─── the queue model ─────────────────────────────────────────────────────

def test_entries_keep_the_tuple_shape_and_gain_ids():
    a = pq.add("first", ({}, {}))
    b = pq.add("second", ({}, {}), ["u1"])
    assert a[0] == "first" and len(a) == 2
    assert b[2] == ["u1"] and b.files == ["u1"]
    assert a.id != b.id and pq.find(b.id) is b
    # old-style entries (tests, the legacy REPL) are wrapped in place
    state.prompt_queue.append(("legacy", None))
    ids = [it.id for it in pq.items()]
    assert len(set(ids)) == 3 and pq.items()[2].text == "legacy"


def test_send_now_moves_to_the_front_after_other_steered_ones():
    a, b, c = pq.add("a"), pq.add("b"), pq.add("c")
    pq.steer(c.id)
    pq.steer(b.id)
    assert [it.text for it in pq.items()] == ["c", "b", "a"]
    assert pq.items()[0].steer and pq.items()[1].steer and not pq.items()[2].steer
    pq.steer(c.id, False)
    assert not pq.find(c.id).steer


def test_commands_cannot_be_sent_mid_turn():
    cmd = pq.add("/model")
    shell = pq.add("!ls")
    assert not cmd.steerable and not shell.steerable
    pq.steer(cmd.id)
    assert not pq.find(cmd.id).steer
    assert pq.apply_op({"op": "steer", "id": cmd.id})["code"] == "command"


def test_take_steered_skips_held_and_unsteered():
    a, b, c = pq.add("a"), pq.add("b"), pq.add("c")
    pq.steer(a.id)
    pq.steer(b.id)
    pq.set_held(b.id, True)
    taken = pq.take_steered()
    assert [t.text for t in taken] == ["a"]
    assert [it.text for it in pq.items()] == ["b", "c"]
    assert pq.pending() == 1  # b is being edited


def test_pop_next_skips_a_message_being_edited():
    a, b = pq.add("a"), pq.add("b")
    pq.set_held(a.id, True)
    assert pq.pop_next().text == "b"
    assert pq.pop_next() is None
    assert [it.text for it in pq.items()] == ["a"]


def test_edit_keeps_id_and_place_and_empty_removes():
    a, b = pq.add("a"), pq.add("b")
    pq.steer(b.id)
    new = pq.edit(b.id, "b, but better")
    assert new.id == b.id and new.steer and new.text == "b, but better"
    assert [it.text for it in pq.items()] == ["b, but better", "a"]
    pq.edit(b.id, "/model")  # became a command: can't stay "send now"
    assert not pq.find(b.id).steer
    pq.edit(a.id, "   ")
    assert pq.find(a.id) is None
    assert pq.edit("nope", "x") is None


def test_clear_keeps_the_one_being_edited():
    a, _b = pq.add("a"), pq.add("b")
    pq.set_held(a.id, True)
    assert pq.clear() == 1
    assert [it.id for it in pq.items()] == [a.id]


def test_apply_op_from_the_web():
    a, b = pq.add("a"), pq.add("b")
    assert pq.apply_op({"op": "edit", "id": a.id, "text": "A"})["ok"]
    assert pq.find(a.id).text == "A"
    assert pq.apply_op({"op": "steer", "id": b.id})["ok"]
    assert pq.items()[0].id == b.id
    assert pq.apply_op({"op": "remove", "id": a.id})["ok"]
    assert pq.apply_op({"op": "remove", "id": a.id})["code"] == "gone"
    pq.set_held(b.id, True)
    assert pq.apply_op({"op": "edit", "id": b.id, "text": "x"})["code"] == "editing"
    rows = pq.public()
    assert rows[0]["editing"] and rows[0]["steer"] and rows[0]["steerable"]


def test_steer_text_round_trips():
    text = pq.steer_text("also check the docs")
    assert text.startswith(pq.STEER_HEAD)
    assert pq.strip_steer(text) == ("also check the docs", True)
    assert pq.strip_steer("plain") == ("plain", False)


def test_attach_to_last_needs_a_user_message(monkeypatch):
    monkeypatch.setattr(state, "messages", [{"role": "assistant", "content": "hi"}])
    assert not pq.attach_to_last([{"type": "text", "text": "x"}])
    monkeypatch.setattr(state, "messages", [
        {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "t1", "content": "ok"}]},
    ])
    assert pq.attach_to_last([{"type": "text", "text": "x"}])
    assert [b["type"] for b in state.messages[-1]["content"]] == ["tool_result", "text"]


def test_steered_message_shows_as_steered_in_web_snapshots(monkeypatch):
    from jarvis.web.state_api import snapshot_messages

    monkeypatch.setattr(state, "messages", [
        {"role": "user", "content": "start"},
        {"role": "assistant", "content": [{"type": "tool_use", "id": "t1", "name": "run_bash", "input": {"command": "ls"}}]},
        {"role": "user", "content": [
            {"type": "tool_result", "tool_use_id": "t1", "content": "a.py"},
            {"type": "text", "text": pq.steer_text("skip the tests")},
        ]},
    ])
    you = [m for m in snapshot_messages() if m["role"] == "you"]
    assert you[0] == {"role": "you", "text": "start", "title": "You"}
    assert you[1]["text"] == "skip the tests" and you[1]["steered"] is True


# ─── TUI: the bar, its buttons, the turn loop ────────────────────────────

@pytest.fixture
def tui(monkeypatch):
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
    monkeypatch.setattr(state, "current_session_id", None)
    from jarvis.tui.app import JarvisTUI

    monkeypatch.setattr(JarvisTUI, "_warm_model_catalogs_background", lambda self: None)
    return JarvisTUI


async def _until(pilot, cond, secs=8.0):
    deadline = time.monotonic() + secs
    while not cond() and time.monotonic() < deadline:
        await pilot.pause(0.05)
    return cond()


def _hold_turn(app_cls, monkeypatch):
    """A turn that runs until ``release`` is set; records what ran."""
    seen: list[str] = []
    release = threading.Event()

    def fake_run_turn(self, inp, turn_id=None, attachments=None):
        def go():
            seen.append(inp)
            release.wait(10)
            self.call_from_thread(self._turn_done, turn_id)

        threading.Thread(target=go, daemon=True).start()

    monkeypatch.setattr(app_cls, "_run_turn", fake_run_turn)
    return seen, release


def test_bar_lists_queued_messages_with_buttons(tui, monkeypatch):
    from jarvis.tui.queue_bar import QueueBar

    seen, release = _hold_turn(tui, monkeypatch)

    async def run() -> None:
        app = tui()
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause(0.2)
            app._begin_turn("refactor the parser")
            app._stash_prompt("and then update the docs")
            app._stash_prompt("/model")
            await pilot.pause(0.1)
            bar = app.query_one("#queuebar", QueueBar)
            assert not bar.has_class("hidden")
            rows = bar.plain_rows()
            assert rows[0].startswith("≡ Queued 2") and "clear all" in rows[0]
            assert "and then update the docs" in rows[1] and "⚡ send now" in rows[1] and "✎ edit" in rows[1]
            assert "/model" in rows[2] and "send now" not in rows[2]  # commands run on their own

            # ⚡ send now on the first message (a real click on its button)
            first = pq.items()[0]
            x = bar.button_x(first.id, "send")
            row = [r for r in bar.children if getattr(r, "item", None) is first][0]
            await pilot.click(row, offset=(x, 0))
            await pilot.pause(0.1)
            assert pq.find(first.id).steer
            assert "next step" in bar.plain_rows()[1] and "↶ undo" in bar.plain_rows()[1]

            # ✕ on the command
            cmd = pq.items()[1]
            row = [r for r in bar.children if getattr(r, "item", None) is cmd][0]
            await pilot.click(row, offset=(bar.button_x(cmd.id, "remove"), 0))
            await pilot.pause(0.1)
            assert [it.text for it in pq.items()] == ["and then update the docs"]

            release.set()
            assert await _until(pilot, lambda: seen == ["refactor the parser", "and then update the docs"])
            assert await _until(pilot, lambda: bar.has_class("hidden"))

    asyncio.run(run())


def test_edit_in_the_box_saves_in_place_and_esc_keeps_it(tui, monkeypatch):
    from jarvis.tui.prompt_area import PromptArea

    seen, release = _hold_turn(tui, monkeypatch)

    async def run() -> None:
        app = tui()
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause(0.2)
            app._begin_turn("long task")
            app._stash_prompt("first follow-up")
            app._stash_prompt("second follow-up")
            prompt = app.query_one("#prompt", PromptArea)
            prompt.text = "a draft I was typing"
            await pilot.pause(0.05)

            first = pq.items()[0]
            app.queue_action("edit", first.id)
            await pilot.pause(0.05)
            assert prompt.text == "first follow-up" and pq.find(first.id).held
            assert "editing in the message box" in app.query_one("#queuebar").plain_rows()[1]

            prompt.text = "first follow-up, rewritten"
            await pilot.press("enter")
            await pilot.pause(0.1)
            assert [it.text for it in pq.items()] == ["first follow-up, rewritten", "second follow-up"]
            assert pq.items()[0].id == first.id and not pq.items()[0].held
            assert prompt.text == "a draft I was typing"  # the draft came back

            # ↑ in an empty box edits the last one; esc leaves it as it was
            prompt.clear()
            await pilot.press("up")
            await pilot.pause(0.05)
            assert prompt.text == "second follow-up"
            await pilot.press("escape")
            await pilot.pause(0.05)
            assert app._busy  # esc ended the edit, not the turn
            assert [it.text for it in pq.items()] == ["first follow-up, rewritten", "second follow-up"]
            assert not any(it.held for it in pq.items())

            release.set()
            assert await _until(pilot, lambda: len(seen) == 3)
            assert seen[1:] == ["first follow-up, rewritten", "second follow-up"]

    asyncio.run(run())


def test_send_now_joins_the_running_turn_between_tool_calls(tui, monkeypatch):
    """The real turn loop: a tool call, then "send now" — the next request
    carries the message inside the tool results, and the transcript shows it."""
    import copy

    import jarvis.repl.render as render
    import jarvis.repl.stream as stream
    from jarvis.tui.transcript import UserBlock

    monkeypatch.setattr(state, "messages", [])
    monkeypatch.setattr(state, "client", object())
    requests: list[list] = []
    tool_running = threading.Event()
    go_on = threading.Event()

    def fake_call():
        requests.append(copy.deepcopy(state.messages))
        if len(requests) == 1:
            return SimpleNamespace(stop_reason="tool_use", content=[
                {"type": "tool_use", "id": "t1", "name": "run_bash", "input": {"command": "pytest"}}])
        return SimpleNamespace(stop_reason="end_turn", content=[{"type": "text", "text": "Done."}])

    def fake_render(resp):
        if resp.stop_reason == "tool_use":
            tool_running.set()
            go_on.wait(10)  # the tool "runs" while the user clicks send now
            state.messages.append({"role": "user", "content": [
                {"type": "tool_result", "tool_use_id": "t1", "content": "3 passed"}]})
            return True
        return False

    monkeypatch.setattr(stream, "call_claude_stream", fake_call)
    monkeypatch.setattr(render, "render_assistant", fake_render)
    monkeypatch.setattr(render, "classify_empty_turn", lambda resp, n: None)

    async def run() -> None:
        app = tui()
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause(0.2)
            app._begin_turn("run the tests")
            assert await _until(pilot, tool_running.is_set)
            app._stash_prompt("also lint the changed files")
            app._stash_prompt("then summarise")
            app.queue_action("send", pq.items()[0].id)
            go_on.set()
            assert await _until(pilot, lambda: not app._busy and len(requests) >= 3, secs=10)

            # 2nd request: the steered text sits in the tool-result message
            last_user = requests[1][-1]
            assert last_user["role"] == "user"
            kinds = [b["type"] for b in last_user["content"]]
            assert kinds == ["tool_result", "text"]
            assert pq.strip_steer(last_user["content"][1]["text"]) == ("also lint the changed files", True)
            # the other message waited for the reply, then ran as its own turn
            assert requests[2][-1] == {"role": "user", "content": "then summarise"}
            blocks = [b for b in app.query(UserBlock)]
            steered = [b for b in blocks if b.steer]
            assert len(steered) == 1 and steered[0].text == "also lint the changed files"
            assert steered[0].badge == "↳ sent while working"

    asyncio.run(run())


# ─── web: /api/queue + /api/pin through the actions layer ────────────────

def test_web_actions_route_queue_and_pin(monkeypatch, tmp_path):
    from jarvis.storage import pin as pin_store
    from jarvis.web.actions_api import run_web_action

    monkeypatch.setattr(state, "pinned_context", "")
    monkeypatch.setattr(state, "pin_enabled", True)
    monkeypatch.setattr("jarvis.storage.prefs.PIN_FILE", tmp_path / "pinned.txt")
    monkeypatch.setattr(state, "save_pin_config", lambda: None)

    a = pq.add("hello")
    assert run_web_action("queue", {"op": "steer", "id": a.id}, console_print=print)["ok"]
    assert pq.find(a.id).steer

    r = run_web_action("pin", {"op": "add", "text": "Always use uv"}, console_print=print)
    assert r["ok"] and r["pin"]["items"] == [{"line": 1, "text": "Always use uv"}]
    run_web_action("pin", {"op": "add", "text": "Answer in English"}, console_print=print)
    r = run_web_action("pin", {"op": "update", "line": 2, "expect": "Answer in English",
                               "text": "Answer briefly"}, console_print=print)
    assert r["ok"] and pin_store.pin_text() == "Always use uv\nAnswer briefly"
    stale = run_web_action("pin", {"op": "remove", "line": 2, "expect": "Answer in English"},
                           console_print=print)
    assert not stale["ok"] and stale["code"] == "changed"
    assert run_web_action("pin", {"op": "remove", "line": 1, "expect": "Always use uv"},
                          console_print=print)["ok"]
    assert pin_store.pin_text() == "Answer briefly"
    assert (tmp_path / "pinned.txt").read_text() == "Answer briefly"
    r = run_web_action("pin", {"op": "toggle", "enabled": False}, console_print=print)
    assert r["pin"]["enabled"] is False and pin_store.injection_text() == ""
    r = run_web_action("pin", {"op": "save", "text": "  one\n\ntwo  "}, console_print=print)
    assert r["pin"]["text"] == "one\n\ntwo" and [i["line"] for i in r["pin"]["items"]] == [1, 3]
    assert run_web_action("pin", {"op": "clear"}, console_print=print)["pin"]["items"] == []


def test_http_routes_for_pin_and_queue(monkeypatch, tmp_path):
    """``GET/POST /api/pin`` and ``POST /api/queue`` through the real handler + bridge."""
    import json
    import urllib.request

    from jarvis.web.bridge import WebBridge
    from jarvis.web.handler import WebHandler
    from jarvis.web.server import _JarvisHTTPServer

    monkeypatch.setattr(state, "pinned_context", "Use uv")
    monkeypatch.setattr(state, "pin_enabled", True)
    monkeypatch.setattr("jarvis.storage.prefs.PIN_FILE", tmp_path / "pinned.txt")
    monkeypatch.setattr(state, "save_pin_config", lambda: None)

    bridge = WebBridge()
    bridge.token = "tok"
    events: list[tuple[str, dict]] = []
    real_emit = bridge.emit
    monkeypatch.setattr(bridge, "emit", lambda kind, data=None: (events.append((kind, data)), real_emit(kind, data)))
    handler = type("H", (WebHandler,), {"bridge": bridge, "app": None})
    srv = _JarvisHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{srv.server_address[1]}"

    def call(path, payload=None):
        req = urllib.request.Request(
            base + path,
            data=None if payload is None else json.dumps(payload).encode(),
            headers={"Content-Type": "application/json", "Authorization": "Bearer tok"},
            method="GET" if payload is None else "POST",
        )
        with urllib.request.urlopen(req, timeout=5) as res:
            return json.loads(res.read())

    try:
        assert call("/api/pin")["items"] == [{"line": 1, "text": "Use uv"}]
        body = call("/api/pin", {"op": "add", "text": "Be brief"})
        assert body["ok"] and [i["text"] for i in body["pin"]["items"]] == ["Use uv", "Be brief"]
        assert events[-1][0] == "pin" and events[-1][1]["lines"] == 2

        a = pq.add("tidy the imports")
        body = call("/api/queue", {"op": "steer", "id": a.id})
        assert body["ok"] and body["entries"][0]["steer"] is True
        assert events[-1] == ("queue", {"items": ["tidy the imports"], "entries": body["entries"]})
        body = call("/api/queue", {"op": "edit", "id": a.id, "text": "tidy imports in app.py"})
        assert body["entries"][0]["text"] == "tidy imports in app.py"
        body = call("/api/queue", {"op": "remove", "id": a.id})
        assert body["ok"] and body["entries"] == []
        assert call("/api/queue", {"op": "remove", "id": a.id})["code"] == "gone"
        snap = call("/api/state")
        assert snap["queue_items"] == [] and snap["pin"] == {"lines": 2, "enabled": True, "chars": len("Use uv\nBe brief")}
    finally:
        srv.shutdown()
        srv.server_close()
