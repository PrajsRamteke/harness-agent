"""/btw — side questions answered in their own window, beside a running turn."""
import contextlib
import json
import threading
import time
import urllib.error
import urllib.request
from types import SimpleNamespace

import pytest

from jarvis import btw, tabs


# ── a model that streams ───────────────────────────────────────────────────

class FakeClient:
    """``client.messages.stream(**kw)`` → ``text_stream`` + a final message."""

    def __init__(self, pieces=("Working on ", "step 3 of 5."), gate=False, hold=None):
        self.pieces = list(pieces)
        self.calls: list[dict] = []
        self.messages = self
        self.hold = hold
        if gate:
            self.gate_tools = [{"name": "bash"}]

    @contextlib.contextmanager
    def stream(self, **kwargs):
        self.calls.append(kwargs)
        pieces = list(self.pieces)

        def text_stream():
            for p in pieces:
                if p == "<hold>":
                    if self.hold is not None:
                        self.hold.wait(5)
                    continue
                yield p

        text = "".join(p for p in pieces if p != "<hold>")
        if getattr(self, "gate_tools", None):
            final = SimpleNamespace(content=[SimpleNamespace(
                type="tool_use", id="t1", name="reply", input={"text": text})])
        else:
            final = SimpleNamespace(content=[SimpleNamespace(type="text", text=text)])
        yield SimpleNamespace(text_stream=text_stream(), get_final_message=lambda: final)


@pytest.fixture(autouse=True)
def _btw_disk(tmp_path, monkeypatch):
    """Side chats are saved to disk — point that at a temp file, never the user's."""
    monkeypatch.setattr(btw, "CHATS_FILE", tmp_path / "btw_chats.json")
    btw.reset()
    yield
    btw.reset()


@pytest.fixture()
def chat(monkeypatch):
    """A chat that is mid-turn: some history, a running tool, nothing queued."""
    btw.reset()
    tab = tabs.active()
    monkeypatch.setitem(tab.session, "messages", [
        {"role": "user", "content": "Add retry logic to the uploader and run the tests"},
        {"role": "assistant", "content": [
            {"type": "text", "text": "I'll start with the uploader."},
            {"type": "tool_use", "id": "a", "name": "read_file", "input": {"path": "jarvis/upload.py"}},
        ]},
        {"role": "user", "content": [
            {"type": "tool_result", "tool_use_id": "a", "content": "x" * 5000},
        ]},
    ])
    monkeypatch.setitem(tab.session, "prompt_queue", [])
    monkeypatch.setattr(tab, "busy", True)
    monkeypatch.setattr(tab, "busy_since", time.time() - 95)
    monkeypatch.setattr(tab, "activity_label", "Reading upload.py")
    client = FakeClient()
    monkeypatch.setitem(tab.config, "client", client)
    monkeypatch.setitem(tab.config, "MODEL", "test-model")
    monkeypatch.setitem(tab.config, "provider", "anthropic")
    monkeypatch.setitem(tab.config, "auth_mode", "api_key")
    yield SimpleNamespace(tab=tab, client=client)
    btw.reset()


def run_ask(question, **kw):
    """Ask and wait for the answer: ``(thread, [events])``."""
    events: list[btw.Event] = []
    finished = threading.Event()

    def on_event(e):
        events.append(e)
        if e.op in ("done", "error"):
            finished.set()

    thread = btw.ask(question, on_event=on_event, **kw)
    assert finished.wait(5), f"no answer: {[e.op for e in events]}"
    return thread, events


# ── /btw parsing ───────────────────────────────────────────────────────────

@pytest.mark.parametrize("text,want", [
    ("/btw what is the progress?", "what is the progress?"),
    ("  /BTW   how long\nwill it take  ", "how long\nwill it take"),
    ("/btw", ""),
    ("/btw   ", ""),
    ("/btwx hello", None),
    ("/btwhat", None),
    ("btw hello", None),
    ("please /btw hi", None),
    ("/model", None),
])
def test_parse(text, want):
    assert btw.parse(text) == want
    assert btw.is_btw(text) == (want is not None)


