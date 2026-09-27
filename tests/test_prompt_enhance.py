"""One-click prompt enhance: core rewrite, web endpoint, TUI composer button."""
import asyncio
import contextlib
import json
import threading
import time
import urllib.request
from types import SimpleNamespace

import pytest

from jarvis import prompt_enhance as pe
from jarvis import state


class FakeClient:
    """``client.messages.stream(**kw)`` → final message with one text block.

    ``reply`` gets the masked prompt the model was sent and returns its answer.
    """

    def __init__(self, reply, delay: float = 0.0):
        self.reply = reply
        self.delay = delay
        self.calls: list[dict] = []
        self.messages = self

    @contextlib.contextmanager
    def stream(self, **kwargs):
        self.calls.append(kwargs)
        sent = kwargs["messages"][0]["content"]
        masked = sent.split("<prompt>\n", 1)[1].rsplit("\n</prompt>", 1)[0]
        if self.delay:
            time.sleep(self.delay)
        text = self.reply(masked)
        final = SimpleNamespace(
            content=[SimpleNamespace(type="thinking", thinking="hmm"),
                     SimpleNamespace(type="text", text=text)],
        )
        yield SimpleNamespace(get_final_message=lambda: final)


@pytest.fixture()
def model(monkeypatch):
    def use(reply, **kw):
        client = FakeClient(reply, **kw)
        monkeypatch.setattr(state, "client", client)
        monkeypatch.setattr(state, "MODEL", "test-model")
        monkeypatch.setattr(state, "provider", "anthropic")
        monkeypatch.setattr(state, "auth_mode", "api_key")
        return client
    return use


def test_fixes_text_and_keeps_protected_parts_exact(model):
    seen = {}

    def reply(masked):
        seen["masked"] = masked
        return "Please fix the bug in ⟦1⟧ and look at ⟦2⟧ (see ⟦3⟧ and ⟦4⟧)."

    client = model(reply)
    out = pe.enhance_prompt("pls fix teh bug in @jarvis/tui/app.py and look at `foo_bar()` "
                            "(see [image 1] and https://example.com/a_b)")
    assert out.changed
    assert out.text == ("Please fix the bug in @jarvis/tui/app.py and look at `foo_bar()` "
                        "(see [image 1] and https://example.com/a_b).")
    # The model never saw the paths / code / chips, only placeholders.
    assert "@jarvis" not in seen["masked"] and "foo_bar" not in seen["masked"]
    assert seen["masked"].count("⟦") == 4
    call = client.calls[0]
    assert call["model"] == "test-model"
    assert "tools" not in call and "thinking" not in call
    assert isinstance(call["system"], str)


def test_nothing_joins_the_conversation(model, monkeypatch):
    monkeypatch.setattr(state, "messages", [{"role": "user", "content": "earlier"}])
    model(lambda m: "Fix the tests.")
    pe.enhance_prompt("fix teh tests")
    assert state.messages == [{"role": "user", "content": "earlier"}]


def test_rejects_rewrite_that_loses_a_protected_part(model):
    model(lambda m: "Fix the bug in the app file.")
    with pytest.raises(pe.EnhanceError, match="kept your prompt"):
        pe.enhance_prompt("fix teh bug in @app.py")


def test_rejects_an_answer_instead_of_a_rewrite(model):
    model(lambda m: "Sure! Here is how to do it:\n" + "step\n" * 200)
    with pytest.raises(pe.EnhanceError, match="answered"):
        pe.enhance_prompt("how do i sort a list")


def test_strips_preamble_quotes_and_prompt_tags(model):
    model(lambda m: 'Here is the corrected prompt:\n"<prompt>Sort the list by date.</prompt>"')
    assert pe.enhance_prompt("sort teh list by date").text == "Sort the list by date."


@pytest.mark.parametrize("masked, reply, want", [
    # Seen live from a free model: preamble + bold label + fenced answer.
    ("pls chek ⟦1⟧ and fix teh bug",
     "I'll correct the prompt for you.\n\n**Corrected prompt:**\n```\nPlease check ⟦1⟧ and fix the bug.\n```",
     "Please check ⟦1⟧ and fix the bug."),
    ("sort teh list", "**Corrected prompt:** Sort the list.", "Sort the list."),
    ("sort teh list", "Sure! Here's the corrected prompt:\n\nSort the list.", "Sort the list."),
    ("sort teh list", "Sort the list.\n\n(I fixed the spelling of \"the\".)", "Sort the list."),
    # The user's own lines stay, even when they look like a preamble.
    ("here is teh updated prompt text:\nfix it",
     "Here is the updated prompt text:\nFix it.",
     "Here is the updated prompt text:\nFix it."),
    ("fix teh bug\n\nchanges should be small",
     "Fix the bug.\n\nChanges should be small.",
     "Fix the bug.\n\nChanges should be small."),
])
def test_clean_unwraps_what_models_add(masked, reply, want):
    assert pe._clean(reply, masked) == want


