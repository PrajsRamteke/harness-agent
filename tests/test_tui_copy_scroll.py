"""Headless TUI tests: ⌃Y clean copy, streaming, cancel safety, scroll-follow.

Uses Textual's Pilot harness via ``asyncio.run`` (no pytest-asyncio needed).
External side effects (auto-update, MCP auto-connect, session DB) are stubbed
so the tests stay hermetic.
"""
import asyncio
from concurrent.futures import ThreadPoolExecutor

import pytest

# The agent loop drives the console from ONE worker thread (the console
# rejects stream deltas from any other thread), so tests do the same.
_WORKER = ThreadPoolExecutor(max_workers=1, thread_name_prefix="turn-worker")


async def worker(fn, *args):
    return await asyncio.get_running_loop().run_in_executor(_WORKER, fn, *args)


@pytest.fixture()
def hermetic_app(monkeypatch):
    """JarvisTUI with startup side effects stubbed out."""
    monkeypatch.setenv("HARNESS_SKIP_UPDATE", "1")

    import jarvis.updater as updater
    import jarvis.mcp.registry as mcp_registry
    import jarvis.storage.sessions as sessions

    monkeypatch.setattr(updater, "maybe_update_and_reexec", lambda: None)

    def fake_auto_connect(console_print=None, **kw):
        # Mirror the real worker's failure print so ordering is exercised.
        if console_print:
            console_print("[red]mcp: failed to connect 'context7': boom[/]")

    monkeypatch.setattr(
        mcp_registry, "auto_connect_servers", fake_auto_connect, raising=False
    )
    monkeypatch.setattr(sessions, "db_init", lambda: None)
    monkeypatch.setattr(sessions, "db_create_session", lambda model: None)

    # Keep the user's real settings.json / prompt history untouched.
    from jarvis import state
    import jarvis.tui.prompt_history as prompt_history

    monkeypatch.setattr(state, "save_trace_config", lambda: None)
    monkeypatch.setattr(prompt_history.PromptHistory, "_save", lambda self: None)

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


def test_welcome_renders_first_on_launch(hermetic_app):
    """The welcome block is the first thing in the transcript — background
    workers (MCP auto-connect) must print below it, never above."""
    from jarvis.tui.transcript import WelcomeBlock

    async def run() -> None:
        app = hermetic_app()
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause(0.4)
            t = app.query_one("#transcript")
            assert isinstance(t.children[0], WelcomeBlock)
            rendered = t.plain_text()
            assert "█" in rendered, "wordmark missing"
            assert "commands" in rendered
            mcp_pos = rendered.find("failed to connect")
            if mcp_pos != -1:
                assert rendered.find("█") < mcp_pos, "mcp line rendered above welcome"

    asyncio.run(run())


def test_streamed_reply_renders_as_markdown_block(hermetic_app):
    from jarvis import state
    from jarvis.tui.transcript import AssistantBlock

    async def run() -> None:
        app = hermetic_app()
        async with app.run_test(size=(100, 30)) as pilot:
            await pilot.pause(0.3)
            t = app.query_one("#transcript")
            con = app._tui_console
            app._start_activity_pulse()  # drives the UI pump like a real turn

            state._assistant_stream_ui_active = True
            await worker(con.assistant_stream_start, "jarvis")
            for chunk in ("Hello ", "**world**", "\n\n- one\n- two"):
                await worker(con.assistant_stream_push, chunk)
            await pilot.pause(0.3)
            blocks = list(t.query(AssistantBlock))
            assert len(blocks) == 1
            assert "Hello" in blocks[0].text, "pump did not drain streamed text"

            final = "Hello **world**\n\n- one\n- two"
            await worker(con.assistant_stream_commit, final, "jarvis", False)
            await pilot.pause(0.2)
            assert blocks[0].text == final
            assert not blocks[0].streaming
            app._stop_activity_pulse()

    asyncio.run(run())


