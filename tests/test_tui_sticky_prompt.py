"""Sticky prompt: your message stays pinned above a long reply.

Headless Pilot tests (``asyncio.run``, no pytest-asyncio); startup side
effects are stubbed like the other TUI tests.
"""
import asyncio
import time

import pytest

_LONG = "\n\n".join(f"Paragraph {i}: " + "lorem ipsum dolor sit amet " * 6 for i in range(30))
_PROMPTS = (
    "fix the stall retry in stream.py and add a test for it",
    "now explain how the anchor works\nand why it jumps\nthird line here",
    "!git status",
)


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
    # Don't read the user's settings.json for ui.sticky_prompt.
    monkeypatch.setattr(JarvisTUI, "_load_sticky_pref", lambda self: None)
    return JarvisTUI


async def _conversation(app, pilot):
    from jarvis.tui.transcript import AssistantBlock, TurnFooter, UserBlock

    t = app.query_one("#transcript")
    for p in _PROMPTS:
        t.add(UserBlock(p, shell=p.startswith("!")))
        t.add(AssistantBlock(_LONG))
        t.add(TurnFooter("coding", "#8888ff", "sonnet", 3.2))
    await pilot.pause(0.3)
    return t, app.query_one("#sticky_prompt")


def test_prompt_spans_match_compositor(hermetic_app):
    async def run() -> None:
        app = hermetic_app()
        async with app.run_test(size=(110, 34)) as pilot:
            await pilot.pause(0.2)
            t, _bar = await _conversation(app, pilot)
            spans = t.prompt_spans()
            assert [b.text for b, _y, _bt in spans] == list(_PROMPTS)
            for block, y, bottom in spans:
                vr = block.virtual_region
                assert (vr.y, vr.bottom) == (y, bottom)

    asyncio.run(run())


def test_sticky_follows_the_turn_at_the_top(hermetic_app):
    async def run() -> None:
        app = hermetic_app()
        async with app.run_test(size=(110, 34)) as pilot:
            await pilot.pause(0.2)
            t, bar = await _conversation(app, pilot)

            # Following the end of a long reply: its prompt is pinned.
            assert t.following and bar.shown
            assert (bar.index, bar.total) == (2, 3)
            assert bar.plain_text().startswith("!git status")
            assert bar.has_class("-shell")
            # Sits exactly over the user boxes, clear of the scrollbar.
            user = t.prompt_spans()[0][0]
            assert (bar.region.x, bar.region.width) == (user.region.x, user.region.width)

            # Reading reply 2: the sticky switches to prompt 2 (folded to one row).
            y2 = t.prompt_spans()[1][1]
            t.scroll_to(y=y2 + 20, animate=False)
            await pilot.pause(0.1)
            assert bar.shown and bar.index == 1
            assert bar.plain_text().startswith("now explain how the anchor works and why it jumps")
            assert bar.region.height == 3  # padded like the user box: blank · text · blank

            # Its prompt back in view → no sticky (never a duplicate).
            t.scroll_to(y=y2, animate=False)
            await pilot.pause(0.1)
            assert not bar.shown

            t.clear()
            await pilot.pause(0.1)
            app._sync_sticky_prompt()
            assert not bar.shown

    asyncio.run(run())


def test_alt_arrows_step_between_prompts(hermetic_app):
    async def run() -> None:
        app = hermetic_app()
        async with app.run_test(size=(110, 34)) as pilot:
            await pilot.pause(0.2)
            t, bar = await _conversation(app, pilot)
            ys = [y for _b, y, _bt in t.prompt_spans()]

            # First ↑ inside a reply lands on its own prompt, then earlier ones.
            for want in (ys[2], ys[1], ys[0]):
                await pilot.press("alt+up")
                await pilot.pause(0.4)
                assert t.scroll_y == want
                assert not bar.shown
            await pilot.press("alt+up")
            await pilot.pause(0.1)
            assert "no earlier prompt" in app._status_msg

            await pilot.press("alt+down")
            await pilot.pause(0.4)
            assert t.scroll_y == ys[1] and not t.following
            await pilot.press("alt+down")
            await pilot.pause(0.4)
            await pilot.press("alt+down")  # past the last prompt → live end
            await pilot.pause(0.4)
            assert t.following and t.is_vertical_scroll_end

    asyncio.run(run())


