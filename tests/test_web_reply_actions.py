"""Web reply actions (Retry, Branch into new chat) and tool timing ("Worked for 4.7s")."""
from __future__ import annotations

from jarvis import state
from jarvis.web import branch_api
from jarvis.web import console_mux
from jarvis.web.state_api import snapshot_messages


def _chat():
    return [
        {"role": "user", "content": "find the title"},
        {"role": "assistant", "content": [
            {"type": "text", "text": "Let me look."},
            {"type": "tool_use", "id": "a1", "name": "grep", "input": {"pattern": "x"}},
        ]},
        {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "a1", "content": "hit"}]},
        {"role": "assistant", "content": [{"type": "text", "text": "Found it in sidebar.js."}]},
        {"role": "user", "content": "now fix it"},
        {"role": "assistant", "content": [{"type": "text", "text": "Done.  Fixed\nit."}]},
    ]


def test_branch_keeps_the_conversation_up_to_that_reply():
    msgs = _chat()
    out = branch_api.branch_messages(msgs, "Found it in sidebar.js.")
    assert out == msgs[:4]
    assert out is not msgs and out[0] is not msgs[0]  # a copy, never the live history


def test_branch_at_a_reply_followed_by_a_tool_call_drops_the_dangling_call():
    out = branch_api.branch_messages(_chat(), "Let me look.")
    assert len(out) == 2
    assert out[-1]["content"] == [{"type": "text", "text": "Let me look."}]


def test_reply_is_found_by_text_whitespace_and_occurrence():
    msgs = _chat() + [
        {"role": "user", "content": "again"},
        {"role": "assistant", "content": [{"type": "text", "text": "Done. Fixed it."}]},
    ]
    assert branch_api.find_reply(msgs, "Done. Fixed it.") == (5, 0)
    assert branch_api.find_reply(msgs, "Done. Fixed it.", nth=1) == (7, 0)
    assert branch_api.find_reply(msgs, "never said") is None


def test_retry_cuts_back_to_the_last_prompt():
    out = branch_api.retry_messages(_chat())
    assert out[-1] == {"role": "user", "content": "now fix it"}
    assert len(out) == 5
    # tool results are not prompts
    assert not branch_api.is_prompt(_chat()[2])


def test_unanswered_prompt_is_only_a_last_prompt_with_no_reply():
    assert branch_api.unanswered_prompt(_chat()) is None
    stopped = _chat() + [{"role": "user", "content": "and the footer"}]
    assert branch_api.unanswered_prompt(stopped) == len(stopped) - 1
    mid_tool = _chat()[:3]  # stopped after a tool ran: tool results are not a prompt
    assert branch_api.unanswered_prompt(mid_tool) is None
    assert branch_api.unanswered_prompt([]) is None


def test_prompt_unsent_edit_takes_the_prompt_back_and_resend_says_retry(monkeypatch):
    monkeypatch.setattr(state, "current_session_id", None)
    monkeypatch.setattr(state, "messages", _chat() + [{"role": "user", "content": "and the footer"}])

    assert branch_api.run("prompt_unsent", {"op": "resend"}, console_print=print) == {"ok": True, "retry": True}
    res = branch_api.run("prompt_unsent", {"op": "edit"}, console_print=print)
    assert res["ok"] and res["removed"]
    assert state.messages == _chat()
    # Nothing unanswered any more: no cut, and Send again sends the text anew.
    assert branch_api.run("prompt_unsent", {"op": "edit"}, console_print=print) == {"ok": True, "removed": False}
    assert state.messages == _chat()
    assert branch_api.run("prompt_unsent", {"op": "resend"}, console_print=print) == {"ok": True, "retry": False}
    assert not branch_api.run("prompt_unsent", {"op": "nope"}, console_print=print)["ok"]


def test_reply_actions_through_run_web_action(monkeypatch, tmp_path):
    from jarvis.storage import sessions
    from jarvis.web.actions_api import run_web_action

    monkeypatch.setattr(sessions, "SESSIONS_DB", tmp_path / "s.db")
    sessions.db_init()
    sid = sessions.db_create_session("m")
    monkeypatch.setattr(state, "messages", _chat())
    monkeypatch.setattr(state, "current_session_id", sid)

    res = run_web_action("reply_branch", {"text": "Found it in sidebar.js."}, console_print=lambda *a: None)
    assert res["ok"] and res["session_id"] != sid
    assert len(sessions.db_load_session(res["session_id"])) == 4
    assert state.current_session_id == sid  # the page resumes it itself

    # Retry is for the latest reply only
    old = run_web_action("reply_retry", {"text": "Found it in sidebar.js."}, console_print=lambda *a: None)
    assert not old["ok"] and "latest" in old["error"]
    res = run_web_action("reply_retry", {"text": "Done. Fixed it."}, console_print=lambda *a: None)
    assert res["ok"]
    assert state.messages[-1] == {"role": "user", "content": "now fix it"}
    assert len(sessions.db_load_session(sid)) == 5

    from jarvis.repl.api_errors import can_retry
    assert can_retry()[0]  # /retry now resends the prompt


def test_tool_times_ride_on_events_and_snapshots(monkeypatch):
    monkeypatch.setattr(console_mux, "_TOOL_TIMES", {})
    start = console_mux.note_tool_time("tool_start", "a1")
    assert set(start) == {"t0"}
    done = console_mux.note_tool_time("tool_done", "a1")
    assert done["t0"] == start["t0"] and done["t1"] >= done["t0"]
    assert console_mux.note_tool_time("tool_done", "unknown") == {}

    monkeypatch.setattr(state, "messages", _chat())
    monkeypatch.setattr(state, "show_internal", False)
    row = next(e for e in snapshot_messages() if e["role"] == "tool")
    assert row["t0"] == done["t0"] and row["t1"] == done["t1"]


def test_tool_times_forget_the_oldest(monkeypatch):
    monkeypatch.setattr(console_mux, "_TOOL_TIMES", {})
    monkeypatch.setattr(console_mux, "_TOOL_TIMES_MAX", 3)
    for i in range(5):
        console_mux.note_tool_time("tool_start", f"t{i}")
    assert list(console_mux._TOOL_TIMES) == ["t2", "t3", "t4"]