def test_commit_uses_scrubbed_text(hermetic_app):
    """The committed (hallucination-scrubbed) text replaces what streamed."""
    from jarvis import state
    from jarvis.tui.transcript import AssistantBlock

    async def run() -> None:
        app = hermetic_app()
        async with app.run_test(size=(100, 30)) as pilot:
            await pilot.pause(0.3)
            con = app._tui_console
            state._assistant_stream_ui_active = True
            await worker(con.assistant_stream_start, "jarvis")
            await worker(con.assistant_stream_push, "raw text with claim")
            await worker(con.assistant_stream_commit, "clean text", "jarvis", True)
            await pilot.pause(0.2)
            (blk,) = app.query(AssistantBlock)
            assert blk.text == "clean text"
            assert blk.has_class("-flagged")

    asyncio.run(run())


def test_cancel_keeps_partial_reply(hermetic_app):
    """Esc during streaming keeps everything streamed so far (including the
    not-yet-drained tail) with an interrupted marker; history and lines
    printed after the live block survive."""
    from jarvis import state

    async def run() -> None:
        app = hermetic_app()
        async with app.run_test(size=(100, 30)) as pilot:
            await pilot.pause(0.3)
            t = app.query_one("#transcript")
            con = app._tui_console

            for i in range(40):
                t.write(f"history {i}")
            await pilot.pause(0.1)

            state._assistant_stream_ui_active = True
            await worker(con.assistant_stream_start, "jarvis")
            await worker(con.assistant_stream_push, "reply line\n" * 60)
            # Unflushed tail — the pump never ran, abort must still keep it.
            await worker(con.assistant_stream_push, "tail-text")

            await worker(con.print, "cancelled-by-user-line")
            await worker(con.assistant_stream_abort)
            await pilot.pause(0.3)

            rendered = t.plain_text()
            assert "reply line" in rendered, "partial reply was deleted on cancel"
            assert "tail-text" in rendered, "unflushed tail lost on cancel"
            assert "interrupted" in rendered, "no interrupted marker"
            assert "history 0" in rendered, "history wiped on cancel"
            assert "cancelled-by-user-line" in rendered
            assert t.scroll_offset.y <= max(0, t.max_scroll_y)

    asyncio.run(run())


def test_empty_stream_abort_leaves_no_placeholder(hermetic_app):
    from jarvis import state
    from jarvis.tui.transcript import AssistantBlock

    async def run() -> None:
        app = hermetic_app()
        async with app.run_test(size=(100, 30)) as pilot:
            await pilot.pause(0.3)
            con = app._tui_console
            state._assistant_stream_ui_active = True
            await worker(con.assistant_stream_start, "jarvis")
            await worker(con.assistant_stream_abort)
            await pilot.pause(0.2)
            assert not list(app.query(AssistantBlock))

    asyncio.run(run())


def test_thinking_and_tool_rows_survive_cancel(hermetic_app):
    """Cancelling mid-turn never removes thinking or tool rows written
    earlier (the old line-anchor design could truncate them)."""
    from jarvis import state
    from jarvis.tui.transcript import ThinkingBlock, ToolBlock

    async def run() -> None:
        app = hermetic_app()
        async with app.run_test(size=(100, 30)) as pilot:
            await pilot.pause(0.3)
            t = app.query_one("#transcript")
            con = app._tui_console

            state.show_internal = True
            await worker(con.thinking_stream_start)
            await worker(con.thinking_stream_push, "model reasoning here")
            await worker(con.thinking_stream_finalize)
            await worker(
                con.emit_tool_event, "tool_start",
                {"id": "t1", "name": "read_file", "input": {"path": "a.py"}},
            )
            await worker(
                con.emit_tool_event, "tool_done",
                {"id": "t1", "name": "read_file", "input": {"path": "a.py"}, "output": "x\ny\n"},
            )
            await worker(con.print, "tool ran: important output")
            await worker(con.thinking_stream_reset)
            await worker(con.assistant_stream_abort)
            await pilot.pause(0.2)

            rendered = t.plain_text()
            assert "model reasoning here" in rendered
            assert "important output" in rendered
            assert list(t.query(ThinkingBlock))
            (row,) = t.query(ToolBlock)
            assert row.status == "done"
            assert "Read 2 lines" in rendered
            state.show_internal = False

    asyncio.run(run())


