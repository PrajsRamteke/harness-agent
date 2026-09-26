"""settings.json written by older versions follows new defaults once."""
import json

import pytest

import jarvis.storage.settings as settings_mod
from jarvis.storage.settings import DEFAULTS, SETTINGS_VERSION, Settings


@pytest.fixture()
def settings_file(tmp_path, monkeypatch):
    monkeypatch.setattr(settings_mod, "_migrate_legacy", lambda: {})
    monkeypatch.setattr(settings_mod, "_read_project_settings", lambda: {})
    return tmp_path / "settings.json"


def _write(path, doc):
    path.write_text(json.dumps(doc) + "\n")


def test_default_thinking_effort_is_high():
    assert DEFAULTS["think"] == {"mode": True, "effort": "high"}


def test_old_file_with_baked_in_medium_moves_to_high(settings_file):
    # Pre-v2 saves wrote every default to disk, including the old "medium".
    _write(settings_file, {"model": "m", "think": {"mode": True, "effort": "medium"}})
    s = Settings(settings_file)
    assert s.get("think.effort") == "high"
    on_disk = json.loads(settings_file.read_text())
    assert on_disk["think"]["effort"] == "high"
    assert on_disk["_version"] == SETTINGS_VERSION
    assert on_disk["model"] == "m"


def test_old_file_keeps_other_efforts(settings_file):
    _write(settings_file, {"think": {"mode": True, "effort": "low"}})
    assert Settings(settings_file).get("think.effort") == "low"


def test_medium_chosen_after_upgrade_sticks(settings_file):
    _write(settings_file, {"think": {"mode": True, "effort": "medium"}})
    Settings(settings_file).set("think.effort", "medium")  # explicit choice
    assert Settings(settings_file).get("think.effort") == "medium"


def test_set_global_on_an_old_file_upgrades_it_first(settings_file):
    _write(settings_file, {"think": {"mode": True, "effort": "medium"}})
    Settings(settings_file).set_global("model", "x")
    on_disk = json.loads(settings_file.read_text())
    assert on_disk["think"]["effort"] == "high" and on_disk["model"] == "x"


def test_version_marker_is_not_a_user_setting(settings_file):
    s = Settings(settings_file)
    s.set("theme", "red")
    assert "_version" in json.loads(settings_file.read_text())
    assert "_version" not in s.all()
    assert "_version" not in s.overrides()
