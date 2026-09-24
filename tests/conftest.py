"""Shared test isolation."""
import pytest


@pytest.fixture(autouse=True)
def _isolated_pet(tmp_path, monkeypatch):
    """Jarvis the pet saves to ~/.config/harness-agent/pet.json on every
    reaction (turn done, tool finished, …). Keep every test — including TUI
    tests that finish turns — away from the real file."""
    import jarvis.pet as pet_pkg
    import jarvis.pet.model as pet_model

    monkeypatch.setattr(pet_model, "PET_FILE", tmp_path / "pet.json")
    pet_model.reset_cache()
    yield
    pet_model.reset_cache()
    pet_pkg.set_reaction_hook(None)