# ── the snapshot the model gets ────────────────────────────────────────────

def test_conversation_keeps_the_newest_messages_when_it_does_not_fit():
    msgs = [{"role": "user", "content": f"message {i} " + "y" * 400} for i in range(100)]
    out = btw.render_conversation(msgs, budget=3000)
    assert "message 99" in out and "message 0 " not in out
    assert out.startswith("(earlier messages left out)")
    assert len(out) < 3600


def test_tool_calls_and_results_are_short_but_readable():
    msgs = [
        {"role": "assistant", "content": [{"type": "tool_use", "id": "1", "name": "read_file",
                                           "input": {"path": "jarvis/upload.py"}}]},
        {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "1", "content": "z" * 9000}]},
        {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "2", "content": "boom", "is_error": True}]},
    ]
    out = btw.render_conversation(msgs)
    assert "→ Reading file: jarvis/upload.py" in out
    assert out.count("z") <= btw.TOOL_RESULT_CHARS
    assert "← result (failed): boom" in out


def test_live_status_says_what_the_turn_is_doing(chat):
    out = btw.live_status(chat.tab)
    assert "Main task: working for 1m 3" in out or "Main task: working for 1m 4" in out  # ~95 s
    assert "Current step: Reading upload.py" in out


def test_live_status_idle(chat, monkeypatch):
    monkeypatch.setattr(chat.tab, "busy", False)
    assert "idle" in btw.live_status(chat.tab)


# ── asking ─────────────────────────────────────────────────────────────────

def test_answer_streams_then_completes_and_sees_the_live_chat(chat):
    thread, events = run_ask("what's the progress?")
    ops = [e.op for e in events]
    assert ops[0] == "start" and ops[-1] == "done"
    assert "".join(e.text for e in events if e.op == "delta") == "Working on step 3 of 5."
    assert events[-1].text == "Working on step 3 of 5."
    assert thread.pairs == [("what's the progress?", "Working on step 3 of 5.")]
    assert not thread.running

    call = chat.client.calls[0]
    system = call["system"]
    assert "Current step: Reading upload.py" in system        # live status
    assert "Add retry logic to the uploader" in system          # the conversation
    assert "→ Reading file: jarvis/upload.py" in system
    assert "tools" not in call                                  # read-only
    assert call["messages"] == [{"role": "user", "content": "what's the progress?"}]


def test_the_main_chat_is_left_exactly_as_it_was(chat):
    before_messages = json.dumps(chat.tab.session["messages"], sort_keys=True)
    before_queue = list(chat.tab.session["prompt_queue"])
    before_calls = chat.tab.session.get("tool_calls_count")
    before_tokens = (chat.tab.session["total_in"], chat.tab.session["total_out"])
    first, _ = run_ask("how long will it take?")
    run_ask("and the tests?", thread_id=first.id)
    assert json.dumps(chat.tab.session["messages"], sort_keys=True) == before_messages
    assert chat.tab.session["prompt_queue"] == before_queue
    assert chat.tab.session.get("tool_calls_count") == before_calls
    assert (chat.tab.session["total_in"], chat.tab.session["total_out"]) == before_tokens
    assert chat.tab.busy is True                                # still working, untouched


def test_follow_ups_continue_the_side_conversation_with_a_fresh_snapshot(chat):
    thread, _ = run_ask("what's the progress?")
    chat.tab.activity_label = "Running pytest"                    # the turn moved on meanwhile
    chat.client.pieces = ["Now running the tests."]
    again, events = run_ask("and now?", thread_id=thread.id)
    assert again.id == thread.id
    assert thread.pairs[-1] == ("and now?", "Now running the tests.")
    second = chat.client.calls[1]
    assert "Current step: Running pytest" in second["system"]     # re-read, not cached
    assert second["messages"] == [
        {"role": "user", "content": "what's the progress?"},
        {"role": "assistant", "content": "Working on step 3 of 5."},
        {"role": "user", "content": "and now?"},
    ]
    assert events[0].turn == 1


