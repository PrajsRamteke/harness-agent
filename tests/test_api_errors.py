"""Failed model requests become one readable error card (repl/api_errors.py).

Covers: classifying every kind of failure (sleep/wake disconnects, timeouts,
rate / usage limits, auth, credits, retired models, provider 5xx …), the
stream reconnecting by itself after a dropped connection, the TUI's
``ErrorBlock`` with clickable fixes, ``/retry`` resuming without a duplicate
user message, and the web remote's ``error`` event + snapshot field.
"""
from __future__ import annotations

import asyncio
import threading
import time
from types import SimpleNamespace

import anthropic
import httpx
import pytest

from jarvis import state
from jarvis.repl import api_errors

_REQ = httpx.Request("POST", "https://api.example.test/v1/messages")


def _status(code: int, body=None, headers=None, cls=anthropic.APIStatusError):
    resp = httpx.Response(code, request=_REQ, headers=headers or {})
    return cls(f"Error code: {code} - {body}", response=resp, body=body)


@pytest.fixture
def session(monkeypatch):
    monkeypatch.setattr(state, "provider", "openai_codex")
    monkeypatch.setattr(state, "MODEL", "gpt-test")
    monkeypatch.setattr(state, "auth_mode", "oauth")
    monkeypatch.setattr(state, "messages", [{"role": "user", "content": "hi"}])
    monkeypatch.setattr(state, "current_session_id", None)
    monkeypatch.setattr(state, "last_api_error", None)


# ─── classifying ─────────────────────────────────────────────────────────

@pytest.mark.parametrize("exc", [
    anthropic.APIConnectionError(request=_REQ),
    httpx.RemoteProtocolError("peer closed connection without sending complete message body"),
    httpx.ConnectError("[Errno 8] nodename nor servname provided, or not known"),
    httpx.ReadError("[Errno 54] Connection reset by peer"),
    ConnectionResetError(54, "Connection reset by peer"),
])
def test_dropped_connections_read_as_network(session, exc):
    rep = api_errors.classify(exc)
    assert rep.kind == "network"
    assert rep.title == "Connection lost"
    assert "sleeps" in rep.message
    assert rep.severity == "warn" and rep.transient
    assert rep.actions[0].command == "/retry" and rep.actions[0].primary


def test_wrapped_network_error_is_found_in_the_cause_chain(session):
    try:
        try:
            raise httpx.ConnectError("Network is unreachable")
        except httpx.ConnectError as inner:
            raise RuntimeError("stream reader failed") from inner
    except RuntimeError as outer:
        assert api_errors.kind_of(outer) == "network"


def test_timeouts(session):
    assert api_errors.classify(httpx.ReadTimeout("read timed out")).kind == "timeout"
    assert api_errors.classify(TimeoutError("Stream stalled for 240s (limit 240s)")).kind == "timeout"


def test_rate_limit_with_retry_after(session):
    exc = _status(429, {"error": {"type": "rate_limit_error", "message": "Slow down"}},
                  headers={"retry-after": "42"}, cls=anthropic.RateLimitError)
    rep = api_errors.classify(exc)
    assert rep.kind == "rate_limit" and rep.retry_after == 42
    assert "42s" in rep.message
    assert rep.status_label() == "429 Too many requests"


def test_usage_limit_is_quota_not_rate_limit(session):
    exc = _status(429, {"error": {"code": "usage_limit_reached",
                                  "message": "The usage limit has been reached"}},
                  headers={"retry-after": "7200"})
    rep = api_errors.classify(exc)
    assert rep.kind == "quota" and rep.title == "Usage limit reached"
    assert "2 h" in rep.message
    assert {a.command for a in rep.actions} >= {"/model", "/provider"}


def test_expired_oauth_offers_sign_in(session):
    rep = api_errors.classify(_status(401, {"error": {"message": "token expired"}}))
    assert rep.kind == "auth" and rep.title == "Signed out"
    assert rep.actions[0].command == "/login"


def test_rejected_key_offers_key(session, monkeypatch):
    monkeypatch.setattr(state, "provider", "openrouter")
    monkeypatch.setattr(state, "auth_mode", "api_key")
    rep = api_errors.classify(_status(401, {"error": {"message": "No auth credentials found"}}))
    assert rep.title == "API key rejected" and rep.actions[0].command == "/key"


def test_openrouter_out_of_credits_links_to_top_up(session, monkeypatch):
    monkeypatch.setattr(state, "provider", "openrouter")
    rep = api_errors.classify(_status(402, {"error": {"message": "Insufficient credits"}}))
    assert rep.kind == "payment"
    assert any(a.command.startswith("https://openrouter.ai") for a in rep.actions)


