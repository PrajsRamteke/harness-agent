"""Command palette catalog invariants."""
from jarvis.tui.commands_catalog import COMMANDS


def test_commands_catalog_has_unique_ids():
    ids = [cmd for cmd, _desc in COMMANDS]
    assert len(ids) == len(set(ids)), f"duplicate command ids: {[i for i in ids if ids.count(i) > 1]}"


def test_unlisted_builtins_are_hidden_but_still_reserved():
    from jarvis.commands.help import _SECTIONS
    from jarvis.storage.commands import _is_reserved
    from jarvis.tui.commands_catalog import UNLISTED_BUILTINS, filter_commands
    from jarvis.tui.palette_modal import _GROUPS

    listed = {c.split()[0] for c, _ in filter_commands("")}
    listed |= {c.split()[0] for _, rows in _SECTIONS for c, _ in rows}
    listed |= {c.split()[0] for _, cmds in _GROUPS for c in cmds}
    for name in UNLISTED_BUILTINS:
        assert f"/{name}" not in listed
        # The handler still runs when typed, so a custom command can't take it.
        assert _is_reserved(name)
