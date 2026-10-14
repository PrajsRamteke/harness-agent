"""Renaming sessions: storage, /rename + /session rename, the web action, the TUI."""
from __future__ import annotations

import pytest

from jarvis import state


@pytest.fixture()
def db(tmp_path, monkeypatch):
    """A private sessions.db with two sessions; the second one is open."""
    import jarvis.storage.sessions as sessions

    monkeypatch.setattr(sessions, "SESSIONS_DB", tmp_path / "sessions.db")
    monkeypatch.setattr(sessions, "CONFIG_DIR", tmp_path)
    for name in ("messages", "current_session_id"):
        monkeypatch.setattr(state, name, getattr(state, name))
    sessions.db_init()
    old = sessions.db_create_session("m")
    sessions.db_append_message(old, 0, {"role": "user", "content": "fix the login bug"})
    sessions.db_set_title_if_empty(old, "fix the login bug")
    cur = sessions.db_create_session("m")
    state.current_session_id = cur
    state.messages = []
    return sessions, old, cur


def test_rename_sets_the_title_and_the_first_message_keeps_away(db):
    sessions, old, cur = db
    assert sessions.db_get_title(old) == "fix the login bug"
    assert sessions.db_rename_session(old, "  Login\n bug   hunt ") is True
    assert sessions.db_get_title(old) == "Login bug hunt"  # one line, collapsed

    # Renamed before its first message: the message doesn't overwrite the name.
    assert sessions.db_rename_session(cur, "Planning")
    sessions.db_set_title_if_empty(cur, "first message text")
    assert sessions.db_get_title(cur) == "Planning"
    rows = {r["id"]: r["title"] for r in sessions.db_list_sessions()}
    assert rows[old] == "Login bug hunt"


def test_rename_refuses_blank_and_unknown(db):
    sessions, old, _cur = db
    with pytest.raises(ValueError):
        sessions.db_rename_session(old, "   ")
    assert sessions.db_rename_session(99999, "x") is False
    long = "x" * 300
    sessions.db_rename_session(old, long)
    assert len(sessions.db_get_title(old)) == 80


def test_title_cache_sees_a_rename_from_another_process(db, monkeypatch):
    sessions, old, _cur = db
    assert sessions.db_get_title(old) == "fix the login bug"
    with sessions.db_conn() as c:  # another Jarvis renames it
        c.execute("UPDATE sessions SET title='elsewhere' WHERE id=?", (old,))
    clock = [sessions.time.monotonic()]
    monkeypatch.setattr(sessions.time, "monotonic", lambda: clock[0])
    clock[0] += sessions._TITLE_TTL + 1
    assert sessions.db_get_title(old) == "elsewhere"


def test_parse_rename():
    from jarvis.commands.session import parse_rename

    assert parse_rename("New title") == (None, "New title")
    assert parse_rename("#12 Old one") == (12, "Old one")
    assert parse_rename("#12") == (12, "")
    assert parse_rename("2024 plans") == (None, "2024 plans")
    assert parse_rename("") == (None, "")


def test_slash_rename_current_and_past(db):
    from jarvis.commands.dispatch import handle_slash

    sessions, old, cur = db
    assert handle_slash("/rename Sprint notes")[0] == "ok"
    assert sessions.db_get_title(cur) == "Sprint notes"
    assert handle_slash(f"/session rename #{old} Auth fixes")[0] == "ok"
    assert sessions.db_get_title(old) == "Auth fixes"


def test_web_action_session_rename(db):
    from jarvis.web.actions_api import run_web_action

    sessions, old, cur = db
    out = run_web_action("session_rename", {"title": "Web named"}, console_print=lambda *a: None)
    assert out == {"ok": True, "session_id": cur, "title": "Web named"}
    out = run_web_action("session_rename", {"session_id": old, "title": "Past"}, console_print=lambda *a: None)
    assert out["ok"] and sessions.db_get_title(old) == "Past"
    bad = run_web_action("session_rename", {"session_id": old, "title": " "}, console_print=lambda *a: None)
    assert bad["ok"] is False and "empty" in bad["error"]
    gone = run_web_action("session_rename", {"session_id": 4242, "title": "x"}, console_print=lambda *a: None)
    assert gone["ok"] is False and "not found" in gone["error"]


def test_web_state_and_session_list_show_the_new_title(db):
    from jarvis.web.pickers_api import list_sessions
    from jarvis.web.state_api import _session_title

    sessions, old, cur = db
    sessions.db_rename_session(cur, "Shown on the web")
    assert _session_title(cur) == "Shown on the web"
    rows = {r["id"]: r for r in list_sessions()["sessions"]}
    assert rows[old]["title"] == "fix the login bug" and rows[old]["auto_title"] is False