@pytest.mark.parametrize("code,kind", [
    (529, "overloaded"), (500, "server"), (503, "server"), (404, "model"), (403, "permission"),
])
def test_status_codes(session, code, kind):
    assert api_errors.classify(_status(code, {"error": {"message": "nope"}})).kind == kind


def test_context_overflow(session):
    exc = _status(400, {"error": {"message": "prompt is too long: 250000 tokens > 200000 maximum"}})
    rep = api_errors.classify(exc)
    assert rep.kind == "context" and rep.actions[0].command == "/new"


def test_bad_request_quotes_the_provider(session):
    exc = _status(400, {"error": {"type": "invalid_request_error", "message": "tools.0.name: bad"}})
    rep = api_errors.classify(exc)
    assert rep.kind == "request"
    assert "tools.0.name: bad" in rep.message
    assert rep.detail.startswith("APIStatusError:")  # SDK prefix dropped, type kept


def test_openrouter_nested_raw_message():
    body = {"error": {"message": "Provider returned error", "code": 429,
                      "metadata": {"raw": '{"error": {"message": "free tier exhausted"}}'}}}
    msg, code, _ = api_errors.provider_message(_status(429, body))
    assert "free tier exhausted" in msg and code == "429"


def test_python_repr_body_in_message_text():
    exc = Exception("Error code: 400 - {'error': {'message': 'model is not supported', 'code': 'x'}}")
    assert api_errors.provider_message(exc)[:2] == ("model is not supported", "x")


def test_detail_is_capped():
    assert len(api_errors.clean_detail("x" * 10_000)) <= api_errors.DETAIL_MAX + 2


def test_rich_panel_fallback_renders(session):
    from rich.console import Console

    con = Console(width=80, record=True, file=open("/dev/null", "w"))
    con.print(api_errors.rich_panel(api_errors.classify(anthropic.APIConnectionError(request=_REQ))))
    out = con.export_text()
    assert "Connection lost" in out and "/retry" in out


# ─── remembering the last one, /retry ────────────────────────────────────

def test_current_error_lasts_until_the_chat_moves_on(session):
    shown = []
    api_errors.show(api_errors.report("network"), console=SimpleNamespace(show_error=shown.append))
    assert shown and api_errors.current_error()["kind"] == "network"
    state.messages.append({"role": "assistant", "content": "ok"})
    assert api_errors.current_error() is None


def test_can_retry(session):
    assert api_errors.can_retry()[0]
    state.messages.append({"role": "assistant", "content": "done"})
    ok, why = api_errors.can_retry()
    assert not ok and "finished" in why
    state.messages.clear()
    assert not api_errors.can_retry()[0]


# ─── the stream reconnects by itself ─────────────────────────────────────

def _final():
    return SimpleNamespace(usage=SimpleNamespace(input_tokens=10, output_tokens=5),
                           content=[SimpleNamespace(type="text", text="hi")], stop_reason="end_turn")


class _Ctx:
    def __init__(self, behavior):
        self._b = behavior

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def get_final_message(self):
        return self._b()

    def close(self):
        pass


@pytest.fixture
def stream_env(monkeypatch, session):
    import jarvis.repl.stream as stream

    monkeypatch.setattr(stream, "build_system", lambda: "sys")
    monkeypatch.setattr(stream, "select_tools", lambda msgs: [])
    monkeypatch.setattr(stream, "trim_messages", lambda msgs: msgs)
    monkeypatch.setattr(stream, "_heal_message_history", lambda: None)
    monkeypatch.setattr(stream, "report_turn_phase", lambda *a, **k: None)
    monkeypatch.setattr(api_errors, "report_turn_phase", lambda *a, **k: None, raising=False)
    slept = []
    monkeypatch.setattr(stream.time, "sleep", lambda s: slept.append(s))
    printed, shown = [], []
    monkeypatch.setattr(stream, "console", SimpleNamespace(
        print=lambda m="", *a, **k: printed.append(str(m)), show_error=shown.append))
    monkeypatch.setattr(state, "provider", "anthropic")
    monkeypatch.setattr(state, "auth_mode", "api_key")
    monkeypatch.setattr(state, "stream_reply_live", False)
    monkeypatch.setattr(state, "think_mode", False)
    state.cancel_requested.clear()
    stream._got_first_delta = False

    def install(script):
        calls = []

        def stream_fn(**kw):
            calls.append(kw)
            return _Ctx(script[min(len(calls), len(script)) - 1])

        monkeypatch.setattr(state, "client", SimpleNamespace(messages=SimpleNamespace(stream=stream_fn)))
        return calls

    return SimpleNamespace(stream=stream, install=install, printed=printed, shown=shown, slept=slept)


