"""Headless TUI tests: /command manager modal → prompt-box insertion.

Uses Textual's Pilot harness via ``asyncio.run`` (no pytest-asyncio needed).
External side effects (auto-update, MCP auto-connect, session DB) are stubbed
so the tests stay hermetic.
"""
import asyncio
import pathlib

import pytest


@pytest.fixture()
def hermetic_app(monkeypatch):
    """JarvisTUI with startup side effects stubbed out."""
    monkeypatch.setenv("HARNESS_SKIP_UPDATE", "1")

    import jarvis.updater as updater
    import jarvis.mcp.registry as mcp_registry
    import jarvis.storage.sessions as sessions

    monkeypatch.setattr(updater, "maybe_update_and_reexec", lambda: None)
    monkeypatch.setattr(
        mcp_registry, "auto_connect_servers", lambda console_print=None, **kw: None,
        raising=False,
    )
    monkeypatch.setattr(sessions, "db_init", lambda: None)
    monkeypatch.setattr(sessions, "db_create_session", lambda model: None)

    from jarvis.tui.app import JarvisTUI

    return JarvisTUI


@pytest.fixture()
def temp_commands(tmp_path, monkeypatch):
    """Point command discovery at a temp project with one command."""
    from jarvis.storage import commands as cc
    from jarvis import state

    monkeypatch.setattr(cc, "_find_project_root", lambda: tmp_path)
    monkeypatch.setattr(state, "global_commands", False)
    d = tmp_path / ".harness/commands"
    d.mkdir(parents=True)
    (d / "pr-description.md").write_text(
        "---\ndescription: Generate a PR description\n---\n\n"
        "Write a PR description from the diff. $ARGUMENTS\n",
        encoding="utf-8",
    )
    cc.invalidate_cache()
    yield tmp_path
    cc.invalidate_cache()


def test_modal_enter_inserts_invocation_into_prompt(hermetic_app, temp_commands):
    """Selecting a command places `/<name> ` in the prompt box, ready to edit."""
    async def run() -> None:
        app = hermetic_app()
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause(0.3)
            app._open_command_manager()
            await pilot.pause(0.3)
            await pilot.press("enter")
            await pilot.pause(0.2)
            inp = app.query_one("#prompt")
            assert inp.text == "/pr-description "

    asyncio.run(run())


def test_modal_t_inserts_template_for_editing(hermetic_app, temp_commands):
    """`t` drops the full template text into the prompt box."""
    async def run() -> None:
        app = hermetic_app()
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause(0.3)
            app._open_command_manager()
            await pilot.pause(0.3)
            await pilot.press("t")
            await pilot.pause(0.2)
            inp = app.query_one("#prompt")
            assert inp.text == "Write a PR description from the diff. $ARGUMENTS"

    asyncio.run(run())


def test_modal_delete_requires_double_press(hermetic_app, temp_commands):
    """First `d` arms the delete; second `d` removes the file."""
    cmd_file = temp_commands / ".harness/commands/pr-description.md"

    async def run() -> None:
        app = hermetic_app()
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause(0.3)
            app._open_command_manager()
            await pilot.pause(0.3)
            await pilot.press("d")
            await pilot.pause(0.1)
            assert cmd_file.exists()  # armed, not deleted yet
            await pilot.press("d")
            await pilot.pause(0.2)
            assert not cmd_file.exists()
            await pilot.press("escape")
            await pilot.pause(0.1)

    asyncio.run(run())


def test_editor_creates_command_from_form(hermetic_app, temp_commands):
    """`n` opens the in-app editor; filling name/desc/template and ^s saves."""
    from textual.widgets import Input, TextArea

    async def run() -> None:
        app = hermetic_app()
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause(0.3)
            app._open_command_manager()
            await pilot.pause(0.3)
            await pilot.press("n")
            await pilot.pause(0.3)

            app.screen.query_one("#cmded_name", Input).value = "daily-report"
            app.screen.query_one("#cmded_desc", Input).value = "Summarize today's work"
            app.screen.query_one("#cmded_body", TextArea).text = "Summarize: $ARGUMENTS"
            await pilot.press("ctrl+s")
            await pilot.pause(0.3)

            created = temp_commands / ".harness/commands/daily-report.md"
            assert created.exists()
            content = created.read_text()
            assert "name: daily-report" in content
            assert "description: Summarize today's work" in content
            assert "Summarize: $ARGUMENTS" in content
            await pilot.press("escape")
            await pilot.pause(0.1)

    asyncio.run(run())


def test_editor_edits_existing_command_prefilled(hermetic_app, temp_commands):
    """`e` opens the editor prefilled; saving updates the same file in place."""
    from textual.widgets import Input, TextArea

    cmd_file = temp_commands / ".harness/commands/pr-description.md"

    async def run() -> None:
        app = hermetic_app()
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause(0.3)
            app._open_command_manager()
            await pilot.pause(0.3)
            await pilot.press("e")
            await pilot.pause(0.3)

            name_inp = app.screen.query_one("#cmded_name", Input)
            body_ta = app.screen.query_one("#cmded_body", TextArea)
            assert name_inp.value == "pr-description"
            assert "Write a PR description" in body_ta.text

            app.screen.query_one("#cmded_desc", Input).value = "Updated description"
            body_ta.text = "New template body. $ARGUMENTS"
            await pilot.press("ctrl+s")
            await pilot.pause(0.3)

            content = cmd_file.read_text()
            assert "description: Updated description" in content
            assert "New template body. $ARGUMENTS" in content
            await pilot.press("escape")
            await pilot.pause(0.1)

    asyncio.run(run())


def test_editor_validation_keeps_input_on_screen(hermetic_app, temp_commands):
    """A bad name doesn't dismiss the editor or lose the typed template."""
    from textual.widgets import Input, TextArea
    from jarvis.tui.command_modal import _CommandEditorScreen

    async def run() -> None:
        app = hermetic_app()
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause(0.3)
            app._open_command_manager()
            await pilot.pause(0.3)
            await pilot.press("n")
            await pilot.pause(0.3)

            app.screen.query_one("#cmded_name", Input).value = "Bad Name!"
            app.screen.query_one("#cmded_body", TextArea).text = "keep me"
            await pilot.press("ctrl+s")
            await pilot.pause(0.2)

            assert isinstance(app.screen, _CommandEditorScreen)
            assert app.screen.query_one("#cmded_body", TextArea).text == "keep me"
            await pilot.press("escape")
            await pilot.pause(0.1)
            await pilot.press("escape")
            await pilot.pause(0.1)

    asyncio.run(run())


def test_typing_slash_command_opens_modal(hermetic_app, temp_commands):
    """Bare `/command` submitted from the prompt opens the manager modal."""
    async def run() -> None:
        app = hermetic_app()
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause(0.3)
            inp = app.query_one("#prompt")
            inp.text = "/command"
            await pilot.press("enter")
            await pilot.pause(0.3)
            from jarvis.tui.command_modal import CommandManagerScreen
            assert isinstance(app.screen, CommandManagerScreen)
            await pilot.press("escape")
            await pilot.pause(0.1)

    asyncio.run(run())