def test_trace_toggle_hides_thinking_without_rebuild(hermetic_app):
    from jarvis import state
    from jarvis.tui.transcript import ThinkingBlock

    async def run() -> None:
        app = hermetic_app()
        async with app.run_test(size=(100, 30)) as pilot:
            await pilot.pause(0.3)
            t = app.query_one("#transcript")
            state.show_internal = True
            app._rebuild_transcript()
            blk = t.add(ThinkingBlock("hidden reasoning"))
            await pilot.pause(0.1)
            assert blk.display is True and blk.styles.display == "block"
            app.action_toggle_internal()  # → off
            await pilot.pause(0.1)
            assert t.has_class("-trace-off")
            assert blk.styles.display == "none"
            assert blk in t.children, "toggle must not drop blocks"
            app.action_toggle_internal()  # → on again
            await pilot.pause(0.1)
            assert blk.styles.display == "block"

    asyncio.run(run())


def test_diff_attaches_under_its_edit_row(hermetic_app):
    from jarvis.tui.transcript import DiffBlock, ToolBlock

    async def run() -> None:
        app = hermetic_app()
        async with app.run_test(size=(100, 30)) as pilot:
            await pilot.pause(0.3)
            t = app.query_one("#transcript")
            con = app._tui_console
            inp = {"path": "pkg/mod.py", "old_str": "a", "new_str": "b"}
            await worker(
                con.emit_tool_event, "tool_start", {"id": "e1", "name": "edit_file", "input": inp}
            )
            await worker(con.print, "unrelated line")
            await worker(con.file_diff, "pkg/mod.py", "x = 1\n", "x = 2\n", "edit")
            await pilot.pause(0.2)
            kids = list(t.children)
            row = next(k for k in kids if isinstance(k, ToolBlock))
            diff = next(k for k in kids if isinstance(k, DiffBlock))
            assert kids.index(diff) == kids.index(row) + 1, "diff not placed under its row"
            assert (diff.added, diff.removed) == (1, 1)
            assert con.changed_files.get("pkg/mod.py") == (1, 1)

    asyncio.run(run())


def test_stream_does_not_yank_scroll_when_scrolled_up(hermetic_app):
    from jarvis import state

    async def run() -> None:
        app = hermetic_app()
        async with app.run_test(size=(100, 30)) as pilot:
            await pilot.pause(0.2)
            t = app.query_one("#transcript")
            con = app._tui_console
            app._start_activity_pulse()

            for i in range(120):
                t.write(f"line {i}")
            await pilot.pause(0.2)

            state._assistant_stream_ui_active = True
            await worker(con.assistant_stream_start, "jarvis")
            await worker(con.assistant_stream_push, "x " * 200)
            await pilot.pause(0.3)
            assert t.is_vertical_scroll_end, "should follow while at the bottom"

            # Scrolled up → streaming, prints and the final commit must not
            # move the viewport (reading/selecting while a reply streams).
            t.scroll_home(animate=False)
            await pilot.pause(0.1)
            y = t.scroll_offset.y
            await worker(con.assistant_stream_push, "\n\ny " * 200)
            await pilot.pause(0.3)
            assert t.scroll_offset.y == y

            await worker(con.print, "tool output line")
            await pilot.pause(0.2)
            assert t.scroll_offset.y == y

            await worker(
                con.assistant_stream_commit, "final reply", "jarvis", False
            )
            await pilot.pause(0.2)
            assert t.scroll_offset.y == y

            # End (empty composer) re-attaches follow.
            await pilot.press("end")
            await pilot.pause(0.2)
            assert t.is_vertical_scroll_end
            app._stop_activity_pulse()

    asyncio.run(run())


def test_prompt_history_up_down(hermetic_app, monkeypatch, tmp_path):
    from jarvis.tui.prompt_history import PromptHistory

    async def run() -> None:
        app = hermetic_app()
        app._history = PromptHistory(tmp_path / "hist.json")
        app._history.add("first prompt")
        app._history.add("second prompt")
        async with app.run_test(size=(100, 30)) as pilot:
            await pilot.pause(0.2)
            prompt = app.query_one("#prompt")
            prompt.text = "draft"
            await pilot.press("up")
            assert prompt.text == "second prompt"
            await pilot.press("up")
            assert prompt.text == "first prompt"
            await pilot.press("down")
            assert prompt.text == "second prompt"
            await pilot.press("down")
            assert prompt.text == "draft", "draft not restored after history walk"

    asyncio.run(run())


