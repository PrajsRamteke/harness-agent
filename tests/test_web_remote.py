"""Web remote: transcript snapshot, tool rows, diffs, answers, live sync, assets."""
from __future__ import annotations

import json
import queue
import re
from pathlib import Path

import pytest

from jarvis import state
from jarvis.web import handler as web_handler
from jarvis.web import state_api
from jarvis.web.bridge import WebBridge
from jarvis.web.console_mux import WebMuxConsole
from jarvis.web.sync import StateWatcher

STATIC = Path(web_handler.__file__).with_name("static")


class _Primary:
    """Stand-in for TUIConsole: records calls, renders nothing."""

    def __init__(self) -> None:
        self.tool_events: list[tuple[str, dict]] = []

    def emit_tool_event(self, event_type, data):
        self.tool_events.append((event_type, data))

    def file_diff(self, *a, **k):
        pass


def _drain(sub: queue.Queue) -> list[dict]:
    out = []
    while True:
        try:
            out.append(json.loads(sub.get_nowait()))
        except queue.Empty:
            return out


@pytest.fixture
def session_state(monkeypatch):
    monkeypatch.setattr(state, "messages", [])
    monkeypatch.setattr(state, "show_internal", True)
    monkeypatch.setattr(state, "current_session_id", None)
    monkeypatch.setattr(state, "MODEL", "claude-sonnet-5")
    return state


# ─── Snapshot transcript ────────────────────────────────────────────────


def test_snapshot_keeps_block_order_and_pairs_tool_results(session_state):
    session_state.messages = [
        {"role": "user", "content": "fix it"},
        {"role": "assistant", "content": [
            {"type": "thinking", "thinking": "look first"},
            {"type": "text", "text": "Reading."},
            {"type": "tool_use", "id": "t1", "name": "read_file", "input": {"path": "a.py"}},
            {"type": "tool_use", "id": "t2", "name": "run_bash", "input": {"cmd": "false"}},
            {"type": "tool_use", "id": "t3", "name": "run_bash", "input": {"cmd": "sleep 9"}},
        ]},
        {"role": "user", "content": [
            {"type": "tool_result", "tool_use_id": "t1", "content": "print(1)"},
            {"type": "tool_result", "tool_use_id": "t2", "content": "ERROR: exit 1", "is_error": True},
        ]},
        {"role": "assistant", "content": [{"type": "text", "text": "Done."}]},
    ]
    msgs = state_api.snapshot_messages()

    assert [m["role"] for m in msgs] == ["you", "thinking", "assistant", "tool", "tool", "tool", "assistant"]
    read, failed, unfinished = msgs[3], msgs[4], msgs[5]
    assert (read["title"], read["args"], read["status"]) == ("Read", "a.py", "done")
    assert failed["status"] == "error" and failed["args"] == "false"
    assert unfinished["status"] == "pending" and unfinished["summary"] == ""
    # tool_result-only user turns are not shown as user bubbles
    assert sum(1 for m in msgs if m["role"] == "you") == 1


def test_snapshot_hides_thinking_without_trace(session_state):
    session_state.show_internal = False
    session_state.messages = [
        {"role": "assistant", "content": [
            {"type": "thinking", "thinking": "secret"},
            {"type": "text", "text": "Hi"},
        ]},
    ]
    assert [m["role"] for m in state_api.snapshot_messages()] == ["assistant"]


# ─── Live tool rows + diffs ─────────────────────────────────────────────


def test_tool_events_reach_web_with_readable_fields():
    bridge = WebBridge()
    sub = bridge.subscribe()
    primary = _Primary()
    mux = WebMuxConsole(primary, bridge)

    mux.emit_tool_event("tool_start", {"id": "x", "name": "read_file", "label": "read_file",
                                       "input": {"path": "src/app.py"}})
    mux.emit_tool_event("tool_done", {"id": "x", "name": "read_file", "label": "read_file",
                                      "input": {"path": "src/app.py"}, "output": "ERROR: no such file",
                                      "error": False})
    start, done = _drain(sub)

    assert start["type"] == "tool_start"
    assert start["data"]["title"] == "Read" and start["data"]["args"] == "src/app.py"
    assert "input" not in start["data"] and "output" not in start["data"]
    # An ERROR: result is a failure even when the executor didn't flag it
    assert done["data"]["error"] is True
    assert done["data"]["summary"] == "no such file"
    assert "summary_error" not in done["data"]
    # The TUI still gets the full payload
    assert primary.tool_events[1][1]["output"] == "ERROR: no such file"


def test_file_diff_is_a_structured_event():
    bridge = WebBridge()
    sub = bridge.subscribe()
    mux = WebMuxConsole(_Primary(), bridge)

    mux.file_diff("notes.txt", "a\nb\n", "a\nc\nd\n", "edit")
    (evt,) = _drain(sub)

    assert evt["type"] == "diff"
    assert evt["data"]["added"] == 2 and evt["data"]["removed"] == 1
    assert "+c" in evt["data"]["lines"] and "-b" in evt["data"]["lines"]


# ─── Answers from the web ───────────────────────────────────────────────


def test_web_answers_use_selected_ids_like_the_tui():
    out = web_handler._normalize_answers({"answers": [
        {"question_id": "q1", "selected": ["approve"]},
        {"question_id": "q2", "selected_ids": ["b"], "labels": ["B"]},
        "junk",
    ]})
    assert out["answers"] == [
        {"question_id": "q1", "selected_ids": ["approve"], "labels": []},
        {"question_id": "q2", "selected_ids": ["b"], "labels": ["B"]},
    ]


