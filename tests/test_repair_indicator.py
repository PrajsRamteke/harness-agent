"""Repaired tool inputs surface a ⚒ indicator on dock rows."""

from jarvis.repl.tool_runs import begin_wave, list_runs, register_queued, set_done, set_running
from jarvis.utils.tool_repair import REPAIR_NOTE_MARKER, has_repair_note


def test_has_repair_note():
    assert has_repair_note(f"file text\n{REPAIR_NOTE_MARKER} renamed 'file_path' → 'path']")
    assert not has_repair_note("plain tool output")
    assert not has_repair_note(None)


def test_set_done_marks_repaired_run():
    begin_wave()
    register_queued("t-1", "read_file", {"path": "a.py"}, notify=False)
    set_running("t-1")
    set_done("t-1", f"content\n{REPAIR_NOTE_MARKER} coerced string 'limit' to integer]")
    run = list_runs()[0]
    assert run["repaired"] is True
    assert run["status"] == "done"


def test_set_done_clean_output_not_repaired():
    begin_wave()
    register_queued("t-2", "read_file", {"path": "a.py"}, notify=False)
    set_running("t-2")
    set_done("t-2", "content")
    run = list_runs()[0]
    assert run["repaired"] is False


def test_multi_edit_children_inherit_repaired_flag():
    begin_wave()
    register_queued(
        "t-3",
        "multi_edit",
        {
            "edits": [
                {"path": "x.py", "old_str": "a", "new_str": "b"},
                {"path": "y.py", "old_str": "c", "new_str": "d"},
            ]
        },
        notify=False,
    )
    set_running("t-3")
    out = (
        "2 succeeded, 0 failed\n"
        "1/2 EDITED x.py (1 replacement)\n"
        "2/2 EDITED y.py (1 replacement)\n"
        f"{REPAIR_NOTE_MARKER} edits[0]: renamed 'old_string' → 'old_str']"
    )
    set_done("t-3", out)
    runs = {r["label"]: r for r in list_runs()}
    assert runs["x.py"]["repaired"] is True
    assert runs["y.py"]["repaired"] is True


def test_dock_row_shows_repair_glyph():
    from jarvis.tui.mixins.activity import ActivityMixin

    class _Host(ActivityMixin):
        pass

    host = _Host()
    row = host._format_tool_dock_row(
        {
            "status": "done",
            "name": "edit_file",
            "label": "a.py",
            "chars": 42,
            "repaired": True,
        }
    )
    assert "⚒" in row
    row_clean = host._format_tool_dock_row(
        {
            "status": "done",
            "name": "edit_file",
            "label": "a.py",
            "chars": 42,
            "repaired": False,
        }
    )
    assert "⚒" not in row_clean
