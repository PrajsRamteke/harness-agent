"""Context packs and the model's context window (repl/context_budget.py).

A session that builds several resolve_context packs used to keep every one of
them forever, overflow the model's window and get stuck ("prompt is too
long" on every message after). These tests pin down the fixes: older packs
collapse, oversized requests are fitted, overflow errors are retried, packs
are sized to the model, and the repo scan can't run away.
"""
from __future__ import annotations

from types import SimpleNamespace

import anthropic
import httpx
import pytest

from jarvis import state
from jarvis.repl import context_budget as cb

PACK = cb.PACK_PREFIX + "\nTask: fix login\nRoot files: app/auth.py\n\n--- app/auth.py (RELATION: root_target) ---\n" + "x" * 60_000
PACK2 = cb.PACK_PREFIX + "\nTask: add billing\n\n--- app/billing.py (RELATION: root_target) ---\n" + "y" * 60_000


def _pack_turn(task_text: str, pack: str, tid: str, steps: int = 3, out_len: int = 3000) -> list:
    msgs = [
        {"role": "user", "content": task_text},
        {"role": "assistant", "content": [{"type": "tool_use", "id": tid, "name": "resolve_context", "input": {}}]},
        {"role": "user", "content": [{"type": "tool_result", "tool_use_id": tid, "content": pack}]},
    ]
    for s in range(steps):
        sid = f"{tid}_s{s}"
        msgs.append({"role": "assistant", "content": [{"type": "tool_use", "id": sid, "name": "run_bash", "input": {}}]})
        msgs.append({"role": "user", "content": [{"type": "tool_result", "tool_use_id": sid, "content": "z" * out_len}]})
    msgs.append({"role": "assistant", "content": [{"type": "text", "text": "done"}]})
    return msgs


def _packs_in(msgs) -> int:
    return sum(1 for m in msgs if isinstance(m.get("content"), list)
               for b in m["content"] if cb.is_pack(b))


def _assert_valid_structure(msgs):
    """Starts with a user message; every tool_result answers a tool_use in the
    message right before it."""
    assert msgs[0]["role"] == "user"
    for i, m in enumerate(msgs):
        if not isinstance(m.get("content"), list):
            continue
        results = [b["tool_use_id"] for b in m["content"] if isinstance(b, dict) and b.get("type") == "tool_result"]
        if results:
            prev = msgs[i - 1]
            uses = {b["id"] for b in prev["content"] if isinstance(b, dict) and b.get("type") == "tool_use"}
            assert prev["role"] == "assistant" and set(results) <= uses


# ── layer 1: older packs collapse ────────────────────────────────────────


def test_older_packs_collapse_newest_previous_and_current_stay():
    msgs = (_pack_turn("fix login", PACK, "p1") + _pack_turn("add billing", PACK2, "p2")
            + _pack_turn("now refactor", PACK, "p3"))
    out = cb.collapse_old_packs(msgs)
    assert _packs_in(out) == 2  # p2 (newest before this turn) + p3 (this turn)
    stub = out[2]["content"][0]["content"]
    assert stub.startswith(cb.STUB_PREFIX) and "Task: fix login" in stub and "app/auth.py" in stub
    assert "read_bundle" in stub
    assert msgs[2]["content"][0]["content"] == PACK  # history untouched
    _assert_valid_structure(out)


def test_collapse_is_stable_inside_a_turn():
    """Same prefix on every request of a tool loop (prompt cache / thinking)."""
    msgs = _pack_turn("fix login", PACK, "p1") + _pack_turn("add billing", PACK2, "p2") + [
        {"role": "user", "content": "keep going"},
    ]
    first = cb.collapse_old_packs(msgs)
    msgs += [
        {"role": "assistant", "content": [{"type": "tool_use", "id": "x1", "name": "resolve_context", "input": {}}]},
        {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "x1", "content": PACK}]},
    ]
    second = cb.collapse_old_packs(msgs)
    assert second[: len(first)] == first


def test_five_task_session_no_longer_carries_every_pack():
    from jarvis.repl.trim import trim_messages

    msgs = []
    for t in range(5):
        msgs += _pack_turn(f"task {t}", PACK, f"p{t}", steps=12)
    msgs.append({"role": "user", "content": "next task"})
    before = cb.estimate_tokens(trim_messages(msgs))
    after = cb.estimate_tokens(cb.collapse_old_packs(trim_messages(msgs)))
    assert _packs_in(cb.collapse_old_packs(trim_messages(msgs))) == 1
    assert after < before * 0.4


# ── layer 2: fitting an oversized request ────────────────────────────────