def test_model_that_insists_on_tools_is_refused_then_answers(model):
    """Free-tier gateways force tools into the request; some models call one."""
    client = model(lambda m: "unused")
    answers = iter([
        [SimpleNamespace(type="tool_use", id="c1", name="bash", input={"cmd": "echo hi"})],
        [SimpleNamespace(type="text", text="Fix the bug.")],
    ])

    @contextlib.contextmanager
    def stream(**kwargs):
        client.calls.append(kwargs)
        final = SimpleNamespace(content=next(answers))
        yield SimpleNamespace(get_final_message=lambda: final)

    client.stream = stream
    assert pe.enhance_prompt("fix teh bug").text == "Fix the bug."
    retry = client.calls[1]["messages"]
    assert retry[1]["content"][0]["type"] == "tool_use"
    assert retry[2]["content"][0]["tool_use_id"] == "c1"


def test_clients_that_force_tools_get_an_answer_tool(model):
    """The free tier adds bash/read to every request; answer via corrected_text."""
    client = model(lambda m: "unused")
    client.gate_tools = [{"name": "bash"}, {"name": "read"}]

    @contextlib.contextmanager
    def stream(**kwargs):
        client.calls.append(kwargs)
        final = SimpleNamespace(content=[
            SimpleNamespace(type="tool_use", id="c1", name="corrected_text",
                            input={"text": "Please fix the bug in ⟦1⟧."}),
        ])
        yield SimpleNamespace(get_final_message=lambda: final)

    client.stream = stream
    # (the period the model put after the path is dropped: "@app.py." ≠ "@app.py")
    assert pe.enhance_prompt("pls fix teh bug in @app.py").text == "Please fix the bug in @app.py"
    call = client.calls[0]
    assert [t["name"] for t in call["tools"]] == ["corrected_text"]
    assert "corrected_text" in call["system"]


def test_unchanged_prompt_reports_no_change(model):
    model(lambda m: m)
    out = pe.enhance_prompt("  Sort the list by date.  ")
    assert out.text == "Sort the list by date." and not out.changed


def test_period_glued_onto_a_path_is_dropped(model):
    model(lambda m: m.replace("look", "Look") + ".")
    assert pe.enhance_prompt("look at @app.py").text == "Look at @app.py"


@pytest.mark.parametrize("text, reason", [
    ("", "type a prompt"),
    ("   ", "type a prompt"),
    ("/model", "slash commands"),
    ("!ls -la", "shell commands"),
    ("[Pasted text #1 +40 lines]", "nothing to fix"),
    ("x" * (pe.MAX_CHARS + 1), "too long"),
])
def test_skip_reasons(text, reason):
    assert reason in (pe.skip_reason(text) or "")


def test_oauth_keeps_the_identity_block_first(model, monkeypatch):
    from jarvis.constants import AUTH_OAUTH, OAUTH_IDENTITY

    client = model(lambda m: "Fix it.")
    monkeypatch.setattr(state, "auth_mode", AUTH_OAUTH)
    pe.enhance_prompt("fix it")
    system = client.calls[0]["system"]
    assert system[0] == {"type": "text", "text": OAUTH_IDENTITY}


def test_opencode_turns_thinking_off(model, monkeypatch):
    from jarvis.constants import PROVIDER_OPENCODE

    client = model(lambda m: "Fix it.")
    monkeypatch.setattr(state, "provider", PROVIDER_OPENCODE)
    pe.enhance_prompt("fix it")
    assert client.calls[0]["thinking"] == {"type": "disabled"}


def test_stalled_model_times_out(model):
    model(lambda m: "late", delay=1.0)
    with pytest.raises(pe.EnhanceError, match="longer than"):
        pe.enhance_prompt("fix it", timeout=0.2)


def test_provider_errors_become_short_messages(model):
    class Refused(Exception):
        status_code = 401

    def reply(m):
        raise Refused("401 unauthorized: very long body …")

    model(reply)
    with pytest.raises(pe.EnhanceError, match="refused"):
        pe.enhance_prompt("fix it")


def test_no_client_is_a_clear_error(monkeypatch):
    monkeypatch.setattr(state, "client", None)
    with pytest.raises(pe.EnhanceError, match="no model connected"):
        pe.enhance_prompt("fix it")


# ── web: POST /api/enhance ─────────────────────────────────────────────────

