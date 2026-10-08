"""Parallel subagents (spawn_agents): runner, team state, cancellation, UIs."""
from __future__ import annotations

import threading
import time
from types import SimpleNamespace

import pytest

from jarvis import state
from jarvis.subagents import runner as runner_mod
from jarvis.subagents import team as team_mod
from jarvis.subagents import tool as tool_mod


# ── a fake model client ──────────────────────────────────────────────────

def _text(t):
    return SimpleNamespace(type="text", text=t)


def _use(tid, name, inp):
    return SimpleNamespace(type="tool_use", id=tid, name=name, input=inp)


def _final(content, stop="end_turn"):
    usage = SimpleNamespace(input_tokens=100, output_tokens=20)
    return SimpleNamespace(content=content, stop_reason=stop, usage=usage)


class _Stream:
    def __init__(self, final, delay, client):
        self._final, self._delay, self._client = final, delay, client
        self.closed = False

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def __iter__(self):
        end = time.monotonic() + self._delay
        while time.monotonic() < end:
            if self.closed:
                raise ConnectionError("stream closed")
            time.sleep(0.01)
        return iter(())

    def get_final_message(self):
        if isinstance(self._final, BaseException):
            raise self._final
        return self._final

    def close(self):
        self.closed = True


class FakeClient:
    """script(brief, n) → final message (or exception) for that agent's n-th request."""

    def __init__(self, script, delay=0.0):
        self.script = script
        self.delay = delay
        self.calls = []
        self.lock = threading.Lock()
        self.messages = self

    def stream(self, **kwargs):
        msgs = kwargs["messages"]
        brief = msgs[0]["content"]
        n = sum(1 for m in msgs if m["role"] == "assistant")
        with self.lock:
            self.calls.append((brief, n, time.monotonic()))
        return _Stream(self.script(brief, n, msgs), self.delay, self)


def _name_of(brief):
    return brief.split('"')[1]


@pytest.fixture
def fake(monkeypatch, tmp_path):
    from jarvis import constants
    from jarvis.constants import set_cwd

    monkeypatch.chdir(tmp_path)
    orig_cwd = constants.CWD
    set_cwd(tmp_path)
    monkeypatch.setattr(state, "provider", "opencode", raising=False)
    monkeypatch.setattr(state, "MODEL", "fake-model", raising=False)
    monkeypatch.setattr(state, "auth_mode", "api_key", raising=False)
    monkeypatch.setattr(state, "think_mode", False, raising=False)
    monkeypatch.setattr(state, "plan_mode", False, raising=False)
    monkeypatch.setattr(runner_mod, "_RETRY_DELAYS", (0.05, 0.05))
    state.cancel_requested.clear()

    def install(script, delay=0.0):
        client = FakeClient(script, delay)
        monkeypatch.setattr(state, "client", client, raising=False)
        return client

    yield install
    set_cwd(orig_cwd)


def _spawn(agents, goal="g"):
    return tool_mod.spawn_agents(agents=agents, goal=goal)


# ── input checks ─────────────────────────────────────────────────────────

def test_rejects_bad_input(fake):
    fake(lambda b, n, m: _final([_text("x")]))
    assert _spawn([]).startswith("ERROR")
    assert "at most 20" in _spawn([{"name": f"a{i}", "task": "t"} for i in range(21)])
    assert "no `task`" in _spawn([{"name": "a"}])


def test_duplicate_names_and_modes(fake):
    runs, err = tool_mod._parse_agents([
        {"name": "Docs", "task": "t"}, {"name": "docs", "task": "t", "mode": "write"},
        "plain string task",
    ])
    assert err is None
    assert [r.name for r in runs] == ["Docs", "docs 2", "Agent 3"]
    assert [r.mode for r in runs] == ["explore", "edit", "explore"]


def test_plan_mode_forces_read_only(fake, monkeypatch):
    monkeypatch.setattr(state, "plan_mode", True)
    runs, _ = tool_mod._parse_agents([{"name": "a", "task": "t", "mode": "edit"}])
    assert runs[0].mode == "explore"
    assert "write_file" not in runner_mod.allowed_tools("edit")


def test_disabled_setting(fake, monkeypatch):
    fake(lambda b, n, m: _final([_text("x")]))
    monkeypatch.setattr(tool_mod, "enabled", lambda: False)
    assert "turned off" in _spawn([{"name": "a", "task": "t"}])


# ── running ──────────────────────────────────────────────────────────────

