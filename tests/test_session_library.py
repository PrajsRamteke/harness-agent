"""Session management: project folders, pin / archive, search, export, the web list."""
import json
import os
import sqlite3

import pytest

from jarvis import state
from jarvis.storage import session_library as lib
from jarvis.storage import sessions


@pytest.fixture
def db(tmp_path, monkeypatch):
    monkeypatch.setattr(sessions, "SESSIONS_DB", tmp_path / "sessions.db")
    monkeypatch.setattr(state, "current_session_id", None, raising=False)
    return tmp_path


def _chat(text, *, cwd, reply="ok", title=None):
    sid = sessions.db_create_session("m", cwd=str(cwd))
    sessions.db_append_message(sid, 0, {"role": "user", "content": text})
    sessions.db_append_message(sid, 1, {"role": "assistant", "content": [{"type": "text", "text": reply}]})
    sessions.db_set_title_if_empty(sid, title or text)
    return sid


def test_old_database_gets_the_new_columns(db):
    c = sqlite3.connect(str(db / "sessions.db"))
    c.executescript("""
        CREATE TABLE sessions (id INTEGER PRIMARY KEY AUTOINCREMENT, title TEXT, model TEXT,
                               created_at REAL, updated_at REAL);
        CREATE TABLE messages (id INTEGER PRIMARY KEY AUTOINCREMENT, session_id INTEGER, idx INTEGER,
                               role TEXT, content_json TEXT, created_at REAL);
        INSERT INTO sessions (title, model, created_at, updated_at) VALUES ('old', 'm', 1, 1);
        INSERT INTO messages (session_id, idx, role, content_json, created_at) VALUES (1, 0, 'user', '"hi"', 1);
    """)
    c.commit()
    c.close()
    rows = sessions.db_list_sessions()
    assert [r["title"] for r in rows] == ["old"]
    assert rows[0]["cwd"] == "" and rows[0]["pinned"] == 0 and rows[0]["archived"] == 0


def test_new_session_records_the_folder_jarvis_runs_in(db, monkeypatch):
    monkeypatch.chdir(db)
    sid = sessions.db_create_session("m")
    assert sessions.db_session_info(sid)["cwd"] == os.getcwd()


def test_filters_by_folder_pin_and_archive(db):
    a1 = _chat("alpha one", cwd="/w/alpha")
    a2 = _chat("alpha two", cwd="/w/alpha")
    b1 = _chat("beta one", cwd="/w/beta")
    assert {r["id"] for r in sessions.db_list_sessions(cwd="/w/alpha")} == {a1, a2}
    assert sessions.db_count_sessions(cwd="/w/beta") == 1

    assert sessions.db_set_pinned([a1], True) == 1
    first = sessions.db_list_sessions(pinned_first=True)[0]
    assert first["id"] == a1

    sessions.db_set_archived([a1, b1], True)
    assert {r["id"] for r in sessions.db_list_sessions()} == {a2}
    assert {r["id"] for r in sessions.db_list_sessions(archived=True)} == {a1, b1}
    assert sessions.db_session_info(a1)["pinned"] == 0  # archiving unpins

    folders = {f["cwd"]: f for f in sessions.db_session_folders()}
    assert folders["/w/alpha"]["count"] == 1 and folders["/w/alpha"]["archived"] == 1


def test_search_matches_message_text_with_a_snippet(db):
    _chat("fix the login page", cwd="/w/a", reply="The rate limit kicks in after five tries.")
    other = _chat("unrelated", cwd="/w/b")
    quoted = _chat('say "héllo wörld" please', cwd="/w/a")
    ids = sessions.db_search_session_ids("RATE LIMIT")
    assert len(ids) == 1 and other not in ids
    snip = lib.snippet(ids[0], "rate limit")
    assert snip["kind"] == "jarvis"
    assert snip["text"][snip["at"]:snip["at"] + snip["len"]].lower() == "rate limit"
    # Non-ASCII and quotes are stored JSON-escaped: still found.
    assert sessions.db_search_session_ids("héllo wörld") == [quoted]
    assert sessions.db_search_session_ids(f"#{other}") == [other]
    assert sessions.db_search_session_ids("100%") == []  # % is literal


def test_backfill_guesses_the_git_root_of_old_chats(db, monkeypatch):
    home = db / "home"
    proj = home / "code" / "shop"
    (proj / ".git").mkdir(parents=True)
    (proj / "src").mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setattr(lib, "_ABS_PATH", __import__("re").compile(r"(%s/[^\"\s]+)" % __import__("re").escape(str(home))))
    sid = sessions.db_create_session("m", cwd="")
    sessions.db_append_message(sid, 0, {"role": "user", "content": "look"})
    sessions.db_append_message(sid, 1, {"role": "assistant", "content": [
        {"type": "tool_use", "id": "t1", "name": "read_file", "input": {"path": f"{proj}/src/app.py"}}]})
    lost = _chat("no paths here", cwd="")
    assert lib.backfill_folders() == 1
    assert sessions.db_session_info(sid)["cwd"] == str(proj)
    assert sessions.db_session_info(sid)["cwd_guessed"] == 1
    assert sessions.db_session_info(lost)["cwd"] == ""
    assert lib.backfill_folders() == 0  # once per database


