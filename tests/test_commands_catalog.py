"""Command palette catalog invariants."""
from jarvis.tui.commands_catalog import COMMANDS


def test_commands_catalog_has_unique_ids():
    ids = [cmd for cmd, _desc in COMMANDS]
    assert len(ids) == len(set(ids)), f"duplicate command ids: {[i for i in ids if ids.count(i) > 1]}"