def test_agents_run_in_parallel_and_report(fake, tmp_path):
    (tmp_path / "a.txt").write_text("alpha\n")

    def script(brief, n, msgs):
        name = _name_of(brief)
        if n == 0:
            return _final([_use(f"{name}-1", "read_file", {"path": str(tmp_path / "a.txt")})], "tool_use")
        # the tool result reached the agent
        assert "alpha" in str(msgs[-1]["content"])
        return _final([_text(f"## Result\n{name} finished")])

    client = fake(script, delay=0.3)
    t0 = time.monotonic()
    out = _spawn([{"name": n, "task": f"do {n}"} for n in ("One", "Two", "Three")])
    took = time.monotonic() - t0
    # 3 agents × 2 requests × 0.3 s would be 1.8 s in sequence
    assert took < 1.3, took
    assert out.startswith("Parallel agents: 3 ran in")
    assert "3 done" in out
    for n in ("One", "Two", "Three"):
        assert f"{n} finished" in out
    assert "### 1. One — done (explore) · 1 tool call ·" in out
    assert len(client.calls) == 6


def test_subagent_tool_rows_stay_off_the_transcript(fake, monkeypatch, tmp_path):
    (tmp_path / "a.txt").write_text("x")
    seen = []
    from jarvis import console as console_mod

    monkeypatch.setattr(console_mod.console, "emit_tool_event",
                        lambda *a: seen.append(a), raising=False)

    def script(brief, n, msgs):
        if n == 0:
            return _final([_use("t1", "read_file", {"path": str(tmp_path / "a.txt")})], "tool_use")
        return _final([_text("done")])

    fake(script)
    _spawn([{"name": "A", "task": "t"}])
    assert seen == []
    team = next(iter(team_mod._teams.values().__reversed__()))
    log = team.agents[0].log
    assert log and log[-1]["status"] == "done"


def test_read_only_agent_cannot_write(fake, tmp_path):
    def script(brief, n, msgs):
        if n == 0:
            return _final([_use("w", "write_file", {"path": str(tmp_path / "x.txt"), "content": "hi"})], "tool_use")
        return _final([_text(str(msgs[-1]["content"]))])

    fake(script)
    out = _spawn([{"name": "Reader", "task": "t"}])
    assert not (tmp_path / "x.txt").exists()
    assert "isn't available to subagents" in out


def test_two_edit_agents_cannot_edit_the_same_file(fake, tmp_path):
    def script(brief, n, msgs):
        name = _name_of(brief)
        if n == 0:
            if name == "B":
                time.sleep(0.2)  # A claims first
            return _final([_use(f"{name}w", "write_file",
                                {"path": str(tmp_path / "shared.txt"), "content": name})], "tool_use")
        return _final([_text(str(msgs[-1]["content"])[:800])])

    fake(script)
    out = _spawn([{"name": "A", "task": "t", "mode": "edit"},
                  {"name": "B", "task": "t", "mode": "edit"}])
    assert (tmp_path / "shared.txt").read_text() == "A"
    assert "is being changed by the parallel agent" in out and "1 tool call" in out
    assert "Files changed: shared.txt (+1 −0)" in out


def test_rate_limit_is_retried(fake):
    class RateLimitError(Exception):
        status_code = 429

    def script(brief, n, msgs):
        if not getattr(script, "failed", False):
            script.failed = True
            return RateLimitError("slow down")
        return _final([_text("ok after retry")])

    fake(script)
    out = _spawn([{"name": "A", "task": "t"}])
    assert "ok after retry" in out and "1 done" in out


def test_one_failing_agent_does_not_sink_the_others(fake):
    def script(brief, n, msgs):
        if _name_of(brief) == "Bad":
            return ValueError("boom")
        return _final([_text("good report")])

    fake(script)
    out = _spawn([{"name": "Good", "task": "t"}, {"name": "Bad", "task": "t"}])
    assert "### 1. Good — done" in out
    assert "### 2. Bad — failed" in out and "ValueError: boom" in out
    assert "didn't finish" in out


def test_max_parallel_queues_the_rest(fake, monkeypatch):
    monkeypatch.setattr(tool_mod, "max_parallel", lambda: 1)
    client = fake(lambda b, n, m: _final([_text("r")]), delay=0.15)
    t0 = time.monotonic()
    out = _spawn([{"name": "A", "task": "t"}, {"name": "B", "task": "t"}])
    assert "2 done" in out
    assert time.monotonic() - t0 >= 0.3
    starts = sorted(t for _, _, t in client.calls)
    assert starts[1] - starts[0] >= 0.14