def test_sticky_clicks_jump_step_and_copy(hermetic_app, monkeypatch):
    copied: list[str] = []

    async def run() -> None:
        app = hermetic_app()
        monkeypatch.setattr(app, "_copy_text", lambda text: copied.append(text) or True)
        async with app.run_test(size=(110, 34)) as pilot:
            await pilot.pause(0.2)
            t, bar = await _conversation(app, pilot)
            ys = [y for _b, y, _bt in t.prompt_spans()]
            pad = bar.content_region.x - bar.region.x
            row = bar.content_region.y - bar.region.y  # first text row
            assert row == 1  # one row of padding above the text
            zones = {name: start for start, _end, name in bar._zones}

            await pilot.click("#sticky_prompt", offset=(pad + zones["copy"] + 1, row))
            await pilot.pause(0.1)
            assert copied == ["!git status"]
            await pilot.click("#sticky_prompt", offset=(pad + zones["copy"] + 1, 0))  # padding row
            await pilot.pause(0.1)
            assert copied == ["!git status"] * 2

            await pilot.click("#sticky_prompt", offset=(pad + 2, row))  # the text
            await pilot.pause(0.4)
            assert t.scroll_y == ys[2] and not bar.shown

            t.scroll_to(y=ys[1] + 20, animate=False)
            await pilot.pause(0.1)
            zones = {name: start for start, _end, name in bar._zones}
            await pilot.click("#sticky_prompt", offset=(pad + zones["prev"] + 1, row))
            await pilot.pause(0.4)
            assert t.scroll_y == ys[0]

    asyncio.run(run())


def test_hover_expands_and_wheel_scrolls_through(hermetic_app):
    from textual import events

    async def run() -> None:
        app = hermetic_app()
        async with app.run_test(size=(110, 34)) as pilot:
            await pilot.pause(0.2)
            t, bar = await _conversation(app, pilot)
            t.scroll_to(y=t.prompt_spans()[1][1] + 20, animate=False)
            await pilot.pause(0.1)

            await pilot.hover("#sticky_prompt", offset=(10, 1))
            await pilot.pause(0.6)
            assert bar.expanded
            lines = bar.plain_text().split("\n")
            assert lines[1:3] == ["and why it jumps", "third line here"]
            assert "click to jump back" in lines[-1]

            y = t.scroll_y
            bar.post_message(events.MouseScrollUp(bar, 10, 0, 0, -1, 0, False, False, False))
            await pilot.pause(0.1)
            assert t.scroll_y < y  # the wheel reaches the transcript underneath
            assert not bar.expanded

    asyncio.run(run())


def test_running_turn_shows_spinner_and_setting_turns_it_off(hermetic_app):
    async def run() -> None:
        app = hermetic_app()
        async with app.run_test(size=(110, 34)) as pilot:
            await pilot.pause(0.2)
            _t, bar = await _conversation(app, pilot)

            app._busy = True
            app._turn_t0 = time.monotonic() - 12
            app._start_activity_pulse()
            await pilot.pause(0.2)
            assert bar.running and "12s" in bar.plain_text()
            app._busy = False
            app._stop_activity_pulse()
            await pilot.pause(0.1)
            assert not bar.running

            app._sticky_on = False  # settings ui.sticky_prompt = false
            app._sync_sticky_prompt()
            assert not bar.shown

    asyncio.run(run())


def test_sticky_prompt_setting_is_a_boolean():
    from jarvis.storage.settings import DEFAULTS, _coerce

    assert DEFAULTS["ui"]["sticky_prompt"] is True
    assert _coerce("ui.sticky_prompt", "off") is False