def test_fit_brings_a_huge_session_under_target_and_keeps_it_valid():
    msgs = []
    for t in range(8):
        msgs += _pack_turn(f"task number {t}", PACK, f"p{t}", steps=10, out_len=8000)
    msgs.append({"role": "user", "content": "what about the tests?"})
    limit = 40_000
    assert cb.estimate_tokens(msgs) > limit
    out, steps = cb.fit_to_window(msgs, limit=limit)
    assert cb.estimate_tokens(out) <= limit * cb.TARGET
    assert out[-1] == msgs[-1]  # the user's new message is always sent
    assert steps
    _assert_valid_structure(out)


def test_fit_leaves_out_oldest_turns_with_a_recap():
    msgs = []
    for t in range(6):
        msgs += _pack_turn(f"request {t}: do the thing", "small", f"q{t}", steps=6, out_len=500)
        # make each turn heavy with assistant text the tool stubbing can't shrink
        msgs.append({"role": "user", "content": f"thanks {t}"})
        msgs.append({"role": "assistant", "content": [{"type": "text", "text": "w" * 20_000}]})
    msgs.append({"role": "user", "content": "final question"})
    out, steps = cb.fit_to_window(msgs, limit=30_000)
    assert any("left out the oldest" in s for s in steps)
    first_text = out[0]["content"][0]["text"]
    assert first_text.startswith("[Context note:") and "request 0: do the thing" in first_text
    assert out[-1] == msgs[-1]
    _assert_valid_structure(out)


def test_fit_is_a_no_op_when_there_is_room():
    msgs = _pack_turn("small", "ok", "s1") + [{"role": "user", "content": "hi"}]
    out, steps = cb.fit_to_window(msgs, limit=1_000_000)
    assert out is msgs and steps == []


def test_steer_text_is_never_used_as_a_cut_point():
    steer = {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "a", "content": "r"},
                                          {"type": "text", "text": "also do X"}]}
    assert not cb.is_turn_start(steer)
    assert cb.is_turn_start({"role": "user", "content": "hello"})


# ── layer 3: provider overflow errors ────────────────────────────────────


@pytest.mark.parametrize("text", [
    "prompt is too long: 215000 tokens > 200000 maximum",
    "This model's maximum context length is 128000 tokens",
    "context_length_exceeded: Your input exceeds the context window of this model",
    "input length and `max_tokens` exceed context limit",
])
def test_overflow_errors_are_recognised(text):
    assert cb.is_context_overflow(Exception(text))


def test_other_errors_are_not_overflow():
    assert not cb.is_context_overflow(Exception("max_tokens: must be at most 64000"))
    assert not cb.is_context_overflow(Exception("invalid x-api-key"))
    assert cb.is_context_overflow(SimpleNamespace(status_code=413, __str__=lambda s: "x"))


# ── pack sizing ──────────────────────────────────────────────────────────


def test_packs_are_sized_to_the_model_and_the_room_left(monkeypatch):
    monkeypatch.setattr(cb, "context_window", lambda model: 32_000)
    monkeypatch.setattr(state, "total_in", 0)
    small = cb.pack_char_cap(0, 80_000)
    assert small < 25_000  # 15% of a 32K window, not 80K chars
    monkeypatch.setattr(cb, "context_window", lambda model: 1_000_000)
    assert cb.pack_char_cap(0, 80_000) == 80_000
    assert cb.pack_char_cap(500_000, 80_000) == 500_000  # explicit ask, big window
    monkeypatch.setattr(cb, "context_window", lambda model: 200_000)
    monkeypatch.setattr(state, "total_in", 166_000)  # nearly full
    assert cb.pack_char_cap(0, 80_000) == cb.PACK_MIN_CHARS


# ── the repo scan can't run away ─────────────────────────────────────────


@pytest.fixture
def project(tmp_path):
    from jarvis import constants
    from jarvis.constants import set_cwd
    from jarvis.tools.context import bundle, graph

    orig = constants.CWD
    for i in range(30):
        (tmp_path / f"mod_{i:02d}.py").write_text(f"def f{i}():\n    return {i}\n")
    set_cwd(tmp_path)
    graph._graph = None
    bundle._CONTEXT_ANALYSIS_CACHE.clear()
    yield tmp_path
    graph._graph = None
    bundle._CONTEXT_ANALYSIS_CACHE.clear()
    set_cwd(orig)


def test_scan_stops_at_the_file_limit_and_says_so(project, monkeypatch):
    from jarvis.tools.context import bundle, extract

    monkeypatch.setattr(extract, "SCAN_MAX_FILES", 5)
    out = bundle.resolve_context("change f3 in mod_03")
    assert "repo index is partial" in out and "5 files" in out