def test_sidebar_title_prefers_the_saved_name(db):
    from jarvis.tui.sidebar import session_title

    sessions, _old, cur = db
    state.messages = [{"role": "user", "content": "what the first message said"}]
    assert session_title() == "what the first message said"
    sessions.db_rename_session(cur, "Renamed")
    assert session_title() == "Renamed"


def test_rename_command_sniffer():
    from jarvis.tui.app_commands import _is_rename_command

    assert _is_rename_command("/rename")
    assert _is_rename_command("/rename New name")
    assert _is_rename_command("/session rename #3 x")
    assert not _is_rename_command("/session")
    assert not _is_rename_command("/renamed")
    assert not _is_rename_command("rename this")


@pytest.fixture()
def tui_app(db, monkeypatch):
    monkeypatch.setenv("HARNESS_SKIP_UPDATE", "1")
    import jarvis.updater as updater
    import jarvis.mcp.registry as mcp_registry
    import jarvis.storage.settings as settings
    import jarvis.tui.prompt_history as prompt_history

    sessions, _old, cur = db
    monkeypatch.setattr(updater, "maybe_update_and_reexec", lambda: None)
    monkeypatch.setattr(mcp_registry, "auto_connect_servers", lambda console_print=None, **kw: None,
                        raising=False)
    monkeypatch.setattr(sessions, "db_init", lambda: None)
    monkeypatch.setattr(sessions, "db_create_session", lambda model: cur)
    monkeypatch.setattr(settings.Settings, "save", lambda self: None)
    monkeypatch.setattr(prompt_history.PromptHistory, "_save", lambda self: None)
    from jarvis.tui.app import JarvisTUI

    monkeypatch.setattr(JarvisTUI, "_warm_model_catalogs_background", lambda self: None)
    return JarvisTUI


def test_tui_session_picker_ctrl_r_renames_the_highlighted_row(db, tui_app):
    import asyncio
    from jarvis.tui.session_modal import SessionPickerScreen
    from jarvis.tui.text_input_modal import TextInputScreen

    sessions, old, _cur = db

    async def run() -> None:
        app = tui_app()
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause(0.3)
            app.push_screen(SessionPickerScreen())
            await pilot.pause(0.3)
            picker = app.screen
            assert picker._current_id() == old  # the only session with messages
            await pilot.press("ctrl+r")
            await pilot.pause(0.2)
            assert isinstance(app.screen, TextInputScreen)
            field = app.screen.query_one("#text_input")
            assert field.value == "fix the login bug"  # prefilled
            field.value = "Login bug hunt"
            await pilot.press("enter")
            await pilot.pause(0.2)
            assert app.screen is picker
            assert sessions.db_get_title(old) == "Login bug hunt"
            assert [r["title"] for r in picker._rows if r["id"] == old] == ["Login bug hunt"]
            assert picker._current_id() == old  # the cursor stays on the renamed row

    asyncio.run(run())


def test_tui_rename_command_with_and_without_a_title(db, tui_app):
    import asyncio
    from jarvis.tui.text_input_modal import TextInputScreen

    sessions, _old, cur = db

    async def run() -> None:
        app = tui_app()
        async with app.run_test(size=(140, 40)) as pilot:
            await pilot.pause(0.3)
            assert state.current_session_id == cur
            assert app._open_modal_for("/rename Release prep")
            await pilot.pause(0.1)
            assert sessions.db_get_title(cur) == "Release prep"

            assert app._open_modal_for("/rename")  # no title → the dialog
            await pilot.pause(0.2)
            assert isinstance(app.screen, TextInputScreen)
            assert app.screen.query_one("#text_input").value == "Release prep"
            await pilot.press("escape")  # cancel keeps the name
            await pilot.pause(0.1)
            assert sessions.db_get_title(cur) == "Release prep"

    asyncio.run(run())


def test_tui_sidebar_title_click_opens_rename(db, tui_app):
    import asyncio
    from jarvis.tui.sidebar import Sidebar, SidebarBody
    from jarvis.tui.text_input_modal import TextInputScreen

    sessions, _old, cur = db
    sessions.db_rename_session(cur, "Sidebar named")

    async def run() -> None:
        app = tui_app()
        async with app.run_test(size=(160, 44)) as pilot:
            await pilot.pause(0.3)
            sb = app.query_one("#sidebar", Sidebar)
            if sb.has_class("hidden"):
                app.action_toggle_sidebar()
                await pilot.pause(0.2)
            body = app.query_one(SidebarBody)
            assert body.render().plain.startswith("Sidebar named ✎")
            await pilot.click(SidebarBody, offset=(2, 0))
            await pilot.pause(0.2)
            assert isinstance(app.screen, TextInputScreen)
            app.screen.query_one("#text_input").value = "Clicked rename"
            await pilot.press("enter")
            await pilot.pause(0.2)
            assert sessions.db_get_title(cur) == "Clicked rename"
            assert body.render().plain.startswith("Clicked rename ✎")

    asyncio.run(run())
