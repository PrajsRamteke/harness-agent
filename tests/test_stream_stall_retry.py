"""Tests for auto-retry of a stalled stream (the "No reply yet — API
queue/throttle" symptom).

When the model stream stalls before sending its first token, the request now
retries on a fresh connection within the existing budget instead of either
hanging until the long read timeout or hard-failing. A stall that strikes
mid-reply (after text started) must NOT retry — that would duplicate output.
"""
from types import SimpleNamespace

import httpx
import pytest

import jarvis.repl.stream as stream
from jarvis.console import HarnessAPIError


def _final():
    return SimpleNamespace(
        usage=SimpleNamespace(input_tokens=10, output_tokens=5),
        content=[SimpleNamespace(type="text", text="hi")],
        stop_reason="end_turn",
    )


class _Ctx:
    def __init__(self, behavior):
        self._behavior = behavior

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def get_final_message(self):
        return self._behavior()

    def close(self):
        pass


class _Messages:
    def __init__(self, script):
        self.script = script
        self.calls = 0

    def stream(self, **kwargs):
        behavior = self.script[min(self.calls, len(self.script) - 1)]
        self.calls += 1
        return _Ctx(behavior)


def _timeout():
    raise httpx.ReadTimeout("stalled")


@pytest.fixture
def harness(monkeypatch):
    """Isolate call_claude_stream from the real API and heavy helpers."""
    from jarvis import state

    monkeypatch.setattr(stream, "build_system", lambda: "sys")
    monkeypatch.setattr(stream, "select_tools", lambda msgs: [])
    monkeypatch.setattr(stream, "trim_messages", lambda msgs: msgs)
    monkeypatch.setattr(stream, "_heal_message_history", lambda: None)
    monkeypatch.setattr(stream, "report_turn_phase", lambda *a, **k: None)
    monkeypatch.setattr(stream, "_report_http_timeout", lambda: None)
    monkeypatch.setattr(stream.time, "sleep", lambda *a, **k: None)

    printed: list[str] = []
    monkeypatch.setattr(
        stream, "console", SimpleNamespace(print=lambda m="", *a, **k: printed.append(str(m)))
    )

    monkeypatch.setattr(state, "messages", [{"role": "user", "content": "hi"}])
    monkeypatch.setattr(state, "stream_reply_live", False)
    monkeypatch.setattr(state, "show_internal", False)
    monkeypatch.setattr(state, "think_mode", False)
    monkeypatch.setattr(state, "provider", "anthropic")
    monkeypatch.setattr(state, "MODEL", "test-model")
    state.cancel_requested.clear()
    stream._got_first_delta = False

    def install(script):
        client = SimpleNamespace(messages=_Messages(script))
        monkeypatch.setattr(state, "client", client)
        return client

    return SimpleNamespace(install=install, printed=printed, state=state)


def test_retries_then_succeeds(harness):
    client = harness.install([_timeout, _timeout, lambda: _final()])
    final = stream.call_claude_stream()
    assert final.usage.input_tokens == 10
    assert client.messages.calls == 3  # two stalls + one success


def test_exhausts_budget_then_raises_harness_error(harness):
    client = harness.install([_timeout])  # always stalls
    with pytest.raises(HarnessAPIError):
        stream.call_claude_stream()
    # delays = [1, 3, 6] → 4 attempts total before giving up.
    assert client.messages.calls == 4


def test_stall_retry_can_be_disabled(harness, monkeypatch):
    monkeypatch.setenv("HARNESS_STREAM_STALL_RETRY", "0")
    client = harness.install([_timeout])
    with pytest.raises(HarnessAPIError):
        stream.call_claude_stream()
    assert client.messages.calls == 1  # no retry when disabled


def test_no_retry_after_first_delta(harness):
    """A stall mid-reply must not retry — it would duplicate streamed text."""

    def stalled_after_progress():
        stream._got_first_delta = True  # simulate text already streamed
        raise httpx.ReadTimeout("stalled mid-reply")

    client = harness.install([stalled_after_progress, lambda: _final()])
    with pytest.raises(HarnessAPIError):
        stream.call_claude_stream()
    assert client.messages.calls == 1


def test_cancel_during_stall_does_not_retry(harness):
    def stalled_then_cancel():
        harness.state.cancel_requested.set()
        raise httpx.ReadTimeout("stalled")

    client = harness.install([stalled_then_cancel, lambda: _final()])
    with pytest.raises(httpx.ReadTimeout):
        stream.call_claude_stream()
    assert client.messages.calls == 1
    harness.state.cancel_requested.clear()