def test_esc_stops_indexing(project, monkeypatch):
    from jarvis.tools.context import bundle

    monkeypatch.setattr(state, "turn_cancelled", lambda: True)
    assert bundle.resolve_context("anything") == "ERROR: cancelled while indexing the repo"


def test_home_folder_is_refused(monkeypatch, tmp_path):
    from jarvis.tools.context import bundle, graph

    monkeypatch.setattr(graph, "CWD", tmp_path)
    monkeypatch.setattr("pathlib.Path.home", classmethod(lambda cls: tmp_path))
    out = bundle.resolve_context("fix the bug")
    assert out.startswith("ERROR: resolve_context indexes a project folder")


def test_normal_project_pack_is_unchanged(project):
    from jarvis.tools.context import bundle

    out = bundle.resolve_context("change f3 in mod_03")
    assert out.startswith(cb.PACK_PREFIX) and "repo index is partial" not in out


# ── request loop ─────────────────────────────────────────────────────────


def _bad_request(text):
    def raise_it():
        req = httpx.Request("POST", "https://api.anthropic.com/v1/messages")
        raise anthropic.BadRequestError(text, response=httpx.Response(400, request=req), body=None)
    return raise_it


def _final():
    return SimpleNamespace(usage=SimpleNamespace(input_tokens=10, output_tokens=5),
                           content=[SimpleNamespace(type="text", text="ok")], stop_reason="end_turn")


class _Ctx:
    def __init__(self, behavior):
        self._behavior = behavior

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def get_final_message(self):
        return self._behavior()


@pytest.fixture
def loop(monkeypatch):
    import jarvis.repl.stream as stream

    for name, value in {
        "build_system": lambda: "sys",
        "select_tools": lambda msgs: [],
        "_heal_message_history": lambda: None,
        "report_turn_phase": lambda *a, **k: None,
    }.items():
        monkeypatch.setattr(stream, name, value)
    printed = []
    monkeypatch.setattr(stream.time, "sleep", lambda *a, **k: None)
    monkeypatch.setattr(stream, "console", SimpleNamespace(print=lambda m="", *a, **k: printed.append(str(m))))
    monkeypatch.setattr(stream, "_last_fit_note", ())
    monkeypatch.setattr(state, "stream_reply_live", False)
    monkeypatch.setattr(state, "think_mode", False)
    monkeypatch.setattr(state, "provider", "anthropic")
    monkeypatch.setattr(state, "auth_mode", "api_key")
    monkeypatch.setattr(state, "MODEL", "claude-test")
    monkeypatch.setattr(cb, "context_window", lambda model: 200_000)
    state.cancel_requested.clear()
    sent = []

    def install(script, messages):
        monkeypatch.setattr(state, "messages", messages)

        def stream_fn(**kwargs):
            sent.append(kwargs)
            return _Ctx(script[min(len(sent), len(script)) - 1])

        monkeypatch.setattr(state, "client", SimpleNamespace(messages=SimpleNamespace(stream=stream_fn)))

    return SimpleNamespace(stream=stream, install=install, sent=sent, printed=printed)


def _big_history():
    msgs = []
    for t in range(12):
        msgs += _pack_turn(f"task {t}", PACK, f"p{t}", steps=8, out_len=9000)
    msgs.append({"role": "user", "content": "and now?"})
    return msgs


def test_oversized_request_is_fitted_before_sending(loop, monkeypatch):
    monkeypatch.setattr(cb, "context_window", lambda model: 60_000)
    hist = _big_history()
    loop.install([_final], hist)
    loop.stream.call_claude_stream()
    sent = loop.sent[0]["messages"]
    assert cb.estimate_tokens(sent) <= cb.input_limit() * cb.TARGET
    assert any("Context ~" in p for p in loop.printed)
    assert len(state.messages) == len(hist)  # history kept


def test_too_long_error_is_retried_smaller(loop):
    msgs = _pack_turn("one", PACK, "a", steps=4) + _pack_turn("two", PACK2, "b", steps=4) + [
        {"role": "user", "content": "go"}]
    loop.install([_bad_request("prompt is too long: 250000 tokens > 200000 maximum"), _final], msgs)
    loop.stream.call_claude_stream()
    assert len(loop.sent) == 2
    assert cb.estimate_tokens(loop.sent[1]["messages"]) < cb.estimate_tokens(loop.sent[0]["messages"])
    assert any("context window is full" in p for p in loop.printed)


def test_overflow_retry_gives_up_after_two(loop):
    too_long = _bad_request("prompt is too long")
    loop.install([too_long, too_long, too_long], [{"role": "user", "content": "x"}])
    with pytest.raises(anthropic.BadRequestError):
        loop.stream.call_claude_stream()
    assert len(loop.sent) == 3


