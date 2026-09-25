"""Shared test isolation."""
import pytest


@pytest.fixture(autouse=True)
def _isolated_pet(tmp_path, monkeypatch):
    """Jarvis the pet saves to ~/.config/harness-agent/pet.json on every
    reaction (turn done, tool finished, …). Keep every test — including TUI
    tests that finish turns — away from the real file."""
    import jarvis.pet as pet_pkg
    import jarvis.pet.model as pet_model

    import jarvis.pet.session as pet_session

    monkeypatch.setattr(pet_model, "PET_FILE", tmp_path / "pet.json")
    pet_model.reset_cache()
    pet_session.reset()
    yield
    pet_model.reset_cache()
    pet_pkg.set_reaction_hook(None)
    pet_pkg.set_action_hook(None)


@pytest.fixture(autouse=True)
def _no_macos_permission_prompts(monkeypatch):
    """Never let a test pop the macOS Screen Recording prompt or open System
    Settings on the developer's machine."""
    import importlib

    shot = importlib.import_module("jarvis.tools.screenshot")
    monkeypatch.setattr(shot, "request_screen_recording", lambda: None)
    monkeypatch.setattr(shot, "_permission_requested", False)
