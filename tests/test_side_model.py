"""The commit text model (``jarvis/side_model.py``): Auto is the chat's model,
a pinned provider/model is used instead, and the web Settings row / picker /
action all agree on what is chosen."""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from jarvis import side_model, state
from jarvis.auth import client as auth_client
from jarvis.storage import settings as settings_mod
from jarvis.web import actions_api, git_api, pickers_api, state_api


@pytest.fixture(autouse=True)
def isolated_settings(tmp_path, monkeypatch):
    monkeypatch.setattr(settings_mod, "SETTINGS_FILE", tmp_path / "settings.json")
    settings_mod.reload_settings()
    yield


@pytest.fixture
def session(monkeypatch):
    monkeypatch.setattr(state, "client", SimpleNamespace(tag="chat"))
    monkeypatch.setattr(state, "MODEL", "chat-model")
    monkeypatch.setattr(state, "provider", "anthropic")
    monkeypatch.setattr(state, "auth_mode", "api_key")


def test_default_is_auto(session):
    assert side_model.preference() == ("", "")
    assert side_model.is_auto()
    client, model, provider, auth_mode = side_model.resolve()
    assert (client.tag, model, provider, auth_mode) == ("chat", "chat-model", "anthropic", "api_key")
    assert side_model.public() == {"source": "", "model": "", "auto": True, "label": "Auto",
                                   "source_label": "", "model_label": ""}


def test_pinned_model_is_built_for_the_side_request_only(session, monkeypatch):
    made = []

    def build(source, model):
        made.append((source, model))
        return SimpleNamespace(tag="side"), "deepseek", ""

    monkeypatch.setattr(auth_client, "build_side_client", build)
    side_model.set_preference("md:deepseek", "deepseek-chat")
    assert side_model.preference() == ("md:deepseek", "deepseek-chat")
    client, model, provider, auth_mode = side_model.resolve()
    assert (client.tag, model, provider, auth_mode) == ("side", "deepseek-chat", "deepseek", "")
    assert made == [("md:deepseek", "deepseek-chat")]
    # the chat's provider / model are untouched
    assert (state.provider, state.MODEL) == ("anthropic", "chat-model")
    assert side_model.label("openrouter", "z-ai/glm-5") == "OpenRouter · glm-5"


def test_same_provider_and_model_reuses_the_session_client(session, monkeypatch):
    monkeypatch.setattr(auth_client, "build_side_client",
                        lambda s, m: pytest.fail("must not rebuild"))
    side_model.set_preference("anthropic", "chat-model")
    assert side_model.resolve()[0].tag == "chat"


def test_reset_goes_back_to_auto(session):
    side_model.set_preference("openrouter", "x/y")
    side_model.set_preference("", "")
    assert side_model.public()["auto"] is True


def test_git_text_asks_the_pinned_model(session, monkeypatch):
    asked = {}

    class Stream:
        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def get_final_message(self):
            return SimpleNamespace(content=[SimpleNamespace(type="text", text="fix: the thing")])

    class FakeClient:
        class messages:
            @staticmethod
            def stream(**kwargs):
                asked.update(kwargs)
                return Stream()

    side_model.set_preference("md:deepseek", "deepseek-chat")
    monkeypatch.setattr(auth_client, "build_side_client",
                        lambda s, m: (FakeClient, "deepseek", ""))
    assert git_api._ask_model_uncached("sys", f"diff {id(object())}", max_tokens=64) == "fix: the thing"
    assert (asked["model"], asked["max_tokens"]) == ("deepseek-chat", 64)


def test_a_dead_choice_says_so(session, monkeypatch):
    def build(source, model):
        raise RuntimeError("no key")

    monkeypatch.setattr(auth_client, "build_side_client", build)
    side_model.set_preference("md:deepseek", "deepseek-chat")
    with pytest.raises(git_api.GitError) as err:
        git_api._ask_model_uncached("sys", "diff", max_tokens=64)
    assert "isn't available" in str(err.value) and "Auto" in str(err.value)


def test_snapshot_carries_the_choice(session):
    side_model.set_preference("openrouter", "z-ai/glm-5")
    assert state_api.snapshot_from_state()["side_model"]["label"] == "OpenRouter · glm-5"


def test_picker_marks_the_side_choice_not_the_chat_model(session, monkeypatch):
    from jarvis.web import pickers_api as pk

    rows = [("anthropic", "chat-model", "200k")]
    monkeypatch.setattr(pk, "model_picker_rows", lambda: rows)
    side_model.set_preference("anthropic", "chat-model")
    assert pk.list_models(for_side=True)["models"][0]["active"] is True
    side_model.set_preference("anthropic", "other")
    assert pk.list_models(for_side=True)["models"][0]["active"] is False
    # the chat picker still marks the chat's model
    assert pk.list_models()["models"][0]["active"] is True


def test_web_action_saves_and_clears_the_choice(session):
    res = actions_api.run_web_action("side_model_select",
                                     {"source": "anthropic", "model_id": "claude-sonnet-5"},
                                     console_print=lambda *a, **k: None)
    assert res["ok"] and res["side_model"]["auto"] is False
    assert side_model.preference() == ("anthropic", "claude-sonnet-5")
    res = actions_api.run_web_action("side_model_select", {"source": "", "model": ""},
                                     console_print=lambda *a, **k: None)
    assert res["ok"] and res["side_model"]["auto"] is True
    assert side_model.preference() == ("", "")


def test_public_splits_provider_and_model_for_the_settings_row(session):
    side_model.set_preference("openrouter", "deepseek/deepseek-v4-flash")
    pub = side_model.public()
    assert pub["auto"] is False
    assert pub["model_label"] == "deepseek-v4-flash"
    assert pub["source_label"] and pub["source_label"] in pub["label"]
