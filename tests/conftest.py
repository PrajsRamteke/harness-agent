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
def _isolated_models_dev(tmp_path, monkeypatch):
    """The models.dev catalog (``jarvis/auth/models_dev.py``) adds a provider
    for every key in the environment. Without this, a developer's real cache
    plus an OPENAI_API_KEY in their shell would change what every picker test
    sees. Each test starts with an empty catalog, its own keys file and no
    network; tests that need a catalog seed one with ``models_dev.store``."""
    from jarvis.auth import models_dev
    from jarvis.constants import paths

    monkeypatch.setattr(models_dev, "CACHE_FILE", tmp_path / "models_dev.json")
    monkeypatch.setattr(paths, "PROVIDER_KEYS_FILE", tmp_path / "provider_keys.json")

    def _offline(*_a, **_k):
        raise OSError("network disabled in tests (models.dev)")

    monkeypatch.setattr(models_dev, "_http_get", _offline)
    models_dev._memo.update(mtime=None, path=None, providers={}, native={}, meta={})
    models_dev._memo.pop("shared", None)
    yield
    models_dev._memo.update(mtime=None, path=None, providers={}, native={}, meta={})
    models_dev._memo.pop("shared", None)


@pytest.fixture(autouse=True)
def _no_macos_permission_prompts(monkeypatch):
    """Never let a test pop the macOS Screen Recording prompt or open System
    Settings on the developer's machine."""
    import importlib

    shot = importlib.import_module("jarvis.tools.screenshot")
    monkeypatch.setattr(shot, "request_screen_recording", lambda: None)
    monkeypatch.setattr(shot, "_permission_requested", False)


@pytest.fixture()
def ext_env(tmp_path, monkeypatch):
    """Skills / MCP installs against a scratch HOME + project folder.

    Nothing reaches the real ``~/.config/harness-agent``, ``~/.harness``,
    ``~/.claude`` … or the current project, and no config is saved to settings.
    """
    import pathlib
    import types

    import jarvis.mcp.auth as mcp_auth
    import jarvis.mcp.config as mcp_config
    import jarvis.mcp.secrets as mcp_secrets
    import jarvis.storage.skill_install as skill_install
    import jarvis.storage.skills as skills
    from jarvis import state

    home = tmp_path / "home"
    proj = tmp_path / "proj"
    home.mkdir()
    proj.mkdir()
    monkeypatch.setattr(pathlib.Path, "home", classmethod(lambda cls: home))
    monkeypatch.setattr(mcp_config, "MCP_GLOBAL_CONFIG_FILE", home / ".config" / "harness-agent" / "mcp.json")
    monkeypatch.setattr(
        mcp_config, "_global_sources",
        lambda: [("jarvis", mcp_config.MCP_GLOBAL_CONFIG_FILE, "")],
    )
    monkeypatch.setattr(mcp_secrets, "SECRETS_FILE", home / ".config" / "harness-agent" / "mcp_secrets.json")
    monkeypatch.setattr(mcp_auth, "AUTH_DIR", home / ".config" / "harness-agent" / "mcp-auth")
    monkeypatch.setattr(skill_install, "HARNESS_SKILLS_DIR", home / ".harness" / "skills")
    monkeypatch.setattr(skills, "HARNESS_SKILLS_DIR", home / ".harness" / "skills")
    monkeypatch.setattr(skills, "CONFIG_DIR", home / ".config" / "harness-agent")
    monkeypatch.chdir(proj)
    monkeypatch.setattr(state, "global_mcp", False)
    monkeypatch.setattr(state, "global_skills", False)
    monkeypatch.setattr(state, "save_mcp_config", lambda: None)
    monkeypatch.setattr(state, "save_skills_config", lambda: None)
    monkeypatch.setattr(mcp_config, "_config", None)
    skills.invalidate_cache()
    yield types.SimpleNamespace(home=home, proj=proj)
    monkeypatch.setattr(mcp_config, "_config", None)
    skills.invalidate_cache()