def _drop():
    raise anthropic.APIConnectionError(request=_REQ)


def test_reconnects_after_a_dropped_connection(stream_env):
    calls = stream_env.install([_drop, _drop, _final])
    final = stream_env.stream.call_claude_stream()
    assert final.stop_reason == "end_turn" and len(calls) == 3
    assert not stream_env.shown
    assert sum("reconnecting" in p for p in stream_env.printed) == 2
    assert sum(stream_env.slept) == pytest.approx(2 + 5)  # Wi-Fi gets time to come back


def test_gives_up_with_a_network_card(stream_env):
    from jarvis.console import HarnessAPIError

    calls = stream_env.install([_drop])
    with pytest.raises(HarnessAPIError):
        stream_env.stream.call_claude_stream()
    assert len(calls) == 4
    (rep,) = stream_env.shown
    assert rep.kind == "network" and rep.attempts == 3
    assert "tried 4 times" in rep.message


def test_no_reconnect_once_text_streamed(stream_env):
    from jarvis.console import HarnessAPIError

    def drop_mid_reply():
        stream_env.stream._got_first_delta = True
        _drop()

    calls = stream_env.install([drop_mid_reply, _final])
    with pytest.raises(HarnessAPIError):
        stream_env.stream.call_claude_stream()
    assert len(calls) == 1
    assert "middle of the reply" in stream_env.shown[0].message


def test_server_error_retries_then_reports_attempts(stream_env):
    from jarvis.console import HarnessAPIError

    def boom():
        raise _status(500, {"error": {"type": "api_error", "message": "Internal server error"}})

    calls = stream_env.install([boom])
    with pytest.raises(HarnessAPIError):
        stream_env.stream.call_claude_stream()
    assert len(calls) == 4
    assert stream_env.shown[0].kind == "server" and stream_env.shown[0].attempts == 3


def test_rate_limit_stops_with_a_card(stream_env):
    def limited():
        raise _status(429, {"error": {"message": "slow down"}}, cls=anthropic.RateLimitError)

    stream_env.install([limited])
    with pytest.raises(anthropic.RateLimitError):
        stream_env.stream.call_claude_stream()
    assert stream_env.shown[0].kind == "rate_limit"


def test_esc_stops_the_wait_between_retries(stream_env, monkeypatch):
    stream_env.install([_drop])
    monkeypatch.setattr(stream_env.stream.time, "sleep", lambda s: state.cancel_requested.set())
    monkeypatch.setattr(state, "turn_cancelled", lambda: state.cancel_requested.is_set())
    with pytest.raises(KeyboardInterrupt):
        stream_env.stream.call_claude_stream()
    state.cancel_requested.clear()


# ─── TUI: the card, its buttons, /retry ──────────────────────────────────

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
    monkeypatch.setattr(state, "last_api_error", None)
    from jarvis.tui.app import JarvisTUI

    monkeypatch.setattr(JarvisTUI, "_warm_model_catalogs_background", lambda self: None)
    return JarvisTUI


async def _until(pilot, cond, secs=8.0):
    deadline = time.monotonic() + secs
    while not cond() and time.monotonic() < deadline:
        await pilot.pause(0.05)
    return cond()


