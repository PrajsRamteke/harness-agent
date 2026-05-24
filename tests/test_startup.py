"""Startup performance helpers — update throttle and bootstrap fast path."""
from __future__ import annotations

import time

from jarvis.bootstrap import _has_any_credentials_fast, ensure_harness_agent_defaults
from jarvis.updater import (
    maybe_update_and_reexec,
    should_check_for_updates,
    start_background_update,
)


def test_should_check_for_updates_respects_skip_env(monkeypatch):
    monkeypatch.delenv("HARNESS_SKIP_UPDATE", raising=False)
    monkeypatch.delenv("HARNESS_UPDATE_INTERVAL", raising=False)
    assert should_check_for_updates(now=0.0) is True

    monkeypatch.setenv("HARNESS_SKIP_UPDATE", "1")
    assert should_check_for_updates(now=0.0) is False


def test_should_check_for_updates_checks_every_launch_by_default(monkeypatch):
    monkeypatch.delenv("HARNESS_SKIP_UPDATE", raising=False)
    monkeypatch.delenv("HARNESS_UPDATE_INTERVAL", raising=False)
    assert should_check_for_updates(now=0.0) is True


def test_should_check_for_updates_throttles_when_interval_set(monkeypatch, tmp_path):
    monkeypatch.delenv("HARNESS_SKIP_UPDATE", raising=False)
    monkeypatch.setenv("HARNESS_UPDATE_INTERVAL", "86400")
    stamp = tmp_path / "last_update_check"
    stamp.write_text(str(time.time()), encoding="utf-8")
    monkeypatch.setattr("jarvis.updater.UPDATE_CHECK_STAMP", stamp)

    assert should_check_for_updates(now=time.time()) is False
    assert should_check_for_updates(now=time.time() + 25 * 3600) is True


def test_bootstrap_pins_free_tier_on_first_install(monkeypatch):
    monkeypatch.setattr("jarvis.storage.prefs.load_saved_model", lambda: "")
    monkeypatch.setattr("jarvis.storage.prefs.should_use_first_run_harness_defaults", lambda: True)
    from jarvis import state
    from jarvis.constants.providers import HARNESS_AGENT_DEFAULT_MODEL, PROVIDER_OPENCODE_ZEN

    state.harness_agent_free = False
    state.MODEL = "claude-sonnet-4-6"
    state.provider = "anthropic"
    ensure_harness_agent_defaults()
    assert state.provider == PROVIDER_OPENCODE_ZEN
    assert state.MODEL == HARNESS_AGENT_DEFAULT_MODEL
    assert state.harness_agent_free is True


def test_bootstrap_respects_saved_model_preference(monkeypatch):
    monkeypatch.setattr("jarvis.storage.prefs.should_use_first_run_harness_defaults", lambda: False)
    from jarvis import state

    state.MODEL = "claude-sonnet-4-6"
    state.provider = "anthropic"
    ensure_harness_agent_defaults()
    assert state.MODEL == "claude-sonnet-4-6"
    assert state.provider == "anthropic"


def test_bootstrap_pins_free_tier_without_credentials(monkeypatch):
    monkeypatch.setattr("jarvis.storage.prefs.load_saved_model", lambda: "")
    monkeypatch.setattr("jarvis.storage.prefs.should_use_first_run_harness_defaults", lambda: True)
    from jarvis import state
    from jarvis.constants.providers import HARNESS_AGENT_DEFAULT_MODEL, PROVIDER_OPENCODE_ZEN

    state.harness_agent_free = False
    ensure_harness_agent_defaults()
    assert state.provider == PROVIDER_OPENCODE_ZEN
    assert state.MODEL == HARNESS_AGENT_DEFAULT_MODEL
    assert state.harness_agent_free is True


def test_has_any_credentials_fast_env(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    monkeypatch.delenv("OPENCODE_API_KEY", raising=False)
    monkeypatch.delenv("OPENCODE_ZEN_API_KEY", raising=False)
    assert _has_any_credentials_fast() in (True, False)


def test_maybe_update_and_reexec_skips_after_reexec(monkeypatch):
    monkeypatch.setenv("HARNESS_UPDATED_REEXEC", "1")
    called = {"n": 0}

    def _boom():
        called["n"] += 1
        return {"updated": True}

    monkeypatch.setattr("jarvis.updater.check_and_update", _boom)
    maybe_update_and_reexec()
    assert called["n"] == 0


def test_start_background_update_respects_skip(monkeypatch):
    monkeypatch.setenv("HARNESS_SKIP_UPDATE", "1")
    called = {"n": 0}

    def _fake_thread(*_a, **_k):
        called["n"] += 1
        class _T:
            def start(self):
                called["n"] += 1
        return _T()

    monkeypatch.setattr("threading.Thread", _fake_thread)
    start_background_update()
    assert called["n"] == 0
