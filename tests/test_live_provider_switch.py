"""Credentials added mid-session must work without restarting Jarvis.

Covers the chain behind "I added an OpenRouter key / signed in, picked a
model, and nothing worked until I restarted": history from the previous
provider, stale auth mode, stale model id after sign-in, the free-tier flag
winning over an explicit paid pick, and keys edited in /key.
"""
import json
import sys

import pytest

from jarvis import state
from jarvis.auth.opencode_client import _ContentBlock, _TextBlock, _ToolUseBlock
from jarvis.constants import (
    AUTH_API_KEY,
    AUTH_OAUTH,
    HARNESS_AGENT_DEFAULT_MODEL,
    PROVIDER_ANTHROPIC,
    PROVIDER_ANTHROPIC_AUTH,
    PROVIDER_HARNESS_AGENT,
    PROVIDER_KIMCHI,
    PROVIDER_OPENAI_CODEX,
    PROVIDER_OPENCODE_ZEN,
    PROVIDER_OPENROUTER,
)
from jarvis.repl.trim import anthropic_wire_messages

_KEY_ENV_VARS = (
    "ANTHROPIC_API_KEY", "OPENROUTER_API_KEY", "OPENCODE_API_KEY",
    "OPENCODE_ZEN_API_KEY", "KIMCHI_API_KEY",
)
_PATH_NAMES = (
    "KEY_FILE", "OPENROUTER_KEY_FILE", "OPENCODE_KEY_FILE", "OPENCODE_ZEN_KEY_FILE",
    "KIMCHI_KEY_FILE", "PROVIDER_FILE", "AUTH_MODE_FILE",
)


@pytest.fixture()
def sandbox(tmp_path, monkeypatch):
    """Credential/config files in tmp_path, no OAuth, session state restored."""
    for var in _KEY_ENV_VARS:
        monkeypatch.delenv(var, raising=False)
    paths = {name: tmp_path / name.lower() for name in _PATH_NAMES}
    # Modules keep their own `from ..constants import X` copies — patch them all.
    for mod in list(sys.modules.values()):
        if not getattr(mod, "__name__", "").startswith("jarvis"):
            continue
        for name, path in paths.items():
            if hasattr(mod, name):
                monkeypatch.setattr(mod, name, path)
    for target in (
        "jarvis.auth.oauth_tokens.load_oauth_tokens",
        "jarvis.auth.client.load_oauth_tokens",
        "jarvis.commands.control.load_oauth_tokens",
        "jarvis.auth.codex_oauth_tokens.load_codex_oauth_tokens",
        "jarvis.auth.client.load_codex_oauth_tokens",
        "jarvis.commands.control.load_codex_oauth_tokens",
    ):
        monkeypatch.setattr(target, lambda: None)
    import jarvis.storage.settings as settings

    monkeypatch.setattr(settings.Settings, "save", lambda self: None)
    saved: list[str] = []
    monkeypatch.setattr("jarvis.commands.control.save_last_model", lambda: saved.append(state.MODEL))
    monkeypatch.setattr("jarvis.auth.connect.oauth_actions.save_last_model",
                        lambda: saved.append(state.MODEL))
    monkeypatch.setattr("jarvis.commands.control.header_panel", lambda: None)

    def no_prompt(*_a, **_k):
        raise AssertionError("must not prompt for a key")

    for target in ("jarvis.auth.api_key.prompt_for_key",
                   "jarvis.auth.openrouter.prompt_for_openrouter_key"):
        monkeypatch.setattr(target, no_prompt)

    # Start every test on the free Harness Agent tier.
    monkeypatch.setattr(state, "provider", PROVIDER_OPENCODE_ZEN)
    monkeypatch.setattr(state, "auth_mode", AUTH_API_KEY)
    monkeypatch.setattr(state, "MODEL", HARNESS_AGENT_DEFAULT_MODEL)
    monkeypatch.setattr(state, "harness_agent_free", True)
    monkeypatch.setattr(state, "client", object())
    paths["saved"] = saved
    return paths


# ── history from another provider ───────────────────────────────────────────

def test_wire_messages_turn_foreign_blocks_into_plain_dicts():
    history = [
        {"role": "user", "content": "hi"},
        {"role": "assistant", "content": [
            _ContentBlock(type="thinking", thinking="hmm"),  # no signature
            _TextBlock(text="hello", extra="dropped"),
            _ToolUseBlock(id="t1", name="bash", input={"cmd": "ls"}),
        ]},
        {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "t1", "content": "ok"}]},
    ]
    out = anthropic_wire_messages(history)
    json.dumps(out)  # the SDK must be able to encode it
    assert out[1]["content"] == [
        {"type": "text", "text": "hello"},
        {"type": "tool_use", "id": "t1", "name": "bash", "input": {"cmd": "ls"}},
    ]
    assert out[0] is history[0] and out[2] is history[2]
    assert isinstance(history[1]["content"][0], _ContentBlock)  # input untouched


def test_wire_messages_keep_signed_thinking_and_sdk_blocks():
    from anthropic.types import TextBlock

    sdk = TextBlock(type="text", text="from claude")
    signed = {"type": "thinking", "thinking": "t", "signature": "sig"}
    history = [{"role": "assistant", "content": [signed, sdk]}]
    assert anthropic_wire_messages(history) == history