def test_slash_popup_filters_and_completes(hermetic_app):
    async def run() -> None:
        app = hermetic_app()
        async with app.run_test(size=(100, 30)) as pilot:
            await pilot.pause(0.2)
            await pilot.press("slash")
            await pilot.pause(0.1)
            assert app.completion_active, "typing / should open the command popup"
            for ch in "the":
                await pilot.press(ch)
            await pilot.pause(0.1)
            opts = app.query_one("#popup_list")
            first = opts.get_option_at_index(opts.highlighted).id
            assert first.startswith("/theme"), first
            await pilot.press("tab")
            await pilot.pause(0.1)
            assert app.query_one("#prompt").text == "/theme"
            assert not app.completion_active
            await pilot.press("escape")

    asyncio.run(run())


def test_long_stream_splits_into_continuation_blocks(hermetic_app):
    """A long reply streams into a chain of bounded blocks (cheap updates);
    the committed text is fully present and in order."""
    from jarvis import state
    from jarvis.tui.transcript import AssistantBlock

    para = "Paragraph {i} " + "word " * 60 + "\n\n"
    reply = "".join(para.format(i=i) for i in range(40))

    async def run() -> None:
        app = hermetic_app()
        async with app.run_test(size=(100, 30)) as pilot:
            await pilot.pause(0.3)
            con = app._tui_console
            app._start_activity_pulse()
            state._assistant_stream_ui_active = True
            await worker(con.assistant_stream_start, "jarvis")
            for i in range(0, len(reply), 400):
                await worker(con.assistant_stream_push, reply[i:i + 400])
                await pilot.pause(0.06)
            await worker(con.assistant_stream_commit, reply, "jarvis", False)
            await pilot.pause(0.2)
            blocks = list(app.query(AssistantBlock))
            assert len(blocks) > 1, "long reply should span continuation blocks"
            assert all(b.continuation for b in blocks[1:])
            joined = "\n\n".join(b.text.strip() for b in blocks)
            assert "Paragraph 0" in joined and "Paragraph 39" in joined
            assert joined.index("Paragraph 0") < joined.index("Paragraph 39")
            app._stop_activity_pulse()

    asyncio.run(run())


def test_session_replay_pairs_tool_results(hermetic_app):
    from jarvis import state
    from jarvis.tui.transcript import AssistantBlock, ToolBlock, UserBlock

    async def run() -> None:
        app = hermetic_app()
        async with app.run_test(size=(100, 30)) as pilot:
            await pilot.pause(0.3)
            state.messages = [
                {"role": "user", "content": "check the file"},
                {"role": "assistant", "content": [
                    {"type": "text", "text": "Reading it."},
                    {"type": "tool_use", "id": "tu1", "name": "read_file", "input": {"path": "a.py"}},
                ]},
                {"role": "user", "content": [
                    {"type": "tool_result", "tool_use_id": "tu1", "content": "x\ny\nz"},
                ]},
                {"role": "assistant", "content": [{"type": "text", "text": "All **good**."}]},
            ]
            try:
                app._render_loaded_session()
                await pilot.pause(0.3)
                t = app.query_one("#transcript")
                kinds = [type(c).__name__ for c in t.children]
                assert kinds.count("UserBlock") == 1
                assert kinds.count("AssistantBlock") == 2
                (row,) = t.query(ToolBlock)
                assert row.status == "done" and row.summary == ["Read 3 lines"]
                assert kinds.index("ToolBlock") < len(kinds) - 1
                assert isinstance(t.children[kinds.index("UserBlock")], UserBlock)
                assert list(t.query(AssistantBlock))[-1].text == "All **good**."
            finally:
                state.messages = []

    asyncio.run(run())


