"""Headless TUI tests: ⌃Y clean copy and scroll-follow during streaming.

Uses Textual's Pilot harness via ``asyncio.run`` (no pytest-asyncio needed).
External side effects (auto-update, MCP auto-connect, session DB) are stubbed
so the tests stay hermetic.
"""
import asyncio

import pytest


@pytest.fixture()
def hermetic_app(monkeypatch):
    """JarvisTUI with startup side effects stubbed out."""
    monkeypatch.setenv("HARNESS_SKIP_UPDATE", "1")

    import jarvis.updater as updater
    import jarvis.mcp.registry as mcp_registry
    import jarvis.storage.sessions as sessions

    monkeypatch.setattr(updater, "maybe_update_and_reexec", lambda: None)

    def fake_auto_connect(console_print=None, **kw):
        # Mirror the real worker's first print so ordering is exercised.
        if console_print:
            console_print("[dim]mcp: auto-connecting 'context7'…[/]")

    monkeypatch.setattr(
        mcp_registry, "auto_connect_servers", fake_auto_connect, raising=False
    )
    monkeypatch.setattr(sessions, "db_init", lambda: None)
    monkeypatch.setattr(sessions, "db_create_session", lambda model: None)

    from jarvis.tui.app import JarvisTUI

    return JarvisTUI


def test_ctrl_y_copies_normalized_reply(hermetic_app, monkeypatch):
    from jarvis import state

    copied: list[str] = []

    async def run() -> None:
        app = hermetic_app()
        monkeypatch.setattr(
            app, "_copy_to_system_clipboard", lambda text: copied.append(text) or True
        )
        async with app.run_test(size=(100, 30)) as pilot:
            await pilot.pause(0.2)

            state.last_assistant_text = ""
            await pilot.press("ctrl+y")
            await pilot.pause()
            assert "nothing to copy" in app._status_msg

            state.last_assistant_text = "Hello   \n\n\n\nWorld  "
            await pilot.press("ctrl+y")
            await pilot.pause()
            assert "copied" in app._status_msg

    asyncio.run(run())
    assert copied == ["Hello\n\nWorld"]


def test_welcome_art_renders_on_launch_and_first(hermetic_app):
    """Regressions: (1) art was decided before layout (width 0 → 24 → no
    art) so the banner appeared artless until /new re-rendered it;
    (2) the MCP auto-connect worker printed before the deferred intro,
    putting 'mcp: auto-connecting…' above the welcome art."""

    async def run() -> None:
        app = hermetic_app()
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause(0.4)
            log = app.query_one("#transcript")
            rendered = "\n".join(strip.text for strip in log.lines)
            assert "██" in rendered, "welcome art missing from startup transcript"
            assert "Harness Agent ready" not in rendered
            mcp_pos = rendered.find("auto-connecting")
            if mcp_pos != -1:
                assert rendered.find("██") < mcp_pos, "mcp line rendered above art"

    asyncio.run(run())


def test_cancel_keeps_partial_reply_and_no_black_screen(hermetic_app):
    """Esc during streaming must keep everything streamed so far — the
    partial reply stays in the transcript with an 'interrupted' marker
    (earlier versions deleted it, and a stale virtual_size could leave the
    viewport past the content: a black screen)."""
    from jarvis import state

    async def run() -> None:
        app = hermetic_app()
        async with app.run_test(size=(100, 30)) as pilot:
            await pilot.pause(0.3)
            log = app.query_one("#transcript")
            con = app._tui_console

            for i in range(40):
                log.write(f"history {i}")
            log.scroll_end(animate=False)
            await pilot.pause(0.2)

            state._assistant_stream_ui_active = True
            await asyncio.to_thread(con.assistant_stream_start, "jarvis")
            await asyncio.to_thread(con.assistant_stream_push, "reply line\n" * 60)
            await asyncio.to_thread(con.assistant_stream_flush)
            # Unflushed tail — must still appear after the abort re-render.
            await asyncio.to_thread(con.assistant_stream_push, "tail-text")
            await pilot.pause(0.2)

            # The app prints this right when Esc is hit — it lands after the
            # live panel and must survive the abort's block replacement.
            await asyncio.to_thread(con.print, "cancelled-by-user-line")
            await asyncio.to_thread(con.assistant_stream_abort)
            await pilot.pause(0.3)

            rendered = "\n".join(strip.text for strip in log.lines)
            assert "reply line" in rendered, "partial reply was deleted on cancel"
            assert "tail-text" in rendered, "unflushed tail lost on cancel"
            assert "interrupted" in rendered, "no interrupted marker on panel"
            assert "history 0" in rendered, "history wiped on cancel"
            assert "cancelled-by-user-line" in rendered, (
                "line printed after the live panel was eaten by abort"
            )
            assert log.virtual_size.height == len(log.lines)
            assert log.scroll_offset.y <= log.max_scroll_y

    asyncio.run(run())