def test_questions_are_validated(chat):
    with pytest.raises(btw.BtwError, match="type a question"):
        btw.ask("   ")
    with pytest.raises(btw.BtwError, match="under"):
        btw.ask("x" * (btw.MAX_QUESTION + 1))
    with pytest.raises(btw.BtwError, match="closed"):
        btw.ask("hi", thread_id="nope")


def test_one_answer_at_a_time_per_thread(chat):
    gate = threading.Event()
    chat.client.pieces = ["a", "<hold>", "b"]
    chat.client.hold = gate
    thread = btw.ask("slow one", on_event=lambda e: None)
    try:
        with pytest.raises(btw.BtwError, match="still answering"):
            btw.ask("another", thread_id=thread.id)
    finally:
        gate.set()


def test_stop_ends_the_answer_quietly(chat):
    gate = threading.Event()
    chat.client.pieces = ["start ", "<hold>", "never shown"]
    chat.client.hold = gate
    events: list[btw.Event] = []
    thread = btw.ask("slow one", on_event=events.append)
    deadline = time.time() + 3
    while not any(e.op == "delta" for e in events) and time.time() < deadline:
        time.sleep(0.01)
    assert btw.stop(thread.id) is True
    gate.set()
    time.sleep(0.2)
    assert not any(e.op in ("done", "error") for e in events)
    assert thread.pairs == [] and not thread.running
    # It can be asked again straight away.
    chat.client.hold = None
    chat.client.pieces = ["ok"]
    _, again = run_ask("retry", thread_id=thread.id)
    assert again[-1].op == "done"
    assert btw.stop(thread.id) is False                          # nothing running now


def test_closing_a_window_keeps_the_chat_in_the_history(chat):
    thread, _ = run_ask("hello")
    assert btw.close(thread.id) is True
    assert btw.get(thread.id) is None                               # no longer an open window
    assert btw.close(thread.id) is False                            # already closed
    kept = btw.archived(thread.id)                                  # but nothing was lost
    assert kept is not None and kept.pairs == [("hello", "Working on step 3 of 5.")]
    assert kept.title == "hello" and kept.closed_at > 0
    assert [t.id for t in btw.history()] == [thread.id]


def test_a_closed_chat_survives_a_restart(chat):
    thread, _ = run_ask("what's the progress?")
    btw.close(thread.id)
    assert btw.CHATS_FILE.exists()

    btw._threads.clear()                                             # Jarvis restarts
    btw._archive.clear()
    btw._loaded = False

    rows = btw.history()
    assert [(t.id, t.title, t.pairs) for t in rows] == [
        (thread.id, "what's the progress?", [("what's the progress?", "Working on step 3 of 5.")]),
    ]


def test_answers_are_saved_without_closing_first(chat):
    run_ask("progress?")
    assert btw.CHATS_FILE.exists()                                   # an answer is never only in RAM
    btw._archive.clear()                                             # a crash before the window closes
    btw._loaded = False
    assert [t.pairs[0][1] for t in btw.history()] == ["Working on step 3 of 5."]


def test_reopen_brings_the_conversation_back(chat):
    thread, _ = run_ask("progress?")
    btw.close(thread.id)
    again = btw.reopen(thread.id)
    assert again.id == thread.id and again.closed_at == 0
    assert again.pairs == [("progress?", "Working on step 3 of 5.")]
    assert btw.archived(thread.id) is None and btw.get(thread.id) is again
    with pytest.raises(btw.BtwError, match="already open"):
        btw.reopen(thread.id)


def test_reopened_chat_carries_on_with_its_history(chat):
    thread, _ = run_ask("progress?")
    btw.close(thread.id)
    btw.reopen(thread.id)
    chat.client.pieces = ["All done."]
    _, events = run_ask("and now?", thread_id=thread.id)
    assert events[-1].text == "All done."
    assert chat.client.calls[1]["messages"] == [
        {"role": "user", "content": "progress?"},
        {"role": "assistant", "content": "Working on step 3 of 5."},
        {"role": "user", "content": "and now?"},
    ]