def test_step_limit_asks_for_a_report(fake, monkeypatch, tmp_path):
    (tmp_path / "a.txt").write_text("x")
    monkeypatch.setattr(tool_mod, "_max_turns", lambda: 4)

    def script(brief, n, msgs):
        last = msgs[-1]["content"]
        if isinstance(last, list) and any(isinstance(b, dict) and "step limit" in str(b.get("text", ""))
                                           for b in last):
            return _final([_text("wrapped up early")])
        return _final([_use(f"t{n}", "read_file", {"path": str(tmp_path / "a.txt")})], "tool_use")

    fake(script)
    out = _spawn([{"name": "Loop", "task": "t"}])
    assert "wrapped up early" in out


# ── stopping ─────────────────────────────────────────────────────────────

def test_cancelling_the_turn_stops_every_agent(fake):
    fake(lambda b, n, m: _final([_text("never")]), delay=5.0)
    me = threading.get_ident()
    threading.Timer(0.3, lambda: state.cancel_thread(me)).start()
    t0 = time.monotonic()
    try:
        out = _spawn([{"name": "A", "task": "t"}, {"name": "B", "task": "t"}])
    finally:
        state.release_thread(me)
    assert time.monotonic() - t0 < 3.0
    assert "### 1. A — cancelled" in out and "### 2. B — cancelled" in out


def test_stopping_one_agent_keeps_the_others(fake):
    def script(brief, n, msgs):
        return _final([_text(f"{_name_of(brief)} report")])

    fake(script, delay=1.0)
    result = {}

    def go():
        result["out"] = _spawn([{"name": "Keep", "task": "t"}, {"name": "Drop", "task": "t"}])

    t = threading.Thread(target=go)
    t.start()
    time.sleep(0.3)
    team = team_mod.running_teams()[-1]
    assert team_mod.stop(team.id, 1)
    t.join(5)
    out = result["out"]
    assert "### 1. Keep — done" in out and "Keep report" in out
    assert "### 2. Drop — stopped" in out and "Stopped by the user" in out


def test_turn_cancelled_follows_parent_links():
    parent, child = 111_111, 222_222
    state.link_thread(child, parent)
    try:
        state.cancel_thread(parent)
        got = {}
        orig = threading.get_ident
        threading.get_ident = lambda: child  # type: ignore[assignment]
        try:
            got["c"] = state.turn_cancelled()
        finally:
            threading.get_ident = orig  # type: ignore[assignment]
        assert got["c"] is True
    finally:
        state.release_thread(parent)
        state.unlink_thread(child)


def test_nested_spawn_is_refused(fake):
    from jarvis.subagents import context as ctx

    ctx.set_current(object(), SimpleNamespace(name="x"))
    try:
        assert "can't start their own" in _spawn([{"name": "a", "task": "t"}])
    finally:
        ctx.clear_current()


# ── board snapshots + UIs ───────────────────────────────────────────────

def test_board_is_rebuilt_from_the_result_text(fake):
    fake(lambda b, n, m: _final([_text(f"report of {_name_of(b)}")]))
    agents = [{"name": "Alpha", "task": "ta"}, {"name": "Beta", "task": "tb", "mode": "edit"}]
    out = _spawn(agents)
    board = tool_mod.board_for("no-such-id", {"agents": agents, "goal": "G"}, out)
    assert board["restored"] and board["goal"] == "G"
    assert [a["name"] for a in board["agents"]] == ["Alpha", "Beta"]
    assert [a["status"] for a in board["agents"]] == ["done", "done"]
    assert board["agents"][1]["mode"] == "edit"
    assert board["agents"][0]["report"] == "report of Alpha"
    assert board["agents"][0]["task"] == "ta"


def test_tui_board_renders_agents():
    from jarvis.tui.agents_block import AgentsBlock, make_tool_block

    blk = make_tool_block("t1", "spawn_agents", {"agents": [{"name": "Auth flow", "task": "x"},
                                                           {"name": "Docs", "task": "y"}]})
    assert isinstance(blk, AgentsBlock)
    now = time.time()
    blk.set_board = AgentsBlock.set_board.__get__(blk)
    blk.board = {"agents": [
        {"i": 0, "name": "Auth flow", "task": "map the auth flow", "status": "running", "activity": "Read client.py",
         "steps": 3, "started": now - 5, "log": [], "files": []},
        {"i": 1, "name": "Docs", "task": "check docs", "status": "done", "activity": "Finished", "steps": 2,
         "started": now - 9, "finished": now - 1, "report": "## Result\nall good",
         "has_report": True, "log": [], "files": [{"path": "a.py", "added": 3, "removed": 1}]},
    ]}
    text = blk.build(100).plain
    assert "Parallel agents" in text and "1 running" in text and "1 done" in text
    assert "Auth flow" in text and "Read client.py" in text and "3 tools" in text
    assert "+3 −1 in 1 file" in text
    blk.expanded = True
    text = blk.build(100).plain
    assert "all good" in text and "brief" in text
    # narrow terminals still render
    assert "Docs" in blk.build(40).plain