def test_markdown_export(db):
    sid = _chat("explain tabs", cwd="/w/a", reply="Tabs hold sessions.")
    md = lib.markdown(sid)
    assert md.startswith("# explain tabs")
    assert "## You" in md and "Tabs hold sessions." in md
    assert lib.export_name(sid) == "explain-tabs.md"
    assert lib.markdown(99999) is None


def test_web_list_scopes_views_and_folders(db, monkeypatch):
    from jarvis.web.pickers_api import list_sessions

    monkeypatch.chdir(db)
    here = os.getcwd()
    mine = _chat("here chat", cwd=here)
    _chat("elsewhere chat", cwd="/w/else")
    gone = _chat("archived chat", cwd=here)
    sessions.db_set_archived([gone], True)

    res = list_sessions(scope="project", folders=True)
    assert [s["id"] for s in res["sessions"]] == [mine]
    assert res["sessions"][0]["here"] is True
    assert res["counts"] == {"all": 2, "pinned": 0, "archived": 1, "project": 1}
    current = [f for f in res["folders"] if f["current"]]
    assert current and current[0]["cwd"] == here

    res = list_sessions(scope="all", view="archived")
    assert [s["id"] for s in res["sessions"]] == [gone]

    res = list_sessions(scope="all", q="elsewhere")
    assert res["sessions"][0]["project"] == "else"
    assert res["sessions"][0]["snippet"]["kind"] in ("you", "jarvis")


def test_actions_pin_archive_move_and_bulk_delete(db, monkeypatch):
    from jarvis.web.actions_api import run_web_action

    monkeypatch.chdir(db)
    a = _chat("a", cwd="/w/x")
    b = _chat("b", cwd="/w/x")
    say = lambda *_a, **_k: None
    assert run_web_action("session_pin", {"session_id": a, "pinned": True}, console_print=say)["ok"]
    assert sessions.db_session_info(a)["pinned"] == 1
    assert run_web_action("session_archive", {"session_ids": [a, b], "archived": True}, console_print=say)["changed"] == 2
    moved = run_web_action("session_move", {"session_id": a}, console_print=say)
    assert moved["ok"] and sessions.db_session_info(a)["cwd"] == os.getcwd()
    monkeypatch.setattr(state, "current_session_id", b, raising=False)
    res = run_web_action("session_delete", {"session_ids": [a, b]}, console_print=say)
    assert res["deleted"] == [a] and res["skipped"] == [b]
    # The open chat can't be archived.
    assert not run_web_action("session_archive", {"session_id": b}, console_print=say)["ok"]


def test_terminal_picker_scope_search_and_pin(db, monkeypatch):
    """/session in the TUI: this project first, Tab → all, message search, ^p pins."""
    import asyncio

    monkeypatch.chdir(db)
    here = os.getcwd()
    mine = _chat("tidy the sidebar", cwd=here, reply="Moved the toggles down.")
    other = _chat("billing refactor", cwd="/w/billing", reply="The webhook now retries with backoff.")

    monkeypatch.setenv("HARNESS_SKIP_UPDATE", "1")
    import jarvis.mcp.registry as mcp_registry
    import jarvis.updater as updater
    import jarvis.tui.session_modal as sm

    monkeypatch.setattr(updater, "maybe_update_and_reexec", lambda: None)
    monkeypatch.setattr(mcp_registry, "auto_connect_servers", lambda console_print=None, **kw: None, raising=False)
    monkeypatch.setattr(sessions, "db_init", lambda: None)
    monkeypatch.setattr(sessions, "db_create_session", lambda model, cwd=None: None)
    from jarvis.tui.app import JarvisTUI

    def ids(opts):
        return [opts.get_option_at_index(i).id for i in range(opts.option_count)
                if not opts.get_option_at_index(i).disabled]

    async def run() -> None:
        app = JarvisTUI()
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause(0.3)
            app.push_screen(sm.SessionPickerScreen())
            await pilot.pause(0.3)
            screen = app.screen
            opts = screen.query_one("#session_list")
            assert ids(opts) == [str(mine)]           # this project only
            await pilot.press("tab")
            await pilot.pause(0.1)
            assert set(ids(opts)) == {str(mine), str(other)}
            for ch in "backoff":                        # only in a reply, not a title
                await pilot.press(ch)
            for _ in range(20):
                await pilot.pause(0.05)
                if ids(opts) == [str(other)]:
                    break
            assert ids(opts) == [str(other)]
            await pilot.press("ctrl+p")
            await pilot.pause(0.1)
            assert sessions.db_session_info(other)["pinned"] == 1
            app.save_screenshot(str(db / "picker.svg"))

    asyncio.run(run())
