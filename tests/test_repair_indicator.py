"""Repaired tool inputs surface a ⚒ indicator on tool rows."""

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


def test_tool_row_shows_repair_glyph():
    """Transcript tool rows flag repaired inputs with ⚒ (and only then)."""
    from jarvis.tui.transcript import ToolBlock

    row = ToolBlock("t-9", "edit_file", {"path": "a.py"})
    row.finish(f"EDITED a.py (1 replacement)\n{REPAIR_NOTE_MARKER} renamed 'x' → 'y']", repaired=True)
    assert "⚒" in row.as_text().plain
    assert row.status == "done"

    clean = ToolBlock("t-10", "edit_file", {"path": "a.py"})
    clean.finish("EDITED a.py (1 replacement)", repaired=False)
    assert "⚒" not in clean.as_text().plain