# ── Codex stream errors ──────────────────────────────────────────────────


def _codex_stream(events):
    from jarvis.auth.codex_client import _CodexStream

    return _CodexStream(iter(events), "gpt-x")


def test_codex_failed_event_raises_instead_of_empty_reply():
    from jarvis.auth.codex_client import CodexResponseError

    err = SimpleNamespace(code="context_length_exceeded", message="Your input exceeds the context window")
    s = _codex_stream([SimpleNamespace(type="response.failed", response=SimpleNamespace(error=err))])
    with pytest.raises(CodexResponseError) as e:
        list(s.text_stream)
    assert cb.is_context_overflow(e.value)


def test_codex_final_message_reads_an_unconsumed_stream():
    s = _codex_stream([
        SimpleNamespace(type="response.output_text.delta", delta="hel"),
        SimpleNamespace(type="response.output_text.delta", delta="lo"),
        SimpleNamespace(type="response.completed", response=SimpleNamespace(usage=None)),
    ])
    assert s.get_final_message().content[0].text == "hello"


def test_codex_overflow_in_loop_is_retried(loop, monkeypatch):
    from jarvis.auth.codex_client import CodexResponseError

    def overflow():
        raise CodexResponseError("Your input exceeds the context window of this model", "context_length_exceeded")

    msgs = _pack_turn("one", PACK, "a", steps=4) + [{"role": "user", "content": "go"}]
    loop.install([overflow, _final], msgs)
    loop.stream.call_claude_stream()
    assert len(loop.sent) == 2


def test_codex_other_failure_is_reported_not_silent(loop):
    from jarvis.auth.codex_client import CodexResponseError
    from jarvis.console import HarnessAPIError

    def boom():
        raise CodexResponseError("server_error: something broke")

    shown = []
    loop.stream.console.show_error = shown.append
    loop.install([boom], [{"role": "user", "content": "go"}])
    with pytest.raises(HarnessAPIError):
        loop.stream.call_claude_stream()
    # A Codex server error is retried (nothing streamed yet), then reported.
    assert len(loop.sent) == 4
    assert [r.kind for r in shown] == ["server"]
    assert "something broke" in shown[0].detail
    assert shown[0].attempts == 3


def test_codex_unknown_failure_keeps_codex_title(loop):
    from jarvis.auth.codex_client import CodexResponseError
    from jarvis.console import HarnessAPIError

    def boom():
        raise CodexResponseError("the model produced invalid output", "invalid_output")

    shown = []
    loop.stream.console.show_error = shown.append
    loop.install([boom], [{"role": "user", "content": "go"}])
    with pytest.raises(HarnessAPIError):
        loop.stream.call_claude_stream()
    assert len(loop.sent) == 1
    assert shown[0].title == "Codex ended the reply with an error"


# ── long tool calls show progress, never "No reply yet" ──────────────────


def test_tool_input_labels():
    from jarvis.repl.stream import tool_input_label

    assert tool_input_label("write_file", '{"path": "site/index.html", "content": "<ht', 13_000) == \
        "Writing site/index.html… 12.7 KB"
    assert tool_input_label("edit_file", '{"path": "app.py"', 300) == "Preparing edit to app.py… 300 chars"
    assert tool_input_label("run_bash", '{"cmd": "pytest -q tests/"}', 40).startswith("Writing command: pytest -q")
    assert tool_input_label("", "", 0) == "Preparing tool call… 0 chars"


def test_streaming_tool_arguments_report_progress(monkeypatch):
    import jarvis.repl.stream as stream

    phases = []
    monkeypatch.setattr(stream, "report_turn_phase", phases.append)
    monkeypatch.setattr(stream, "console", SimpleNamespace(print=lambda *a, **k: None))
    monkeypatch.setattr(state, "think_mode", False)

    class FakeStream:
        def __iter__(self):
            yield SimpleNamespace(type="content_block_start", index=0,
                                  content_block=SimpleNamespace(type="tool_use", name="write_file"))
            yield SimpleNamespace(type="input_json", partial_json='{"path": "big.html", "content": "')
            for _ in range(3):
                yield SimpleNamespace(type="input_json", partial_json="x" * 4000)

        def get_final_message(self):
            return None

    stream._consume_live_text_stream(FakeStream(), "jarvis")
    assert phases[0] == "Writing a file… 0 chars"           # name known, path not yet
    assert phases[1].startswith("Writing big.html… ")       # path shown as soon as it arrives
    assert not any("No reply yet" in p for p in phases)