def test_wire_messages_never_leave_an_assistant_turn_empty():
    history = [{"role": "assistant", "content": [_ContentBlock(type="thinking", thinking="x")]}]
    out = anthropic_wire_messages(history)
    assert out[0]["content"] == [{"type": "text", "text": "…"}]


# ── picking a model right after adding its credential ───────────────────────

def test_openrouter_key_added_mid_session_is_used(sandbox):
    from jarvis.commands.control import _apply_model_selection

    sandbox["OPENROUTER_KEY_FILE"].write_text("sk-or-new-key")
    _apply_model_selection("vendor/model:free", source=PROVIDER_OPENROUTER)

    assert state.provider == PROVIDER_OPENROUTER
    assert state.MODEL == "vendor/model:free"
    assert state.client.auth_token == "sk-or-new-key"
    assert state.harness_agent_free is False


def test_anthropic_oauth_pick_after_sign_in_does_not_ask_for_api_key(sandbox, monkeypatch):
    from jarvis.commands.control import _apply_model_selection

    tokens = {"access_token": "oat-token", "refresh_token": "r", "expires_at": 9e12}
    for target in ("jarvis.auth.client.load_oauth_tokens",
                   "jarvis.commands.control.load_oauth_tokens"):
        monkeypatch.setattr(target, lambda: tokens)
    monkeypatch.setattr("jarvis.auth.client.get_fresh_oauth_token", lambda: tokens)

    # auth_mode is still the stale "api_key" and there is no API key on disk.
    _apply_model_selection("claude-sonnet-5", source=PROVIDER_ANTHROPIC_AUTH)

    assert state.provider == PROVIDER_ANTHROPIC
    assert state.auth_mode == AUTH_OAUTH
    assert state.MODEL == "claude-sonnet-5"
    assert state.client.auth_token == "oat-token"


def test_switching_to_anthropic_never_keeps_another_providers_model(sandbox, monkeypatch):
    from jarvis.commands.control import _handle_provider

    sandbox["KEY_FILE"].write_text("sk-ant-test")
    monkeypatch.setattr(state, "provider", PROVIDER_OPENROUTER)
    monkeypatch.setattr(state, "MODEL", "vendor/model:free")
    _handle_provider(PROVIDER_ANTHROPIC)
    assert state.provider == PROVIDER_ANTHROPIC
    assert state.MODEL.startswith("claude-")


def test_paid_zen_pick_on_free_tier_builds_the_paid_client(sandbox, monkeypatch):
    import jarvis.auth.client as client

    monkeypatch.setattr(client, "build_harness_agent_client", lambda: "free")
    monkeypatch.setattr(client, "_build_opencode_zen_client", lambda: "paid")
    assert client._build_opencode_zen_client_for_model(
        "big-pickle", source=PROVIDER_OPENCODE_ZEN) == "paid"
    assert state.harness_agent_free is False
    assert client._build_opencode_zen_client_for_model(
        "big-pickle", source=PROVIDER_HARNESS_AGENT) == "free"
    assert state.harness_agent_free is True


@pytest.mark.parametrize("provider, prefix", [
    (PROVIDER_ANTHROPIC, "claude-"),
    (PROVIDER_OPENAI_CODEX, "gpt-"),
])
def test_sign_in_moves_the_model_to_the_new_provider(sandbox, provider, prefix):
    from jarvis.auth.connect.oauth_actions import adopt_provider_model

    state.provider = provider
    adopt_provider_model(provider)
    assert state.MODEL.startswith(prefix)
    assert state.harness_agent_free is False
    assert sandbox["saved"] == [state.MODEL]


# ── /key edits apply to the running session ─────────────────────────────────

def test_replacing_the_active_key_rebuilds_the_client(sandbox, monkeypatch):
    from jarvis.commands.control import apply_key_change

    monkeypatch.setattr(state, "provider", PROVIDER_OPENROUTER)
    monkeypatch.setattr(state, "harness_agent_free", False)
    sandbox["OPENROUTER_KEY_FILE"].write_text("sk-or-replacement")
    note = apply_key_change(PROVIDER_OPENROUTER)
    assert "now in use" in note
    assert state.client.auth_token == "sk-or-replacement"


def test_key_for_another_provider_only_points_at_model_picker(sandbox):
    from jarvis.commands.control import apply_key_change

    before = state.client
    assert apply_key_change(PROVIDER_KIMCHI) == "Kimchi models are now in /model"
    assert state.client is before
    assert apply_key_change(PROVIDER_KIMCHI, removed=True) == ""


def test_deleting_the_active_key_falls_back_to_the_free_tier(sandbox, monkeypatch):
    from jarvis.commands.control import apply_key_change

    monkeypatch.setattr(state, "provider", PROVIDER_OPENROUTER)
    monkeypatch.setattr(state, "MODEL", "vendor/model:free")
    monkeypatch.setattr(state, "harness_agent_free", False)
    note = apply_key_change(PROVIDER_OPENROUTER, removed=True)
    assert "Harness Agent" in note
    assert state.provider == PROVIDER_OPENCODE_ZEN
    assert state.harness_agent_free is True
    assert state.MODEL == HARNESS_AGENT_DEFAULT_MODEL  # not the OpenRouter slug
    assert sandbox["saved"] == [HARNESS_AGENT_DEFAULT_MODEL]