def test_web_mux_sends_each_report_once():
    from jarvis.web.console_mux import WebMuxConsole

    sent = []
    bridge = SimpleNamespace(emit=lambda t, d: sent.append((t, d)))
    mux = WebMuxConsole(SimpleNamespace(), bridge)
    board = {"id": "T", "agents": [{"i": 0, "status": "done", "report": "R"},
                                   {"i": 1, "status": "running", "report": ""}]}
    mux.subagents_update("T", board)
    mux.subagents_update("T", board)
    first, second = sent[0][1]["agents"], sent[1][1]["agents"]
    assert sent[0][0] == "agents"
    assert first[0]["report"] == "R" and "report" not in second[0]


def test_settings_validate_numbers():
    from jarvis.storage.settings import _coerce

    assert _coerce("subagents.max_parallel", "3") == 3
    with pytest.raises(ValueError):
        _coerce("subagents.max_parallel", 21)
    assert _coerce("subagents.enabled", "off") is False


def test_team_command_builds_a_prompt():
    from jarvis.commands.dispatch import handle_slash

    result, send, text = handle_slash("/team audit the web handlers")
    assert send and "spawn_agents" in text and "audit the web handlers" in text


# ── asking for agents explicitly · images reach the agents ─────────────

@pytest.mark.parametrize("text, hit", [
    ("implement its faster and using multiple sub-agents", True),
    ("use subagents for this", True),
    ("spin up 4 agents", True),
    ("run agents in parallel", True),
    ("/agent coding", False),
    ("the agent is broken", False),
    ("run the tests in parallel", False),
])
def test_explicit_request_detection(text, hit):
    assert bool(tool_mod.EXPLICIT_RE.search(text)) is hit


def test_explicit_request_adds_a_turn_note(monkeypatch):
    from jarvis.repl import system

    monkeypatch.setattr(state, "messages", [{"role": "user", "content": "build it using multiple sub-agents"}])
    assert "EXPLICITLY ASKED FOR MULTIPLE / PARALLEL SUBAGENTS" in system._subagents_request_block()
    monkeypatch.setattr(state, "messages", [{"role": "user", "content": "build it"}])
    assert system._subagents_request_block() == ""
    # a tool-result message isn't the turn's message: the note stays for the whole turn
    monkeypatch.setattr(state, "messages", [
        {"role": "user", "content": [{"type": "text", "text": "use subagents"}]},
        {"role": "assistant", "content": [{"type": "tool_use", "id": "x", "name": "read_file", "input": {}}]},
        {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "x", "content": "ok"}]},
    ])
    assert system._subagents_request_block() != ""
    monkeypatch.setattr(tool_mod, "enabled", lambda: False)
    import jarvis.subagents as pkg
    monkeypatch.setattr(pkg, "enabled", lambda: False)
    assert system._subagents_request_block() == ""


def test_attached_images_go_to_every_agent(fake, monkeypatch):
    img = {"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": "AAAA"}}
    monkeypatch.setattr(state, "messages", [{"role": "user", "content": [img, {"type": "text", "text": "build this"}]}])
    seen = []

    def script(brief, n, msgs):
        seen.append(msgs[0]["content"])
        return _final([_text("done")])

    client = fake(script)
    client.stream_orig = client.stream

    def stream(**kw):  # the brief is the text block after the image
        first = kw["messages"][0]["content"]
        if isinstance(first, list):
            kw = {**kw, "messages": [{"role": "user", "content": first[-1]["text"]}, *kw["messages"][1:]]}
            seen.append(first)
        return client.stream_orig(**kw)

    client.stream = stream
    out = _spawn([{"name": "A", "task": "t"}, {"name": "B", "task": "t"}])
    assert "2 done" in out
    with_images = [c for c in seen if isinstance(c, list)]
    assert len(with_images) == 2
    assert all(c[0]["type"] == "image" for c in with_images)
    assert "the user attached" in with_images[0][-1]["text"]


def test_streaming_write_label_names_the_file(tmp_path):
    from jarvis.repl.stream import tool_input_label
    from jarvis.subagents.runner import _short
    import os

    home = os.path.expanduser("~")
    label = _short(tool_input_label("write_file", f'{{"path": "{home}/Desktop/x/app.js", "content": "', 4096))
    assert label == "Writing ~/Desktop/x/app.js… 4.0 KB"