def test_openai_style_stream_yields_tool_progress():
    from jarvis.auth.opencode_client import _OpenCodeStream

    s = _OpenCodeStream.__new__(_OpenCodeStream)
    s._collected_tool_calls, s._collected_text, s._collected_reasoning = {}, [], []
    s._usage_obj, s._tool_deltas = None, []
    fn = SimpleNamespace(name="write_file", arguments='{"path": "a.txt"')
    chunk = SimpleNamespace(usage=None, choices=[SimpleNamespace(delta=SimpleNamespace(
        content=None, tool_calls=[SimpleNamespace(index=0, id="c1", function=fn)]))])
    s._process_chunk(chunk)
    assert s._tool_deltas == [(0, "write_file", '{"path": "a.txt"')]


def test_codex_stream_yields_tool_progress():
    s = _codex_stream([
        SimpleNamespace(type="response.output_item.added",
                        item=SimpleNamespace(type="function_call", id="fc1", name="write_file")),
        SimpleNamespace(type="response.function_call_arguments.delta", item_id="fc1", delta='{"path":"x"'),
    ])
    assert list(s.delta_stream) == [("tool_input", ("fc1", "write_file", "")),
                                    ("tool_input", ("fc1", "write_file", '{"path":"x"'))]


# ── half-arrived tool calls never run ────────────────────────────────────


def test_tool_call_cut_off_by_output_limit_is_not_run(tmp_path, monkeypatch):
    from jarvis.repl import render
    from jarvis.repl.stream import check_tool_inputs

    for name in ("emit_tool_start", "emit_tool_done", "report_turn_phase"):
        monkeypatch.setattr(render, name, lambda *a, **k: None)
    target = tmp_path / "cut.html"
    block = SimpleNamespace(type="tool_use", id="w1", name="write_file",
                            input={"path": str(target), "content": "<html><body>half"})
    check_tool_inputs(SimpleNamespace(stop_reason="max_tokens", content=[block]))
    _, _, _, out = render._run_tool(block)
    assert out.startswith("ERROR: this tool call was cut off") and not target.exists()


def test_invalid_streamed_json_is_not_run(monkeypatch):
    from jarvis.repl import render
    from jarvis.repl.stream import check_tool_inputs

    for name in ("emit_tool_start", "emit_tool_done", "report_turn_phase"):
        monkeypatch.setattr(render, name, lambda *a, **k: None)
    good = SimpleNamespace(type="tool_use", id="g1", name="list_dir", input={"path": "."})
    setattr(good, "__json_buf", b'{"path": "."}')
    bad = SimpleNamespace(type="tool_use", id="b1", name="list_dir", input={"path": "."})
    setattr(bad, "__json_buf", b'{"path": ".", "show_all": tr')
    check_tool_inputs(SimpleNamespace(stop_reason="tool_use", content=[good, bad]))
    assert render._UNUSABLE_TOOL_CALLS.get("b1", "").startswith("the arguments for this tool call arrived")
    assert "g1" not in render._UNUSABLE_TOOL_CALLS
    render._UNUSABLE_TOOL_CALLS.clear()


def test_eager_tool_streaming_only_on_anthropic_direct(monkeypatch):
    import jarvis.repl.stream as stream

    monkeypatch.setattr(stream, "_eager_tools_off", False)
    monkeypatch.setattr(state, "client", anthropic.Anthropic(api_key="x"))
    monkeypatch.setattr(state, "provider", "anthropic")
    assert stream._eager_tool_streaming_ok()
    monkeypatch.setattr(state, "provider", "openrouter")
    assert not stream._eager_tool_streaming_ok()
    monkeypatch.setattr(state, "provider", "anthropic")
    monkeypatch.setenv("HARNESS_EAGER_TOOLS", "0")
    assert not stream._eager_tool_streaming_ok()


def test_refused_eager_field_is_dropped_and_retried(loop, monkeypatch):
    import jarvis.repl.stream as stream

    monkeypatch.setattr(stream, "_eager_tools_off", False)
    monkeypatch.setattr(stream, "select_tools", lambda msgs: [{"name": "t", "input_schema": {"type": "object"}}])
    monkeypatch.setattr(stream, "_eager_tool_streaming_ok", lambda: not stream._eager_tools_off)
    loop.install([_bad_request("tools.0.eager_input_streaming: Extra inputs are not permitted"), _final],
                 [{"role": "user", "content": "hi"}])
    loop.stream.call_claude_stream()
    assert loop.sent[0]["tools"][0].get("eager_input_streaming") is True
    assert "eager_input_streaming" not in loop.sent[1]["tools"][0]
