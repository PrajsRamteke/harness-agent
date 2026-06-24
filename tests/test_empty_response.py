"""Regression tests for silent empty-turn handling.

Symptom being fixed: sometimes the agent "just stops" / shows no API response
— the model returns a turn with no text and no tool calls, and the turn loop
breaks without showing anything (no output, no error).

These tests pin the decision logic the loop now uses so an empty turn is
either retried once (transient) or surfaced with a clear message.
"""
from types import SimpleNamespace

from jarvis.repl.render import (
    EMPTY_TURN_MAX_RETRIES,
    classify_empty_turn,
    empty_turn_message,
    response_has_visible_output,
)


def _resp(blocks, stop_reason="end_turn"):
    return SimpleNamespace(content=blocks, stop_reason=stop_reason)


def _text(s):
    return SimpleNamespace(type="text", text=s)


def _tool():
    return SimpleNamespace(type="tool_use", name="read_file", input={}, id="t1")


def _thinking(s="pondering"):
    return SimpleNamespace(type="thinking", thinking=s)


# ── response_has_visible_output ────────────────────────────────────────────

def test_text_response_is_visible():
    assert response_has_visible_output(_resp([_text("hello")])) is True


def test_tool_only_response_is_visible():
    assert response_has_visible_output(_resp([_tool()])) is True


def test_empty_content_is_not_visible():
    assert response_has_visible_output(_resp([])) is False


def test_whitespace_text_is_not_visible():
    assert response_has_visible_output(_resp([_text("   \n\t ")])) is False


def test_thinking_only_is_not_visible():
    # The classic "just stops": model thinks, then end_turn with no answer.
    assert response_has_visible_output(_resp([_thinking()])) is False


def test_none_content_is_not_visible():
    assert response_has_visible_output(_resp(None)) is False


# ── classify_empty_turn ────────────────────────────────────────────────────

def test_productive_turn_proceeds():
    assert classify_empty_turn(_resp([_text("hi")]), 0) is None


def test_empty_end_turn_retries_once_then_stops():
    empty = _resp([], stop_reason="end_turn")
    assert classify_empty_turn(empty, 0) == "retry"
    # After exhausting the retry budget, the loop must stop (and surface).
    assert classify_empty_turn(empty, EMPTY_TURN_MAX_RETRIES) == "stop"


def test_empty_max_tokens_does_not_retry():
    # Re-calling an identical request won't help when it hit the token cap.
    assert classify_empty_turn(_resp([], stop_reason="max_tokens"), 0) == "stop"


def test_thinking_only_retries_then_stops():
    empty = _resp([_thinking()], stop_reason="end_turn")
    assert classify_empty_turn(empty, 0) == "retry"
    assert classify_empty_turn(empty, EMPTY_TURN_MAX_RETRIES) == "stop"


# ── empty_turn_message ─────────────────────────────────────────────────────

def test_message_mentions_stop_reason():
    msg = empty_turn_message(_resp([], stop_reason="end_turn"))
    assert "empty response" in msg.lower()
    assert "end_turn" in msg


def test_max_tokens_message_is_specific():
    msg = empty_turn_message(_resp([], stop_reason="max_tokens"))
    assert "token" in msg.lower()


# ── integration: the real REPL turn loop (jarvis.main._send_and_loop) ───────

class _FakeStatus:
    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


class _FakeConsole:
    """Minimal Rich-Console stand-in capturing printed text."""

    def __init__(self, sink):
        self.sink = sink

    def status(self, *a, **k):
        return _FakeStatus()

    def print(self, msg="", *a, **k):
        self.sink.append(str(msg))


def _isolate_state(monkeypatch):
    from jarvis import state
    monkeypatch.setattr(state, "messages", [])
    monkeypatch.setattr(state, "current_session_id", None)
    monkeypatch.setattr(state, "web_tool_used_this_turn", False)
    state.cancel_requested.clear()
    return state


def test_loop_surfaces_empty_response_after_one_retry(monkeypatch):
    """An all-empty turn must retry once, then show a message — not stop blank."""
    import jarvis.main as m
    state = _isolate_state(monkeypatch)

    calls = {"n": 0}

    def fake_stream():
        calls["n"] += 1
        return _resp([], stop_reason="end_turn")

    printed: list[str] = []
    monkeypatch.setattr(m, "call_claude_stream", fake_stream)
    monkeypatch.setattr(m, "console", _FakeConsole(printed))

    m._send_and_loop("hello")

    assert calls["n"] == 2, "should call once, then retry exactly once"
    assert any("empty response" in p.lower() for p in printed), printed
    # No empty assistant turn should be appended to history.
    assert all(msg.get("role") != "assistant" for msg in state.messages)


def test_loop_proceeds_on_visible_response(monkeypatch):
    """A normal text response proceeds — no retry, no empty message."""
    import jarvis.main as m
    state = _isolate_state(monkeypatch)

    calls = {"n": 0}

    def fake_stream():
        calls["n"] += 1
        return _resp([_text("hi there")], stop_reason="end_turn")

    printed: list[str] = []
    monkeypatch.setattr(m, "call_claude_stream", fake_stream)
    monkeypatch.setattr(m, "render_assistant", lambda resp: False)
    monkeypatch.setattr(m, "console", _FakeConsole(printed))

    m._send_and_loop("hello")

    assert calls["n"] == 1
    assert not any("empty response" in p.lower() for p in printed)
    assert any(msg.get("role") == "assistant" for msg in state.messages)
