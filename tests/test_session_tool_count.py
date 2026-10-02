"""A resumed session keeps its tool-call count (sidebars, /stats, web).

The counter used to be reset to 0 on resume while tokens were re-estimated
from the history, so an old session with tool calls read "0 tool calls".
"""
from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from jarvis import state


def _history() -> list[dict]:
    return [
        {"role": "user", "content": "check the repo"},
        {"role": "assistant", "content": [
            {"type": "text", "text": "Looking."},
            {"type": "tool_use", "id": "t1", "name": "run_bash", "input": {"command": "ls"}},
            {"type": "tool_use", "id": "t2", "name": "read_file", "input": {"path": "a.py"}},
        ]},
        {"role": "user", "content": [
            {"type": "tool_result", "tool_use_id": "t1", "content": "a.py"},
            {"type": "tool_result", "tool_use_id": "t2", "content": "print(1)"},
        ]},
        {"role": "assistant", "content": [
            {"type": "tool_use", "id": "t3", "name": "run_bash", "input": {"command": "pytest"}},
        ]},
        {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "t3", "content": "ok"}]},
        {"role": "assistant", "content": [{"type": "text", "text": "All good."}]},
    ]


@pytest.fixture()
def db(tmp_path, monkeypatch):
    """A private sessions.db holding one saved session with 3 tool calls."""
    import jarvis.storage.sessions as sessions

    monkeypatch.setattr(sessions, "SESSIONS_DB", tmp_path / "sessions.db")
    monkeypatch.setattr(sessions, "CONFIG_DIR", tmp_path)
    for name in ("messages", "current_session_id", "tool_calls_count", "total_in", "total_out", "total_tokens"):
        monkeypatch.setattr(state, name, getattr(state, name))
    sessions.db_init()
    sid = sessions.db_create_session("test-model")
    sessions.db_replace_session_messages(sid, _history())
    # A different, live session is open with its own count.
    state.messages = []
    state.tool_calls_count = 7
    return SimpleNamespace(sid=sid, sessions=sessions)


def test_count_tool_calls_counts_tool_use_blocks_only():
    from jarvis.repl.trim import count_tool_calls

    assert count_tool_calls(_history()) == 3
    assert count_tool_calls([]) == 0 and count_tool_calls(None) == 0
    # Plain-text turns and tool_result blocks never count.
    assert count_tool_calls([{"role": "user", "content": [{"type": "tool_use"}]},
                             {"role": "assistant", "content": "just text"}]) == 0
    # Blocks still held as SDK objects (not yet serialised) count too.
    sdk_block = SimpleNamespace(type="tool_use", name="run_bash")
    assert count_tool_calls([{"role": "assistant", "content": [sdk_block, SimpleNamespace(type="text")]}]) == 1


def test_terminal_and_web_resume_restore_the_count(db):
    from jarvis.commands.control import session_stats
    from jarvis.tui.session_modal import resume_session_into_state
    from jarvis.web.state_api import state_fields

    assert resume_session_into_state(db.sid, lambda *a, **k: None, preview=False, quiet=True)
    assert state.tool_calls_count == 3
    assert session_stats()["tool_calls"] == 3          # /stats
    assert state_fields()["tool_calls"] == 3           # web sidebar

    # Calls made after resuming add on top, like any live session.
    state.tool_calls_count += 1
    assert session_stats()["tool_calls"] == 4


def test_web_session_actions(db):
    from jarvis.web.actions_api import run_web_action

    res = run_web_action("session_resume", {"session_id": db.sid}, console_print=lambda *a, **k: None)
    assert res["ok"] and state.tool_calls_count == 3
    # A new chat still starts from zero.
    res = run_web_action("session_new", {}, console_print=lambda *a, **k: None)
    assert res["ok"] and state.tool_calls_count == 0


def test_legacy_session_resume_command(db, monkeypatch):
    import jarvis.commands.session as session_cmd

    monkeypatch.setattr(session_cmd, "console", SimpleNamespace(print=lambda *a, **k: None))
    assert session_cmd._resume_session(db.sid) == db.sid
    assert state.tool_calls_count == 3


def test_load_from_file_counts_its_tool_calls(db, tmp_path, monkeypatch):
    import jarvis.commands.files_shell as fs

    monkeypatch.setattr(fs, "console", SimpleNamespace(print=lambda *a, **k: None))
    path = tmp_path / "chat.json"
    path.write_text(json.dumps(_history()))
    assert fs.handle_files_shell("/load", str(path))
    assert state.tool_calls_count == 3


def test_resuming_a_session_without_tools_reads_zero(db):
    from jarvis.tui.session_modal import resume_session_into_state

    sid = db.sessions.db_create_session("test-model")
    db.sessions.db_replace_session_messages(sid, [
        {"role": "user", "content": "hi"},
        {"role": "assistant", "content": [{"type": "text", "text": "hello"}]},
    ])
    assert resume_session_into_state(sid, lambda *a, **k: None, preview=False, quiet=True)
    assert state.tool_calls_count == 0