def test_stale_worker_cannot_touch_next_turn(hermetic_app):
    """After Esc the next turn may start while the old worker unwinds: the old
    worker's deltas / abort must not reach the new turn's reply block."""
    from jarvis import state
    from jarvis.tui.transcript import AssistantBlock

    old_worker = ThreadPoolExecutor(max_workers=1, thread_name_prefix="stale")

    async def on_old(fn, *args):
        return await asyncio.get_running_loop().run_in_executor(old_worker, fn, *args)

    async def run() -> None:
        app = hermetic_app()
        async with app.run_test(size=(100, 30)) as pilot:
            await pilot.pause(0.3)
            con = app._tui_console
            app._start_activity_pulse()
            state._assistant_stream_ui_active = True
            await on_old(con.reset_stream_ui)
            await on_old(con.assistant_stream_start, "jarvis")
            await on_old(con.assistant_stream_push, "old partial")
            con.assistant_stream_abort()  # Esc: UI thread finalizes it now
            # next turn on the real worker
            await worker(con.reset_stream_ui)
            await worker(con.assistant_stream_start, "jarvis")
            await worker(con.assistant_stream_push, "new reply")
            await on_old(con.assistant_stream_push, " STALE")
            await on_old(con.assistant_stream_abort)
            await pilot.pause(0.2)
            await worker(con.assistant_stream_commit, "new reply", "jarvis", False)
            await pilot.pause(0.2)
            old_blk, new_blk = list(app.query(AssistantBlock))
            assert old_blk.interrupted and "old partial" in old_blk.text
            assert new_blk.text == "new reply" and not new_blk.interrupted
            app._stop_activity_pulse()
        old_worker.shutdown(wait=False)

    asyncio.run(run())


def test_late_tool_result_after_forget_adds_no_row(hermetic_app):
    from jarvis.tui.transcript import ToolBlock

    async def run() -> None:
        app = hermetic_app()
        async with app.run_test(size=(100, 30)) as pilot:
            await pilot.pause(0.3)
            con = app._tui_console
            inp = {"path": "a.py"}
            await worker(con.emit_tool_event, "tool_start", {"id": "x1", "name": "read_file", "input": inp})
            con.cancel_running_tools()
            con.forget_tools()
            await worker(con.emit_tool_event, "tool_done",
                         {"id": "x1", "name": "read_file", "input": inp, "output": "late"})
            await pilot.pause(0.1)
            rows = list(app.query(ToolBlock))
            assert len(rows) == 1 and rows[0].status == "cancelled"

    asyncio.run(run())


def test_cancelled_worker_stays_cancelled_after_flag_clears():
    import threading

    from jarvis import state

    seen: dict[str, bool] = {}
    go = threading.Event()

    def turn() -> None:
        go.wait(2)
        seen["cancelled"] = state.turn_cancelled()

    t = threading.Thread(target=turn)
    t.start()
    state.cancel_thread(t.ident)
    state.cancel_requested.clear()  # next turn began
    go.set()
    t.join(2)
    state.release_thread(t.ident)
    assert seen["cancelled"] is True
    assert state.turn_cancelled() is False  # other threads unaffected


def test_trace_changed_elsewhere_syncs_transcript(hermetic_app):
    from jarvis import state

    async def run() -> None:
        app = hermetic_app()
        async with app.run_test(size=(100, 30)) as pilot:
            await pilot.pause(0.3)
            t = app.query_one("#transcript")
            state.show_internal = True
            app._rebuild_transcript()
            state.show_internal = False  # e.g. /verbose or the web toggle
            app._slow_refresh()
            await pilot.pause(0.1)
            assert t.has_class("-trace-off")
            state.show_internal = True
            app._slow_refresh()
            assert not t.has_class("-trace-off")

    asyncio.run(run())


def test_exact_slash_match_ranks_first(monkeypatch):
    import jarvis.tui.commands_catalog as cat
    from jarvis.tui.mixins.file_ref import slash_matches

    monkeypatch.setattr(cat, "_custom_command_entries", lambda: [("/pr ", "custom pr")])
    assert slash_matches("/pr")[0][0].strip() == "/pr"


def test_footer_segments_are_clickable(hermetic_app, monkeypatch):
    opened: list[str] = []

    async def run() -> None:
        app = hermetic_app()
        monkeypatch.setattr(app, "_open_model_picker", lambda: opened.append("models"))
        monkeypatch.setattr(app, "_open_agent_picker", lambda: opened.append("agents"))
        async with app.run_test(size=(120, 30)) as pilot:
            await pilot.pause(0.3)
            bar = app.query_one("#footer_left")
            spans = {action: (start, end) for start, end, action in bar._spans}
            start, _end = spans["open_models"]
            await pilot.click("#footer_left", offset=(start + 1, 0))
            await pilot.pause(0.1)
            start, _end = spans["open_agents"]
            await pilot.click("#footer_left", offset=(start, 0))
            await pilot.pause(0.1)

    asyncio.run(run())
    assert opened == ["models", "agents"]