def test_tui_error_card_and_retry_resume(tui, monkeypatch):
    """A dropped connection shows an ErrorBlock; clicking ↻ Retry sends the same
    conversation again — no second copy of the user's message."""
    import copy

    import jarvis.repl.render as render
    import jarvis.repl.stream as stream
    from jarvis.tui.transcript import ErrorBlock, UserBlock

    monkeypatch.setattr(state, "messages", [])
    monkeypatch.setattr(state, "client", object())
    monkeypatch.setattr(state, "provider", "openai_codex")
    monkeypatch.setattr(state, "MODEL", "gpt-test")
    requests: list[list] = []
    ran: list[str] = []

    def fake_call():
        requests.append(copy.deepcopy(state.messages))
        if len(requests) == 1:
            raise anthropic.APIConnectionError(request=_REQ)
        return SimpleNamespace(stop_reason="end_turn", content=[{"type": "text", "text": "Back."}])

    monkeypatch.setattr(stream, "call_claude_stream", fake_call)
    monkeypatch.setattr(render, "render_assistant", lambda resp: False)
    monkeypatch.setattr(render, "classify_empty_turn", lambda resp, n: None)

    async def run() -> None:
        app = tui()
        async with app.run_test(size=(110, 40)) as pilot:
            await pilot.pause(0.2)
            app._begin_turn("summarise the repo")
            assert await _until(pilot, lambda: not app._busy and app.query(ErrorBlock))
            block = app.query(ErrorBlock).last()
            assert block.report.kind == "network" and block.has_class("-warn")
            await pilot.pause(0.1)
            text = block.render().plain
            assert "Connection lost" in text and "↻ Retry" in text and "▸ Details" in text
            assert "OpenAI Codex · gpt-test" in text

            # Details chip expands the raw error in place.
            line, x0, _x1, _ = next(c for c in block._chips if c[3] == "::details")
            await pilot.click(ErrorBlock, offset=(3 + x0 + 1, 1 + line))
            assert block.expanded
            assert "APIConnectionError" in block.render().plain

            # ↻ Retry resumes the same request.
            monkeypatch.setattr(app, "run_error_action",
                                lambda cmd, _orig=app.run_error_action: (ran.append(cmd), _orig(cmd)))
            await pilot.pause(0.1)
            line, x0, _x1, _ = next(c for c in block._chips if c[3] == "/retry")
            await pilot.click(ErrorBlock, offset=(3 + x0 + 1, 1 + line))
            assert ran == ["/retry"]
            assert await _until(pilot, lambda: not app._busy and len(requests) == 2)
            assert requests[1] == requests[0] == [{"role": "user", "content": "summarise the repo"}]
            assert [m["role"] for m in state.messages] == ["user", "assistant"]
            assert len(app.query(UserBlock)) == 1  # retry doesn't echo a prompt
            assert state.last_api_error is None

            # Nothing left to retry now: it says so instead of sending.
            app._begin_turn("/retry")
            assert await _until(pilot, lambda: not app._busy)
            assert len(requests) == 2

    asyncio.run(run())


def test_tui_not_signed_in_card(tui, monkeypatch):
    from jarvis.tui.transcript import ErrorBlock

    monkeypatch.setattr(state, "messages", [])
    monkeypatch.setattr(state, "client", None)
    import jarvis.auth.client as client_mod

    monkeypatch.setattr(client_mod, "make_client", lambda interactive=False: None)

    async def run() -> None:
        app = tui()
        async with app.run_test(size=(110, 40)) as pilot:
            await pilot.pause(0.2)
            app._begin_turn("hello")
            assert await _until(pilot, lambda: not app._busy and app.query(ErrorBlock))
            rep = app.query(ErrorBlock).last().report
            assert rep.title == "Not signed in"
            assert [a.command for a in rep.actions] == ["/login", "/key", "/provider"]

    asyncio.run(run())


def test_error_block_wraps_chips_on_narrow_widths(session):
    from jarvis.tui.transcript import ErrorBlock

    rep = api_errors.classify(_status(402, {"error": {"message": "Insufficient credits"}}))
    block = ErrorBlock(rep)
    text = block._build(24).plain
    assert all(len(line) <= 24 for line in text.split("\n"))
    rows = {line for line, *_ in block._chips}
    assert len(rows) > 1  # chips wrapped onto several rows


# ─── web: the error event + snapshot ─────────────────────────────────────

def test_web_mux_mirrors_error_cards(session):
    from jarvis.web.console_mux import WebMuxConsole

    events, terminal = [], []
    bridge = SimpleNamespace(emit=lambda kind, data: events.append((kind, data)))
    mux = WebMuxConsole(SimpleNamespace(show_error=terminal.append, print=lambda *a, **k: None), bridge)
    rep = api_errors.classify(anthropic.APIConnectionError(request=_REQ))
    api_errors.show(rep, console=mux)
    assert terminal == [rep]
    kind, data = events[-1]
    assert kind == "error"
    assert data["kind"] == "network" and data["severity"] == "warn"
    assert data["actions"][0] == {"label": "Retry", "command": "/retry", "primary": True}


def test_snapshot_carries_the_live_error(session):
    from jarvis.web.state_api import snapshot_from_state

    api_errors.show(api_errors.report("rate_limit"), console=SimpleNamespace(show_error=lambda r: None))
    assert snapshot_from_state()["error"]["kind"] == "rate_limit"
    assert snapshot_from_state(busy=True)["error"] is None
    state.messages.append({"role": "user", "content": "next"})
    assert snapshot_from_state()["error"] is None
