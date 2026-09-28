"""Claude Pro/Max (OAuth) traffic claims a Claude Code version; Anthropic refuses
newer models to old ones ("claude_code_version_too_old"). The harness sends a
recent version, learns a newer minimum from the refusal, and retries once."""
from types import SimpleNamespace

import anthropic
import httpx
import pytest

import jarvis.repl.stream as stream
from jarvis.auth import oauth_tokens as ot
from jarvis.console import HarnessAPIError
from jarvis.constants.oauth import CLAUDE_CODE_VERSION

# The refusal as Anthropic sends it (from a real session, 2026-09-28).
REFUSAL = (
    "Error code: 400 - {'type': 'error', 'error': {'type': 'invalid_request_error', "
    "'message': \"Claude Code 2.1.98 does not support this model; version NEED or newer "
    "is required. Run 'claude update', or update the Claude desktop app, then try again.\", "
    "'details': {'error_code': 'claude_code_version_too_old'}}, 'request_id': 'req_011CfVUh'}"
)


@pytest.fixture
def version_file(tmp_path, monkeypatch):
    path = tmp_path / "claude_code_version"
    monkeypatch.setattr(ot, "_version_file", lambda: path)
    return path


def test_baseline_meets_the_minimum_anthropic_asked_for():
    assert ot._version_key(CLAUDE_CODE_VERSION) >= (2, 1, 280)


def test_headers_claim_the_current_version(version_file):
    assert ot.oauth_client_headers()["User-Agent"] == f"claude-cli/{CLAUDE_CODE_VERSION} (external, cli)"
    assert ot.oauth_user_agent() == ot.oauth_client_headers()["User-Agent"]


def test_a_newer_minimum_is_learned_saved_and_sent(version_file):
    assert ot.learn_claude_code_version(REFUSAL.replace("NEED", "2.1.900")) == "2.1.900"
    assert version_file.read_text() == "2.1.900"
    assert ot.oauth_client_headers()["User-Agent"] == "claude-cli/2.1.900 (external, cli)"


def test_nothing_to_learn(version_file):
    # Already met (the minimum in the reported error is below the baseline).
    assert ot.learn_claude_code_version(REFUSAL.replace("NEED", "2.1.280")) is None
    # Any other 400.
    assert ot.learn_claude_code_version("Error code: 400 - prompt is too long") is None
    assert not version_file.exists()


def test_a_saved_version_never_goes_below_the_baseline(version_file):
    version_file.write_text("2.0.1")
    assert ot.claude_code_version() == CLAUDE_CODE_VERSION
    version_file.write_text("not a version")
    assert ot.claude_code_version() == CLAUDE_CODE_VERSION


# ─── The request loop retries once with the learned version ──────────────


def _refusal(need: str):
    def raise_it():
        request = httpx.Request("POST", "https://api.anthropic.com/v1/messages")
        raise anthropic.BadRequestError(
            REFUSAL.replace("NEED", need), response=httpx.Response(400, request=request), body=None,
        )
    return raise_it


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


class _Messages:
    def __init__(self, script):
        self.script, self.calls = script, 0

    def stream(self, **kwargs):
        behavior = self.script[min(self.calls, len(self.script) - 1)]
        self.calls += 1
        return _Ctx(behavior)


@pytest.fixture
def oauth_session(monkeypatch, version_file):
    from jarvis import state

    for name, value in {
        "build_system": lambda: "sys",
        "select_tools": lambda msgs: [],
        "trim_messages": lambda msgs: msgs,
        "_heal_message_history": lambda: None,
        "report_turn_phase": lambda *a, **k: None,
    }.items():
        monkeypatch.setattr(stream, name, value)
    monkeypatch.setattr(stream.time, "sleep", lambda *a, **k: None)
    printed: list[str] = []
    monkeypatch.setattr(stream, "console", SimpleNamespace(print=lambda m="", *a, **k: printed.append(str(m))))
    monkeypatch.setattr(state, "messages", [{"role": "user", "content": "hi"}])
    monkeypatch.setattr(state, "stream_reply_live", False)
    monkeypatch.setattr(state, "think_mode", False)
    monkeypatch.setattr(state, "provider", "anthropic")
    monkeypatch.setattr(state, "auth_mode", "oauth")
    monkeypatch.setattr(state, "MODEL", "claude-newest")
    state.cancel_requested.clear()
    rebuilt: list[str] = []

    def install(script):
        client = SimpleNamespace(messages=_Messages(script))
        monkeypatch.setattr(state, "client", client)
        # Rebuilding keeps the same fake so the script carries on.
        monkeypatch.setattr(stream, "_build_client_from_mode", lambda mode, **k: rebuilt.append(mode) or client)
        return client

    return SimpleNamespace(install=install, printed=printed, rebuilt=rebuilt)


def test_refusal_is_healed_and_the_turn_goes_through(oauth_session, version_file):
    client = oauth_session.install([_refusal("2.1.900"), lambda: _final()])
    final = stream.call_claude_stream()
    assert final.usage.output_tokens == 5
    assert client.messages.calls == 2
    assert oauth_session.rebuilt == ["oauth"]
    assert version_file.read_text() == "2.1.900"
    assert any("2.1.900" in line for line in oauth_session.printed)


def test_no_retry_loop_when_the_new_version_is_refused_too(oauth_session):
    client = oauth_session.install([_refusal("2.1.900")])  # every attempt refused
    with pytest.raises(anthropic.BadRequestError):
        stream.call_claude_stream()
    assert client.messages.calls == 2  # the original + one retry, then give up


def test_api_key_sessions_are_left_alone(oauth_session, monkeypatch, version_file):
    from jarvis import state

    monkeypatch.setattr(state, "auth_mode", "api_key")
    client = oauth_session.install([_refusal("2.1.900")])
    with pytest.raises(anthropic.BadRequestError):
        stream.call_claude_stream()
    assert client.messages.calls == 1 and not version_file.exists()
