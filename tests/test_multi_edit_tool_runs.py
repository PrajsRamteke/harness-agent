"""multi_edit expands into parallel file dock rows like read_file."""
from jarvis.repl.tool_runs import (
    begin_wave,
    expand_dock_tool_use,
    list_runs,
    multi_edit_paths,
    register_queued,
    set_done,
    set_running,
    show_parallel_file_panel,
)


def test_multi_edit_paths_dedupes_in_order():
    inp = {
        "edits": [
            {"path": "a.py", "old_str": "x", "new_str": "y"},
            {"path": "b.py", "old_str": "x", "new_str": "y"},
            {"path": "a.py", "old_str": "z", "new_str": "w"},
        ]
    }
    assert multi_edit_paths(inp) == ["a.py", "b.py"]


def test_multi_edit_registers_parallel_rows():
    begin_wave()
    register_queued(
        "tool-1",
        "multi_edit",
        {
            "edits": [
                {"path": "jarvis/a.py", "old_str": "1", "new_str": "2"},
                {"path": "jarvis/b.py", "old_str": "3", "new_str": "4"},
                {"path": "jarvis/c.py", "old_str": "5", "new_str": "6"},
            ]
        },
        notify=False,
    )
    runs = list_runs()
    assert len(runs) == 3
    assert show_parallel_file_panel()
    assert [r["label"] for r in runs] == ["jarvis/a.py", "jarvis/b.py", "jarvis/c.py"]
    assert all(r["name"] == "multi_edit" for r in runs)


def test_multi_edit_set_done_splits_per_path():
    begin_wave()
    register_queued(
        "tool-2",
        "multi_edit",
        {
            "edits": [
                {"path": "x.py", "old_str": "a", "new_str": "b"},
                {"path": "y.py", "old_str": "c", "new_str": "d"},
            ]
        },
        notify=False,
    )
    set_running("tool-2")
    out = (
        "2 succeeded, 0 failed\n"
        "1/2 EDITED x.py (1 replacement)\n"
        "2/2 EDITED y.py (1 replacement)"
    )
    set_done("tool-2", out)
    runs = {r["label"]: r for r in list_runs()}
    assert runs["x.py"]["status"] == "done"
    assert runs["y.py"]["status"] == "done"
    assert "EDITED x.py" in runs["x.py"]["content"]
    assert "EDITED y.py" in runs["y.py"]["content"]


def test_expand_dock_tool_use_for_replay():
    block = {
        "name": "multi_edit",
        "input": {
            "edits": [
                {"path": "one.py", "old_str": "a", "new_str": "b"},
                {"path": "two.py", "old_str": "c", "new_str": "d"},
            ]
        },
    }
    expanded = expand_dock_tool_use(block)
    assert len(expanded) == 2
    assert expanded[0]["_dock_label"] == "one.py"
    assert expanded[1]["_dock_label"] == "two.py"
