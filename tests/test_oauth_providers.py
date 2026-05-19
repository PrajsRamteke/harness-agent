"""OAuth provider registry tests."""
from jarvis.auth.connect.oauth_status import oauth_connection_status
from jarvis.constants.oauth_providers import (
    OAUTH_PROVIDERS, OAUTH_ID_ANTHROPIC, OAUTH_ID_OPENAI_CODEX, oauth_provider,
)


def test_oauth_providers_are_subscription_login_only():
    ids = {p.id for p in OAUTH_PROVIDERS}
    assert OAUTH_ID_ANTHROPIC in ids
    assert OAUTH_ID_OPENAI_CODEX in ids
    assert "anthropic_api" not in ids
    assert "openrouter" not in ids


def test_anthropic_oauth_status_when_logged_out():
    spec = oauth_provider(OAUTH_ID_ANTHROPIC)
    assert spec is not None
    st = oauth_connection_status(spec)
    assert st.connected is False


def test_openai_codex_available():
    spec = oauth_provider(OAUTH_ID_OPENAI_CODEX)
    assert spec is not None
    assert spec.available is True
    st = oauth_connection_status(spec)
    assert st.connected is False