def test_forget_is_the_only_way_to_lose_one(chat):
    thread, _ = run_ask("hello")
    btw.close(thread.id)
    assert btw.forget(thread.id) is True
    assert btw.history() == [] and btw.archived(thread.id) is None
    with pytest.raises(btw.BtwError, match="gone"):
        btw.reopen(thread.id)
    btw._loaded = False                                               # and it stays gone after a restart
    assert btw.history() == []


def test_opening_too_many_windows_archives_the_oldest(chat):
    first = btw.new_thread(chat.tab.id)
    first.pairs.append(("keep me", "saved"))
    for _ in range(btw.MAX_THREADS):
        btw.new_thread(chat.tab.id)
    assert btw.get(first.id) is None
    assert btw.archived(first.id).pairs == [("keep me", "saved")]   # never dropped on eviction


def test_provider_errors_become_short_messages(chat):
    class Boom(Exception):
        status_code = 429

    def stream(**kw):
        raise Boom("slow down\nlong trace")

    chat.client.stream = stream
    _, events = run_ask("hi")
    assert events[-1].op == "error"
    assert events[-1].text == "rate limited — try again in a moment"


def test_no_client_is_a_clear_error(chat, monkeypatch):
    monkeypatch.setitem(chat.tab.config, "client", None)
    _, events = run_ask("hi")
    assert events[-1].op == "error"
    assert events[-1].text == "no model connected — run /provider"


def test_model_that_reaches_for_a_tool_is_refused_then_answers(chat):
    seen = []

    @contextlib.contextmanager
    def stream(**kwargs):
        seen.append(kwargs)
        if len(seen) == 1:
            final = SimpleNamespace(content=[SimpleNamespace(type="tool_use", id="t", name="read_file",
                                                             input={"path": "x"})])
        else:
            final = SimpleNamespace(content=[SimpleNamespace(type="text", text="About halfway.")])
        yield SimpleNamespace(text_stream=iter(()), get_final_message=lambda: final)

    chat.client.stream = stream
    _, events = run_ask("progress?")
    assert events[-1].text == "About halfway."
    refusal = seen[1]["messages"][-1]["content"][0]
    assert refusal["is_error"] and "Tools are disabled" in refusal["content"]


def test_clients_that_force_tools_get_a_reply_tool(chat, monkeypatch):
    client = FakeClient(("Almost done.",), gate=True)
    monkeypatch.setitem(chat.tab.config, "client", client)
    _, events = run_ask("progress?")
    assert events[-1].text == "Almost done."
    assert client.calls[0]["tools"][0]["name"] == "reply"
    assert "".join(e.text for e in events if e.op == "delta") == "Almost done."


def test_oauth_keeps_the_identity_block_first(chat, monkeypatch):
    from jarvis.constants import AUTH_OAUTH, OAUTH_IDENTITY

    monkeypatch.setitem(chat.tab.config, "auth_mode", AUTH_OAUTH)
    run_ask("progress?")
    system = chat.client.calls[0]["system"]
    assert system[0] == {"type": "text", "text": OAUTH_IDENTITY}
    assert "live_status" in system[1]["text"]


def test_it_uses_the_chats_own_model_never_the_side_model(chat, monkeypatch):
    from jarvis import side_model

    def boom():
        raise AssertionError("the commit-text model must not see the conversation")

    monkeypatch.setattr(side_model, "resolve", boom)
    run_ask("progress?")
    assert chat.client.calls[0]["model"] == "test-model"


def test_history_lists_open_chats_too_not_only_closed_ones(chat):
    """A chat you used and left open (or minimized) is part of the history."""
    kept, _ = run_ask("first question")
    btw.close(kept.id)
    time.sleep(0.01)
    left_open, _ = run_ask("second question")
    rows = btw.all_chats()
    assert [t.id for t in rows] == [left_open.id, kept.id]            # newest activity first
    assert [t.public()["open"] for t in rows] == [True, False]
    assert btw.history() == [kept]                                     # `history()` stays closed-only

    empty = btw.new_thread(chat.tab.id)                                # a window nobody typed in
    assert empty.id not in [t.id for t in btw.all_chats()]