def test_big_paste_collapses_and_queued_message_is_editable(hermetic_app):
    from textual import events

    from jarvis import state

    async def run() -> None:
        app = hermetic_app()
        async with app.run_test(size=(100, 30)) as pilot:
            await pilot.pause(0.2)
            prompt = app.query_one("#prompt")
            big = "\n".join(f"row {i}" for i in range(40))
            await prompt._on_paste(events.Paste(big))
            assert prompt.text.startswith("[Pasted text #") and "+40 lines" in prompt.text

            prompt.clear()
            app._busy = True
            state.prompt_queue.append(("queued idea", None))
            try:
                await pilot.press("up")
                assert prompt.text == "queued idea"
                assert not state.prompt_queue
            finally:
                app._busy = False
                state.prompt_queue.clear()

    asyncio.run(run())


def test_hovered_rows_survive_relayout(hermetic_app):
    """Regression: hover hints queried ``is_mouse_over`` during layout, which
    hit the compositor mid-reflow (IndexError crash). Hover now comes from
    enter/leave events only."""
    import inspect

    import jarvis.tui.transcript as transcript_mod
    from jarvis.tui.transcript import ToolBlock, TurnFooter

    code = inspect.getsource(transcript_mod)
    assert "self.is_mouse_over" not in code

    async def run() -> None:
        app = hermetic_app()
        async with app.run_test(size=(100, 30)) as pilot:
            await pilot.pause(0.3)
            con = app._tui_console
            inp = {"cmd": "ls"}
            await worker(con.emit_tool_event, "tool_start", {"id": "h1", "name": "run_bash", "input": inp})
            await worker(con.emit_tool_event, "tool_done",
                         {"id": "h1", "name": "run_bash", "input": inp, "output": "$ ls\nexit=0\na\nb\nc"})
            t = app.query_one("#transcript")
            t.add(TurnFooter("coding", "#56d364", "m", 1.0, reply="hello"))
            await pilot.pause(0.2)
            await pilot.hover(ToolBlock)
            await pilot.pause(0.1)
            await pilot.resize_terminal(70, 24)
            await pilot.pause(0.2)
            await pilot.hover(TurnFooter)
            await pilot.resize_terminal(110, 32)
            await pilot.pause(0.2)
            row = app.query_one(ToolBlock)
            assert row.status == "done"

    asyncio.run(run())


def test_turn_footer_names_agent_and_model_only_on_change(hermetic_app, monkeypatch):
    """The status bar shows the current agent/model, so turn footers don't
    repeat them — only a mid-session switch is marked. An interrupted LLM
    turn says so once, in its footer (no separate notice line)."""
    import time

    from jarvis import state
    from jarvis.tui.transcript import TurnFooter

    async def run() -> None:
        app = hermetic_app()
        async with app.run_test(size=(100, 30)) as pilot:
            await pilot.pause(0.3)

            def finish(model: str, *, cancelled: bool = False) -> str:
                monkeypatch.setattr(state, "MODEL", model)
                app._busy, app._turn_t0, app._turn_is_llm = True, time.monotonic(), True
                app._turn_cancelled = cancelled
                app._turn_done()
                return list(app.query(TurnFooter))[-1].plain_text()

            first = finish("deepseek/deepseek-v4.1-flash")
            again = finish("deepseek/deepseek-v4.1-flash")
            switched = finish("anthropic/claude-opus-5-5")
            cancelled = finish("anthropic/claude-opus-5-5", cancelled=True)
            await pilot.pause(0.1)

            for line in (first, again, cancelled):
                assert "deepseek" not in line and "opus" not in line, line
            assert "claude-opus-5-5" in switched and "anthropic/" not in switched
            assert "interrupted · what should Jarvis do instead?" in cancelled
            rendered = app.query_one("#transcript").plain_text()
            assert rendered.count("what should Jarvis do instead?") == 1

    asyncio.run(run())
