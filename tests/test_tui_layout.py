"""Headless layout checks: dialog chrome, live theme switches, activity row."""
import asyncio

import pytest


@pytest.fixture()
def hermetic_app(monkeypatch):
    monkeypatch.setenv("HARNESS_SKIP_UPDATE", "1")

    import jarvis.updater as updater
    import jarvis.mcp.registry as mcp_registry
    import jarvis.storage.sessions as sessions
    import jarvis.storage.settings as settings
    import jarvis.tui.prompt_history as prompt_history
    from jarvis import state

    monkeypatch.setattr(updater, "maybe_update_and_reexec", lambda: None)
    monkeypatch.setattr(mcp_registry, "auto_connect_servers", lambda console_print=None, **kw: None,
                        raising=False)
    monkeypatch.setattr(sessions, "db_init", lambda: None)
    monkeypatch.setattr(sessions, "db_create_session", lambda model: None)
    monkeypatch.setattr(settings.Settings, "save", lambda self: None)
    monkeypatch.setattr(prompt_history.PromptHistory, "_save", lambda self: None)
    monkeypatch.setattr(state, "save_trace_config", lambda: None)

    from jarvis.tui.app import JarvisTUI

    monkeypatch.setattr(JarvisTUI, "_warm_model_catalogs_background", lambda self: None)
    return JarvisTUI


@pytest.fixture()
def model_picker(monkeypatch):
    import jarvis.tui.model_modal as mm
    from jarvis.constants import PROVIDER_HARNESS_AGENT

    rows = [(PROVIDER_HARNESS_AGENT, "free-a", "Free A"), (PROVIDER_HARNESS_AGENT, "free-b", "Free B")]
    monkeypatch.setattr(mm, "model_picker_rows", lambda live=False: rows)
    monkeypatch.setattr(mm.ModelPickerScreen, "_refresh_catalogs", lambda self: None)
    monkeypatch.setattr(mm, "_recent_models", lambda: [])
    return mm.ModelPickerScreen


def _hex(color) -> str:
    return color.hex.lower()


def test_dialog_chrome_applies_at_startup(hermetic_app, model_picker):
    """Shared chrome must style dialogs without a /theme switch first
    (scoped DEFAULT_CSS used to turn `.tui-modal-screen #modal` into a
    selector that never matched)."""
    from jarvis.tui import theme as ui

    async def run() -> None:
        app = hermetic_app()
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause(0.2)
            app.push_screen(model_picker())
            await pilot.pause(0.4)
            screen = app.screen
            frame = screen.query_one("#modal")
            assert _hex(frame.styles.background) == ui.BG_1.lower()
            assert frame.styles.padding.left == 3
            assert screen.query_one("#modal_title").styles.text_style.bold
            assert screen.query_one("#model_search").styles.padding.left == 1
            # The dialog's own `height: 85%` still wins over the chrome's `height: auto`.
            assert not frame.styles.height.is_auto and frame.styles.height.value == 85

    asyncio.run(run())


def test_theme_switch_recolors_open_dialog_and_keeps_its_size(hermetic_app, model_picker,
                                                              monkeypatch):
    from jarvis import state
    from jarvis.tui import theme as ui

    before = ui.active_theme()
    target = next(n for n in ui.theme_names() if n != before)
    monkeypatch.setattr(state, "theme", state.theme)
    monkeypatch.setattr(state, "theme_colors", dict(getattr(state, "theme_colors", {}) or {}))

    async def run() -> None:
        app = hermetic_app()
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause(0.2)
            app.push_screen(model_picker())
            await pilot.pause(0.4)
            app._apply_theme_runtime(target, rebuild_transcript=False)
            await pilot.pause(0.3)
            frame = app.screen.query_one("#modal")
            assert _hex(frame.styles.background) == ui.PALETTES[target]["bg_1"].lower()
            assert not frame.styles.height.is_auto and frame.styles.height.value == 85

    try:
        asyncio.run(run())
    finally:
        ui.set_theme(before)


def test_activity_row_collapses_when_idle(hermetic_app):
    async def run() -> None:
        app = hermetic_app()
        async with app.run_test(size=(100, 30)) as pilot:
            await pilot.pause(0.2)
            row = app.query_one("#activity")
            assert row.has_class("-idle") and not row.display

            app._busy = True
            app._activity_label = "Thinking"
            app._refresh_activity_widgets()
            await pilot.pause()
            assert row.display and row.region.height == 1
            # One blank row between the activity line and the composer, and
            # between the composer and the footer.
            composer = app.query_one("#composer")
            assert composer.region.y - row.region.bottom == 1
            assert app.query_one("#footer").region.y - composer.region.bottom == 1

            app._busy = False
            app._activity_label = ""
            app._refresh_activity_widgets()
            await pilot.pause()
            assert not row.display

    asyncio.run(run())