def test_activity_reorders_the_history(chat):
    a, _ = run_ask("alpha")
    time.sleep(0.01)
    b, _ = run_ask("beta")
    assert [t.id for t in btw.all_chats()] == [b.id, a.id]
    time.sleep(0.01)
    chat.client.pieces = ["again"]
    run_ask("alpha follow-up", thread_id=a.id)                         # touching an old chat lifts it
    assert [t.id for t in btw.all_chats()] == [a.id, b.id]


def test_open_chats_survive_a_restart_as_history(chat):
    thread, _ = run_ask("progress?")                                   # never closed
    btw._threads.clear()
    btw._archive.clear()
    btw._loaded = False
    rows = btw.all_chats()
    assert [t.id for t in rows] == [thread.id]
    assert rows[0].pairs == [("progress?", "Working on step 3 of 5.")]
    assert rows[0].public()["open"] is False                           # after a restart it is a saved chat


def test_threads_are_capped(chat):
    for _ in range(btw.MAX_THREADS + 3):
        btw.new_thread(chat.tab.id)
    assert len(btw.threads()) == btw.MAX_THREADS


def test_a_stopped_answer_keeps_what_was_written(chat):
    gate = threading.Event()
    chat.client.pieces = ["The uploader is ", "<hold>", "done"]
    chat.client.hold = gate
    events: list[btw.Event] = []
    thread = btw.ask("progress?", on_event=events.append)
    deadline = time.time() + 3
    while not any(e.op == "delta" for e in events) and time.time() < deadline:
        time.sleep(0.01)
    btw.stop(thread.id)
    gate.set()
    time.sleep(0.1)
    btw.close(thread.id)
    kept = btw.archived(thread.id)
    assert kept.turns() == [{"q": "progress?", "a": "The uploader is ", "status": "stopped"}]


# ── web: POST /api/btw and "/btw …" sent as a prompt ───────────────────────