@pytest.fixture()
def web_server():
    from jarvis.web.handler import WebHandler
    from jarvis.web.server import _JarvisHTTPServer

    bridge = SimpleNamespace(token="tok")
    handler = type("H", (WebHandler,), {"bridge": bridge, "app": None})
    server = _JarvisHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()

    def post(payload, token="tok"):
        req = urllib.request.Request(
            f"http://127.0.0.1:{server.server_address[1]}/api/enhance",
            data=json.dumps(payload).encode(),
            headers={"Content-Type": "application/json", "Authorization": f"Bearer {token}"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=5) as res:
                return res.status, json.loads(res.read())
        except urllib.error.HTTPError as err:
            return err.code, None

    yield post
    server.shutdown()
    server.server_close()


def test_web_enhance_returns_the_fixed_text(model, web_server):
    model(lambda m: "Fix the flaky test.")
    status, body = web_server({"text": "fix teh flaky tset"})
    assert status == 200
    assert body == {"ok": True, "text": "Fix the flaky test.", "changed": True}


def test_web_enhance_reports_errors_and_needs_the_token(model, web_server):
    model(lambda m: "unused")
    assert web_server({"text": "/model"}) == (200, {"ok": False, "error": "slash commands aren't rewritten"})
    assert web_server({"text": "fix it"}, token="wrong")[0] == 401


# ── TUI: ✦ enhance button in the composer ─────────────────────────────────

@pytest.fixture()
def hermetic_app(monkeypatch):
    monkeypatch.setenv("HARNESS_SKIP_UPDATE", "1")

    import jarvis.updater as updater
    import jarvis.mcp.registry as mcp_registry
    import jarvis.storage.sessions as sessions
    import jarvis.storage.settings as settings
    import jarvis.tui.prompt_history as prompt_history

    monkeypatch.setattr(updater, "maybe_update_and_reexec", lambda: None)
    monkeypatch.setattr(mcp_registry, "auto_connect_servers", lambda console_print=None, **kw: None,
                        raising=False)
    monkeypatch.setattr(sessions, "db_init", lambda: None)
    monkeypatch.setattr(sessions, "db_create_session", lambda model: None)
    monkeypatch.setattr(settings.Settings, "save", lambda self: None)
    monkeypatch.setattr(prompt_history.PromptHistory, "_save", lambda self: None)

    from jarvis.tui.app import JarvisTUI

    monkeypatch.setattr(JarvisTUI, "_warm_model_catalogs_background", lambda self: None)
    return JarvisTUI


async def _wait_for(pilot, cond, secs=3.0):
    t0 = time.monotonic()
    while not cond():
        if time.monotonic() - t0 > secs:
            raise AssertionError("timed out waiting")
        await pilot.pause(0.05)


def test_tui_button_enhances_then_undoes(hermetic_app, model):
    from jarvis.tui.enhance_button import BUSY, IDLE, UNDO

    model(lambda m: "Please fix the bug in ⟦1⟧ now.", delay=1.0)

    async def run() -> None:
        app = hermetic_app()
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause(0.3)
            prompt = app.query_one("#prompt")
            btn = app.query_one("#enhance")
            assert btn.has_class("hidden")  # nothing typed yet

            prompt.insert("pls fix teh bug in @app.py now")
            await pilot.pause(0.1)
            assert not btn.has_class("hidden") and btn.mode == IDLE

            await pilot.click("#enhance")
            await pilot.pause(0.05)
            assert btn.mode == BUSY and prompt.read_only
            # Enter while it runs doesn't send the half-done prompt.
            await pilot.press("enter")
            assert prompt.text == "pls fix teh bug in @app.py now"

            await _wait_for(pilot, lambda: btn.mode != BUSY)
            assert prompt.text == "Please fix the bug in @app.py now."
            assert btn.mode == UNDO and not prompt.read_only
            assert app._busy is False  # nothing was sent

            await pilot.click("#enhance")  # ↶ undo
            await pilot.pause(0.1)
            assert prompt.text == "pls fix teh bug in @app.py now"
            assert btn.mode == IDLE

            # ⌃G enhances too, and ⌃Z undoes it like any other edit.
            await pilot.press("ctrl+g")
            await _wait_for(pilot, lambda: btn.mode == UNDO)
            await pilot.press("ctrl+z")
            await pilot.pause(0.1)
            assert prompt.text == "pls fix teh bug in @app.py now"
            assert btn.mode == IDLE

    asyncio.run(run())


def test_tui_escape_cancels_and_keeps_the_prompt(hermetic_app, model):
    from jarvis.tui.enhance_button import IDLE

    model(lambda m: "Fixed.", delay=0.5)

    async def run() -> None:
        app = hermetic_app()
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause(0.3)
            prompt = app.query_one("#prompt")
            prompt.insert("fix teh thing")
            await pilot.press("ctrl+g")
            await pilot.pause(0.05)
            await pilot.press("escape")
            await pilot.pause(0.8)  # the late reply is dropped
            assert prompt.text == "fix teh thing"
            assert app.query_one("#enhance").mode == IDLE
            assert not prompt.read_only

    asyncio.run(run())


def test_tui_commands_hide_the_button(hermetic_app, model):
    model(lambda m: "unused")

    async def run() -> None:
        app = hermetic_app()
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause(0.3)
            prompt = app.query_one("#prompt")
            prompt.insert("!git status")
            await pilot.pause(0.1)
            assert app.query_one("#enhance").has_class("hidden")

    asyncio.run(run())

