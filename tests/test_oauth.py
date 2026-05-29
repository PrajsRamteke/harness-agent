"""OAuth helper tests (no live network)."""
from jarvis.auth.oauth_tokens import parse_oauth_code, parse_oauth_paste, exchange_oauth_code
from jarvis.auth.pkce import _pkce_pair


def test_parse_oauth_code_bare():
    assert parse_oauth_code("abc123") == "abc123"


def test_parse_oauth_code_with_state_fragment():
    assert parse_oauth_code("abc123#state-token") == "abc123"


def test_parse_oauth_paste_with_state():
    code, state = parse_oauth_paste("abc123#state-token", "fallback")
    assert code == "abc123"
    assert state == "state-token"


def test_exchange_oauth_code_requires_state():
    status, body = exchange_oauth_code("abc123", "verifier", "")
    assert status == 400
    assert isinstance(body, dict)


def test_parse_oauth_code_from_callback_url():
    url = "https://platform.claude.com/oauth/code/callback?code=abc123&state=xyz"
    assert parse_oauth_code(url) == "abc123"


def test_anthropic_auth_models_for_picker():
    from jarvis import state
    from jarvis.auth.anthropic_models import anthropic_auth_models_for_picker
    from jarvis.constants import ANTHROPIC_AUTH_MODEL_IDS

    state.anthropic_model_ids = None
    rows = anthropic_auth_models_for_picker()
    assert [m for m, _ in rows] == list(ANTHROPIC_AUTH_MODEL_IDS)

    state.anthropic_model_ids = ["claude-sonnet-4-6", "claude-custom-preview"]
    rows = anthropic_auth_models_for_picker()
    assert rows[0][0] == "claude-opus-4-8"
    assert "claude-custom-preview" in [m for m, _ in rows]
    state.anthropic_model_ids = None


def test_pkce_pair_returns_distinct_state_and_verifier():
    verifier, challenge, oauth_state = _pkce_pair()
    assert verifier
    assert challenge
    assert oauth_state
    assert verifier != oauth_state