def test_cancel_does_not_wipe_output_when_trace_off(hermetic_app):
    """Regression: with trace off, the thinking stream 'starts' without ever
    rendering; cancelling then truncated at its anchor, wiping tool panels
    and prints written after it."""
    from jarvis import state

    async def run() -> None:
        app = hermetic_app()
        async with app.run_test(size=(100, 30)) as pilot:
            await pilot.pause(0.3)
            log = app.query_one("#transcript")
            con = app._tui_console

            # Trace off: thinking deltas arrive but are never drawn.
            state.show_internal = False
            await asyncio.to_thread(con.thinking_stream_start)
            await asyncio.to_thread(con.thinking_stream_push, "invisible reasoning")
            await asyncio.to_thread(con.print, "tool ran: important output")
            await pilot.pause(0.2)

            await asyncio.to_thread(con.assistant_stream_abort)
            await pilot.pause(0.2)

            rendered = "\n".join(strip.text for strip in log.lines)
            assert "important output" in rendered, "cancel wiped tool output"

    asyncio.run(run())


def test_cancel_keeps_finalized_thinking_and_tool_panels(hermetic_app):
    """Regression: with trace ON, an iteration that streamed thinking and then
    ran tools (no text) left the thinking anchor 'live'; Esc then truncated at
    it — wiping the thinking panel AND every tool panel after it."""
    from jarvis import state

    async def run() -> None:
        app = hermetic_app()
        async with app.run_test(size=(100, 30)) as pilot:
            await pilot.pause(0.3)
            log = app.query_one("#transcript")
            con = app._tui_console

            state.show_internal = True
            await asyncio.to_thread(con.thinking_stream_start)
            await asyncio.to_thread(con.thinking_stream_push, "model reasoning here")
            await asyncio.to_thread(con.thinking_stream_flush)
            await asyncio.to_thread(con.thinking_stream_finalize)
            await asyncio.to_thread(con.print, "tool panel: read_file output")
            await pilot.pause(0.2)

            # User presses esc mid-turn.
            await asyncio.to_thread(con.assistant_stream_abort)
            await pilot.pause(0.2)

            rendered = "\n".join(strip.text for strip in log.lines)
            assert "model reasoning here" in rendered, "finalized thinking wiped"
            assert "read_file output" in rendered, "tool panel wiped by cancel"
            state.show_internal = False

    asyncio.run(run())


def test_stale_thinking_anchor_cannot_wipe_next_iteration(hermetic_app):
    """Regression: iteration N streams thinking then only tool calls; if
    iteration N+1 commits text without its own thinking, the commit truncated
    at N's stale anchor, deleting the tool panels in between. The stream layer
    now calls thinking_stream_reset() before each iteration."""
    from jarvis import state

    async def run() -> None:
        app = hermetic_app()
        async with app.run_test(size=(100, 30)) as pilot:
            await pilot.pause(0.3)
            log = app.query_one("#transcript")
            con = app._tui_console

            state.show_internal = True
            # Iteration N: thinking rendered + finalized, then tool output.
            await asyncio.to_thread(con.thinking_stream_start)
            await asyncio.to_thread(con.thinking_stream_push, "iteration-N reasoning")
            await asyncio.to_thread(con.thinking_stream_flush)
            await asyncio.to_thread(con.thinking_stream_finalize)
            await asyncio.to_thread(con.print, "tool panel between iterations")
            await pilot.pause(0.2)

            # Iteration N+1 begins: stream layer resets thinking state.
            await asyncio.to_thread(con.thinking_stream_reset)
            state._assistant_stream_ui_active = True
            await asyncio.to_thread(con.assistant_stream_start, "jarvis")
            await asyncio.to_thread(con.assistant_stream_push, "final answer")
            await asyncio.to_thread(
                con.assistant_stream_commit, "final answer", "jarvis", False,
                ["iteration-N reasoning"],
            )
            await pilot.pause(0.2)

            rendered = "\n".join(strip.text for strip in log.lines)
            assert "tool panel between iterations" in rendered, (
                "stale thinking anchor wiped tool panels on commit"
            )
            assert "final answer" in rendered
            state.show_internal = False

    asyncio.run(run())


def test_stream_does_not_yank_scroll_when_scrolled_up(hermetic_app):
    async def run() -> None:
        app = hermetic_app()
        async with app.run_test(size=(100, 30)) as pilot:
            await pilot.pause(0.2)
            log = app.query_one("#transcript")
            con = app._tui_console

            for i in range(120):
                log.write(f"line {i}")
            log.scroll_end(animate=False)
            await pilot.pause(0.2)

            # At bottom -> streaming flushes keep following.
            await asyncio.to_thread(con.assistant_stream_start, "jarvis")
            await asyncio.to_thread(con.assistant_stream_push, "x" * 200)
            await asyncio.to_thread(con.assistant_stream_flush)
            await pilot.pause(0.2)
            assert log.is_vertical_scroll_end

            # Scrolled up -> flushes, prints, and the final commit must not
            # move the viewport (this was the "can't read/select while
            # streaming" bug).
            log.scroll_home(animate=False)
            await pilot.pause()
            y = log.scroll_offset.y
            await asyncio.to_thread(con.assistant_stream_push, "y" * 400)
            await asyncio.to_thread(con.assistant_stream_flush)
            await pilot.pause(0.2)
            assert log.scroll_offset.y == y

            await asyncio.to_thread(con.print, "tool output line")
            await pilot.pause(0.2)
            assert log.scroll_offset.y == y

            await asyncio.to_thread(
                con.assistant_stream_commit, "final reply", "jarvis", False
            )
            await pilot.pause(0.2)
            assert log.scroll_offset.y == y

            # End key resumes following.
            await pilot.press("end")
            await pilot.pause()
            assert log.is_vertical_scroll_end

    asyncio.run(run())