def test_token_check_tolerates_non_ascii():
    h = web_handler.WebHandler.__new__(web_handler.WebHandler)
    h.bridge = WebBridge()
    assert h._token_ok(h.bridge.token)
    assert not h._token_ok("tökén")


# ─── Live sync from the terminal ────────────────────────────────────────


def test_state_watcher_pushes_terminal_changes(session_state, monkeypatch):
    monkeypatch.setattr(state_api, "_session_title", lambda sid: f"title {sid}")
    session_state.current_session_id = 1
    bridge = WebBridge()
    watcher = StateWatcher(bridge, busy=lambda: False)

    assert watcher.tick() is None  # nobody connected
    sub = bridge.subscribe()
    assert watcher.tick() is None  # first look is the baseline
    assert watcher.tick() is None  # nothing changed

    session_state.MODEL = "gpt-5.5-codex"  # e.g. /model in the terminal
    assert watcher.tick() == "state"
    (evt,) = _drain(sub)
    assert evt["data"]["model"] == "gpt-5.5-codex" and "messages" not in evt["data"]

    session_state.current_session_id = 2  # /new or resume in the terminal
    assert watcher.tick() == "snapshot"
    (evt,) = _drain(sub)
    assert evt["type"] == "snapshot" and "messages" in evt["data"]
    assert evt["data"]["session_title"] == "title 2"


def test_state_watcher_replays_when_conversation_shrinks(session_state):
    session_state.messages = [{"role": "user", "content": "a"}, {"role": "assistant", "content": "b"}]
    bridge = WebBridge()
    bridge.subscribe()
    watcher = StateWatcher(bridge, busy=lambda: False)
    watcher.tick()
    session_state.messages = []  # /reset
    assert watcher.tick() == "snapshot"


def test_stalled_client_is_told_to_resync():
    bridge = WebBridge()
    sub = bridge.subscribe()
    for i in range(sub.maxsize + 5):
        bridge.emit("stream_delta", {"chunk": str(i)})
    events = _drain(sub)
    assert events[0]["type"] == "resync"
    assert len(events) < sub.maxsize


# ─── Static assets ──────────────────────────────────────────────────────


def test_index_only_references_existing_assets():
    html = (STATIC / "index.html").read_text()
    refs = re.findall(r'(?:href|src)="/static/([^"]+)"', html)
    assert refs, "index.html should load local assets"
    for rel in refs:
        assert (STATIC / rel).is_file(), rel


def test_js_modules_import_existing_files_and_names():
    js = STATIC / "js"
    exports: dict[str, set[str]] = {}
    for f in js.glob("*.js"):
        src = f.read_text()
        names = set(re.findall(r"export (?:async )?(?:function|const|let) ([\w$]+)", src))
        for group in re.findall(r"export \{([^}]+)\}", src):
            names |= {n.strip().split(" as ")[-1] for n in group.split(",") if n.strip()}
        exports[f.name] = names
    for f in js.glob("*.js"):
        for names, mod in re.findall(r"import \{([^}]+)\} from '\./([\w.]+)'", f.read_text()):
            assert mod in exports, f"{f.name} imports missing {mod}"
            for name in (n.strip() for n in names.split(",") if n.strip()):
                assert name in exports[mod], f"{f.name} imports {name} not exported by {mod}"


def test_every_icon_used_is_bundled():
    src = (STATIC / "js" / "icons.js").read_text()
    bundled = set(json.loads(re.search(r"const ICONS = (\{.*\});", src).group(1)))
    used: set[str] = set()
    for f in (STATIC / "js").glob("*.js"):
        if f.name == "icons.js":
            continue
        text = f.read_text()
        used |= set(re.findall(r"icon\('([a-z0-9-]+)'\)", text))
        used |= set(re.findall(r"icon: '([a-z0-9-]+)'", text))
    used |= set(re.findall(r'data-icon="([a-z0-9-]+)"', (STATIC / "index.html").read_text()))
    assert used - bundled == set()


def test_thinking_reaches_web_once(monkeypatch):
    """finalize + commit both carry the same thought — the web gets it once."""
    monkeypatch.setattr(state, "show_internal", True)
    bridge = WebBridge()
    sub = bridge.subscribe()

    class _P(_Primary):
        def __getattr__(self, name):
            return lambda *a, **k: None

    mux = WebMuxConsole(_P(), bridge)
    mux.thinking_stream_start()
    mux.thinking_stream_push("plan the fix")
    mux.thinking_stream_finalize()
    mux.assistant_stream_start("jarvis")
    mux.assistant_stream_push("Done.")
    mux.assistant_stream_commit("Done.", "jarvis", False, ["plan the fix"])

    thoughts = [e for e in _drain(sub) if e["type"] == "message" and e["data"]["role"] == "thinking"]
    assert [t["data"]["text"] for t in thoughts] == ["plan the fix"]


def test_server_accepts_a_burst_of_module_requests():
    """The page fetches ~20 ES modules at once; a backlog of 5 (socketserver's
    default) made macOS reset the rest and the app never booted."""
    from jarvis.web.server import _JarvisHTTPServer

    assert _JarvisHTTPServer.request_queue_size >= 64
    modules = len(list((STATIC / "js").glob("*.js")))
    assert _JarvisHTTPServer.request_queue_size > modules + 4


def test_server_accepts_a_burst_of_module_requests():
    """The page fetches ~20 ES modules at once; socketserver's default backlog
    of 5 made macOS reset the rest (ERR_CONNECTION_RESET) and the app never booted."""
    from jarvis.web.server import _JarvisHTTPServer

    modules = len(list((STATIC / "js").glob("*.js")))
    assert _JarvisHTTPServer.request_queue_size > modules + 4