@pytest.fixture()
def web(chat):
    from jarvis.web.bridge import WebBridge
    from jarvis.web.handler import WebHandler
    from jarvis.web.server import _JarvisHTTPServer

    bridge = WebBridge()
    submitted: list[str] = []
    bridge.set_handlers(on_submit=lambda text, files=None: submitted.append(text))
    handler = type("H", (WebHandler,), {"bridge": bridge, "app": None})
    server = _JarvisHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{server.server_address[1]}"

    def post(path, payload, token=None):
        req = urllib.request.Request(
            base + path,
            data=json.dumps(payload).encode(),
            headers={"Content-Type": "application/json", "Authorization": f"Bearer {token or bridge.token}"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=5) as res:
                return res.status, json.loads(res.read())
        except urllib.error.HTTPError as err:
            return err.code, None

    def get(path):
        req = urllib.request.Request(base + path, headers={"Authorization": f"Bearer {bridge.token}"})
        with urllib.request.urlopen(req, timeout=5) as res:
            return json.loads(res.read())

    def events(op):
        deadline = time.time() + 5
        while time.time() < deadline:
            got = [e["data"] for e in bridge.history() if e["type"] == "btw" and e["data"].get("op") == op]
            if got:
                return got
            time.sleep(0.02)
        return []

    yield SimpleNamespace(post=post, get=get, events=events, submitted=submitted, bridge=bridge)
    server.shutdown()
    server.server_close()


def test_web_ask_streams_btw_events_to_every_page(web):
    status, body = web.post("/api/btw", {"op": "ask", "text": "how long will it take?"})
    assert status == 200 and body["ok"] and body["id"]
    done = web.events("done")
    assert done and done[0]["text"] == "Working on step 3 of 5." and done[0]["id"] == body["id"]
    assert web.events("start")[0]["question"] == "how long will it take?"
    assert [d["text"] for d in web.events("delta")] == ["Working on ", "step 3 of 5."]


def test_web_follow_up_close_and_restore(web):
    _, first = web.post("/api/btw", {"op": "ask", "text": "progress?"})
    web.events("done")
    listing = web.get("/api/btw")
    assert listing["threads"][0]["pairs"] == [{"q": "progress?", "a": "Working on step 3 of 5."}]

    _, again = web.post("/api/btw", {"op": "ask", "text": "and the tests?", "id": first["id"]})
    assert again["id"] == first["id"]
    deadline = time.time() + 5
    while time.time() < deadline and len(web.get("/api/btw")["threads"][0]["pairs"]) < 2:
        time.sleep(0.02)
    status, closed = web.post("/api/btw", {"op": "close", "id": first["id"]})
    assert status == 200 and closed["ok"] is True
    assert closed["saved"]["id"] == first["id"]                 # the chat came back with the close
    assert web.events("closed")[0]["id"] == first["id"]
    left = web.get("/api/btw")
    assert left["threads"] == []                                # no open window
    assert [t["id"] for t in left["history"]] == [first["id"]]   # but it is saved

    _, reopened = web.post("/api/btw", {"op": "reopen", "id": first["id"]})
    assert reopened["ok"] and reopened["id"] == first["id"]
    assert [t["q"] for t in reopened["thread"]["turns"]] == ["progress?", "and the tests?"]
    assert web.get("/api/btw")["threads"][0]["id"] == first["id"]

    assert web.post("/api/btw", {"op": "forget", "id": first["id"]})[1] == {"ok": True}
    assert web.get("/api/btw")["history"] == []
    _, body = web.post("/api/btw", {"op": "ask", "text": "hi", "id": first["id"]})
    assert body["ok"] is False and "closed" in body["error"]


def test_web_history_includes_open_and_closed_chats(web):
    _, closed = web.post("/api/btw", {"op": "ask", "text": "one"})
    web.events("done")
    web.post("/api/btw", {"op": "close", "id": closed["id"]})
    time.sleep(0.01)
    _, live = web.post("/api/btw", {"op": "ask", "text": "two"})
    deadline = time.time() + 5
    while time.time() < deadline and not web.get("/api/btw")["threads"][0]["pairs"]:
        time.sleep(0.02)
    listing = web.get("/api/btw")
    rows = {t["id"]: t for t in listing["history"]}
    assert set(rows) == {closed["id"], live["id"]}
    assert rows[live["id"]]["open"] is True and rows[closed["id"]]["open"] is False
    assert rows[closed["id"]]["turns"][0]["q"] == "one"               # enough to show it without another call


def test_web_errors_and_auth(web):
    assert web.post("/api/btw", {"op": "ask", "text": "  "})[1] == {"ok": False, "error": "type a question after /btw"}
    assert web.post("/api/btw", {"op": "nope"})[1]["ok"] is False
    assert web.post("/api/btw", {"op": "ask", "text": "hi"}, token="wrong")[0] == 401


def test_btw_sent_as_a_prompt_is_answered_aside_not_queued(web):
    status, body = web.post("/api/prompt", {"text": "/btw how far along?"})
    assert status == 200 and body["ok"] and body["btw"] is True
    assert web.events("done")
    assert web.submitted == []                                   # never reached the turn / queue

    status, body = web.post("/api/prompt", {"text": "/btw"})      # bare: just opens the window
    assert status == 200 and body == {"ok": True, "btw": True}
    assert web.submitted == []

    status, _ = web.post("/api/prompt", {"text": "a normal message"})
    assert status == 200 and web.submitted == ["a normal message"]


# ── TUI: /btw while a turn runs ────────────────────────────────────────────

@pytest.fixture()
def hermetic_app(monkeypatch):
    monkeypatch.setenv("HARNESS_SKIP_UPDATE", "1")

    import jarvis.updater as updater
    import jarvis.mcp.registry as mcp_registry
    import jarvis.storage.sessions as sessions
    import jarvis.storage.settings as settings
    import jarvis.tui.prompt_history as prompt_history

    monkeypatch.setattr(updater, "maybe_update_and_reexec", lambda: None)
    monkeypatch.setattr(mcp_registry, "auto_connect_servers", lambda console_print=None, **kw: None,
                        raising=False)
    monkeypatch.setattr(sessions, "db_init", lambda: None)
    monkeypatch.setattr(sessions, "db_create_session", lambda model: None)
    monkeypatch.setattr(settings.Settings, "save", lambda self: None)
    monkeypatch.setattr(prompt_history.PromptHistory, "_save", lambda self: None)

    from jarvis.tui.app import JarvisTUI

    monkeypatch.setattr(JarvisTUI, "_warm_model_catalogs_background", lambda self: None)
    return JarvisTUI


def test_tui_btw_opens_a_window_and_is_never_queued_behind_the_turn(chat, hermetic_app):
    import asyncio

    from jarvis import prompt_queue
    from jarvis.tui.btw_modal import BtwScreen

    async def wait_for(pilot, cond, secs=4.0):
        t0 = time.monotonic()
        while not cond():
            if time.monotonic() - t0 > secs:
                raise AssertionError("timed out waiting")
            await pilot.pause(0.05)

    async def run() -> None:
        app = hermetic_app()
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause(0.3)
            app._busy = True                                   # a turn is running
            prompt = app.query_one("#prompt")
            prompt.insert("/btw how long will it take?")
            await pilot.press("enter")
            await wait_for(pilot, lambda: isinstance(app.screen, BtwScreen))
            screen = app.screen
            assert prompt_queue.items() == []                  # not queued behind the turn
            await wait_for(pilot, lambda: screen._side_turns and screen._side_turns[0]["status"] == "done")
            assert screen._side_turns[0]["a"] == "Working on step 3 of 5."
            thread = screen._side_thread
            assert btw.get(thread).pairs[0][0] == "how long will it take?"

            # A follow-up in the window's own input continues the same side thread.
            chat.client.pieces = ["Tests come last."]
            await pilot.press(*"and the tests?", "enter")
            await wait_for(pilot, lambda: len(screen._side_turns) == 2 and screen._side_turns[1]["status"] == "done")
            assert screen._side_thread == thread
            assert screen._side_turns[1]["a"] == "Tests come last."

            await pilot.press("escape")                        # idle → closes, drops the thread
            await wait_for(pilot, lambda: not isinstance(app.screen, BtwScreen))
            assert btw.get(thread) is None
            assert app._busy is True                           # the turn was never touched

    asyncio.run(run())


# ── side chats belong to the session they were asked in ─────────────────────

@pytest.fixture()
def two_chats(chat, monkeypatch):
    """Two chat tabs — sessions 101 and 202 — each with its own conversation."""
    a = chat.tab
    monkeypatch.setitem(a.session, "current_session_id", 101)
    b = tabs.create(activate=False)
    b.session["current_session_id"] = 202
    b.session["messages"] = [{"role": "user", "content": "Refactor the billing module"}]
    b.config.update(client=chat.client, MODEL="test-model", provider="anthropic", auth_mode="api_key")
    yield SimpleNamespace(a=a, b=b, client=chat.client)
    tabs.close(b)


def ids(rows):
    return [t.id for t in rows]


def test_a_side_chat_is_hidden_in_other_sessions_and_returns_with_its_own(two_chats):
    a, b = two_chats.a, two_chats.b
    in_a, _ = run_ask("how far along?", tab_id=a.id)
    assert in_a.session_id == 101 and in_a.tab_id == a.id

    assert ids(btw.all_chats(tab=a)) == [in_a.id]          # session 101 sees it
    assert ids(btw.open_chats(a)) == [in_a.id]
    assert btw.all_chats(tab=b) == [] and btw.open_chats(b) == []   # session 202 does not
    assert ids(btw.all_chats()) == [in_a.id]               # (unscoped = everything, for tools/tests)

    in_b, _ = run_ask("and here?", tab_id=b.id)
    assert in_b.session_id == 202
    assert ids(btw.all_chats(tab=a)) == [in_a.id]          # each chat sees only its own
    assert ids(btw.all_chats(tab=b)) == [in_b.id]


def test_closed_side_chats_stay_with_their_session(two_chats):
    a, b = two_chats.a, two_chats.b
    thread, _ = run_ask("progress?", tab_id=a.id)
    btw.close(thread.id)
    assert ids(btw.all_chats(tab=a)) == [thread.id] and btw.all_chats(tab=b) == []
    btw.reopen(thread.id)
    assert ids(btw.open_chats(a)) == [thread.id] and btw.open_chats(b) == []


def test_the_answer_is_about_the_chat_the_side_chat_belongs_to(two_chats):
    run_ask("what is it doing?", tab_id=two_chats.b.id)
    system = two_chats.client.calls[-1]["system"]
    assert "Refactor the billing module" in system          # session 202's conversation …
    assert "Add retry logic to the uploader" not in system  # … not the one that is on screen


def test_it_finds_its_chat_even_when_the_tab_number_changed(two_chats):
    a = two_chats.a
    assert btw.scoped_tab(999, 101) is a                    # session wins over a stale tab id
    assert btw.scoped_tab(999, None) is tabs.active()       # nothing known: the one on screen


def test_a_side_chat_asked_before_the_chat_has_a_session_joins_it_later(chat, monkeypatch):
    monkeypatch.setitem(chat.tab.session, "current_session_id", None)
    thread, _ = run_ask("anything yet?", tab_id=chat.tab.id)
    assert thread.session_id is None
    assert btw.belongs(thread, chat.tab.id, None)            # same new chat: still shown
    monkeypatch.setitem(chat.tab.session, "current_session_id", 303)   # the first message made a session
    assert btw.belongs(thread, chat.tab.id, 303)
    assert thread.session_id == 303                          # … and it follows the session from now on
    assert not btw.belongs(thread, chat.tab.id, 404)         # another session in that tab: hidden


def test_a_sessionless_chat_is_not_claimed_by_a_reused_tab_number_after_restart(chat, monkeypatch):
    monkeypatch.setitem(chat.tab.session, "current_session_id", None)
    thread, _ = run_ask("early question", tab_id=chat.tab.id)
    btw.close(thread.id)
    btw._threads.clear()
    btw._archive.clear()
    btw._loaded = False
    monkeypatch.setitem(chat.tab.session, "current_session_id", 505)
    assert btw.all_chats(tab=chat.tab) == []                 # not shown in whatever chat uses tab 1 now
    assert [t.title for t in btw.all_chats()] == ["early question"]   # but it is still on disk


def test_the_session_survives_a_restart(two_chats):
    a, b = two_chats.a, two_chats.b
    thread, _ = run_ask("progress?", tab_id=a.id)
    btw.close(thread.id)
    btw._threads.clear()
    btw._archive.clear()
    btw._loaded = False
    assert ids(btw.all_chats(tab=a)) == [thread.id]          # back in session 101
    assert btw.all_chats(tab=b) == []


def test_web_lists_only_the_chat_it_is_asked_about(two_chats, web):
    a, b = two_chats.a, two_chats.b
    _, first = web.post("/api/btw", {"op": "ask", "text": "in a", "tab": a.id})
    web.events("done")
    start = web.events("start")[0]
    assert start["session"] == 101 and start["tab"] == a.id   # the event says which chat it is for

    mine = web.get(f"/api/btw?tab={a.id}&session=101")
    assert [t["id"] for t in mine["history"]] == [first["id"]] and mine["session"] == 101
    assert [t["id"] for t in mine["threads"]] == [first["id"]]

    other = web.get(f"/api/btw?tab={b.id}&session=202")
    assert other["history"] == [] and other["threads"] == [] and other["session"] == 202

    web.post("/api/btw", {"op": "close", "id": first["id"]})
    assert web.get(f"/api/btw?tab={b.id}&session=202")["history"] == []
    assert [t["id"] for t in web.get(f"/api/btw?tab={a.id}&session=101")["history"]] == [first["id"]]
    assert web.events("closed")[0]["session"] == 101
