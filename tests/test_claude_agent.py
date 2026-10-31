"""Claude Code CLI provider (``claude_agent``): status, the tool bridge, routing.

Nothing here runs the real ``claude`` (conftest hides it): the client is driven
by a fake Claude Code that speaks the SDK's message types — stream events, an
assistant message per block, MCP tool calls through the session's handlers.
"""
from __future__ import annotations

import asyncio
import json
import time
from types import SimpleNamespace as NS

import pytest

from jarvis.auth import claude_agent as ca
from jarvis.auth import claude_agent_client as cac


# ─── A fake Claude Code ───────────────────────────────────────────────────


class StreamEvent:
    def __init__(self, event):
        self.event = event
        self.parent_tool_use_id = None


class ToolUseBlock:
    def __init__(self, id, name, input):
        self.id, self.name, self.input = id, name, input


class TextBlock:
    def __init__(self, text):
        self.text = text


class AssistantMessage:
    def __init__(self, content, error=None):
        self.content = content
        self.error = error
        self.parent_tool_use_id = None


class SystemMessage:
    def __init__(self, subtype, data):
        self.subtype, self.data = subtype, data


class ResultMessage:
    def __init__(self, session_id, is_error=False, stop_reason="end_turn", errors=None, subtype="success"):
        self.session_id = session_id
        self.is_error = is_error
        self.stop_reason = stop_reason
        self.errors = errors
        self.subtype = subtype
        self.result = ""
        self.api_error_status = None


_n = [0]


def _tid() -> str:
    _n[0] += 1
    return f"toolu_{_n[0]:04d}"


class FakeClaude:
    """One fake `claude` process. ``brain(fake, blocks)`` is an async function
    that plays one turn using the helpers below."""

    def __init__(self, sess, brain, log):
        self.sess = sess
        self.brain = brain
        self.log = log
        self.out: asyncio.Queue = asyncio.Queue()
        self.sid = sess.resume or f"sess-{sess.n}"
        self.queries: list[list[dict]] = []
        log.append(("spawn", sess.resume, sess.spec))

    async def connect(self):
        pass

    async def receive_messages(self):
        while True:
            m = await self.out.get()
            if m is None:
                return
            yield m

    async def query(self, prompt):
        async for msg in prompt:
            blocks = msg["message"]["content"]
            self.queries.append(blocks)
            self.log.append(("query", blocks))
            asyncio.ensure_future(self._turn(blocks))

    async def _turn(self, blocks):
        await self.emit(SystemMessage("init", {"session_id": self.sid}))
        await self.brain(self, blocks)

    async def interrupt(self):
        self.log.append(("interrupt",))
        await self.emit(ResultMessage(self.sid, is_error=False, stop_reason="end_turn"))

    async def disconnect(self):
        self.log.append(("disconnect", self.sid))
        await self.out.put(None)

    # helpers for brains
    async def emit(self, m):
        await self.out.put(m)

    async def say(self, text, *, stop="end_turn", result=True):
        await self.emit(StreamEvent({"type": "message_start", "message": {"usage": {
            "input_tokens": 3, "cache_read_input_tokens": 100, "cache_creation_input_tokens": 7}}}))
        await self.emit(StreamEvent({"type": "content_block_start", "index": 0,
                                     "content_block": {"type": "text", "text": ""}}))
        for part in (text[: len(text) // 2], text[len(text) // 2:]):
            await self.emit(StreamEvent({"type": "content_block_delta", "index": 0,
                                         "delta": {"type": "text_delta", "text": part}}))
        await self.emit(AssistantMessage([TextBlock(text)]))
        await self.emit(StreamEvent({"type": "content_block_stop", "index": 0}))
        await self.emit(StreamEvent({"type": "message_delta", "delta": {"stop_reason": stop},
                                     "usage": {"input_tokens": 3, "output_tokens": 11,
                                               "cache_read_input_tokens": 100}}))
        await self.emit(StreamEvent({"type": "message_stop"}))
        if result:
            await self.emit(ResultMessage(self.sid, stop_reason=stop))

    async def call_tools(self, calls):
        """Stream tool_use blocks, then call each through the MCP handler (as
        Claude Code does) and return the results."""
        await self.emit(StreamEvent({"type": "message_start", "message": {"usage": {"input_tokens": 5}}}))
        ids = []
        for i, (name, args) in enumerate(calls):
            tid = _tid()
            ids.append(tid)
            full = cac.PREFIX + name
            await self.emit(StreamEvent({"type": "content_block_start", "index": i,
                                         "content_block": {"type": "tool_use", "id": tid, "name": full, "input": {}}}))
            raw = json.dumps(args)
            await self.emit(StreamEvent({"type": "content_block_delta", "index": i,
                                         "delta": {"type": "input_json_delta", "partial_json": raw}}))
            await self.emit(AssistantMessage([ToolUseBlock(tid, full, args)]))
            await self.emit(StreamEvent({"type": "content_block_stop", "index": i}))
        await self.emit(StreamEvent({"type": "message_delta", "delta": {"stop_reason": "tool_use"},
                                     "usage": {"input_tokens": 5, "output_tokens": 20}}))
        await self.emit(StreamEvent({"type": "message_stop"}))
        results = []
        for name, args in calls:  # one by one, like Claude Code
            results.append(await self.sess._handler(name)(args))
        return results


def _text(blocks) -> str:
    return "\n".join(b.get("text", "") for b in blocks if b.get("type") == "text")


@pytest.fixture
def harness():
    log: list = []
    brains: dict = {}

    def factory(sess):
        fake = FakeClaude(sess, brains["brain"], log)
        brains.setdefault("fakes", []).append(fake)
        return fake

    eng = cac._Engine(factory=factory)
    client = cac.ClaudeAgentClient(engine_=eng)
    yield NS(client=client, engine=eng, log=log, brains=brains)
    eng.close_all()


def _run(client, messages, *, tools=None, system="You are Jarvis.", model="sonnet", thinking=None):
    deltas = []
    with client.messages.stream(model=model, messages=messages, tools=tools or TOOLS,
                                system=system, thinking=thinking) as st:
        for kind, d in st.delta_stream:
            deltas.append((kind, d))
        final = st.get_final_message()
    return final, deltas


TOOLS = [{"name": "get_time", "description": "Time in a zone",
          "input_schema": {"type": "object", "properties": {"tz": {"type": "string"}}, "required": ["tz"]}}]


# ─── The tool bridge ──────────────────────────────────────────────────────


def test_tool_call_round_trip_through_jarvis(harness):
    seen = {}

    async def brain(fake, blocks):
        if "time" in _text(blocks):
            results = await fake.call_tools([("get_time", {"tz": "UTC"}), ("get_time", {"tz": "IST"})])
            seen["results"] = results
            await fake.say("UTC 10:00, IST 15:30")
        else:
            await fake.say("ok")

    harness.brains["brain"] = brain
    msgs = [{"role": "user", "content": "what time is it?"}]
    final, deltas = _run(harness.client, msgs)
    assert final.stop_reason == "tool_use"
    calls = [b for b in final.content if b.type == "tool_use"]
    assert [(c.name, c.input) for c in calls] == [("get_time", {"tz": "UTC"}), ("get_time", {"tz": "IST"})]
    assert ("tool_input", (0, "get_time", "")) in deltas  # progress for long calls

    msgs.append({"role": "assistant", "content": final.content})
    msgs.append({"role": "user", "content": [
        {"type": "tool_result", "tool_use_id": calls[0].id, "content": "UTC 10:00"},
        {"type": "tool_result", "tool_use_id": calls[1].id, "content": [{"type": "text", "text": "IST 15:30"}],
         "is_error": False},
    ]})
    final2, deltas2 = _run(harness.client, msgs)
    assert final2.stop_reason == "end_turn"
    assert "".join(d for k, d in deltas2 if k == "text") == "UTC 10:00, IST 15:30"
    assert seen["results"][0]["content"] == [{"type": "text", "text": "UTC 10:00"}]
    assert seen["results"][1]["content"] == [{"type": "text", "text": "IST 15:30"}]
    # Usage: the prompt's real size (uncached + cache read + write) and output.
    assert final2.usage.cache_read_input_tokens == 100 and final2.usage.output_tokens == 11
    assert sum(1 for e in harness.log if e[0] == "spawn") == 1


def test_next_turn_continues_the_same_process(harness):
    async def brain(fake, blocks):
        await fake.say("reply " + _text(blocks))

    harness.brains["brain"] = brain
    msgs = [{"role": "user", "content": "one"}]
    final, _ = _run(harness.client, msgs)
    msgs += [{"role": "assistant", "content": final.content}, {"role": "user", "content": "two"}]
    final2, _ = _run(harness.client, msgs)
    assert "two" in final2.content[0].text
    fake = harness.brains["fakes"][0]
    assert len(harness.brains["fakes"]) == 1
    # Only the new message is sent — Claude Code already has the rest.
    assert [_text(q) for q in fake.queries] == ["one", "two"]


def test_prompt_change_restarts_and_resumes_the_session(harness):
    async def brain(fake, blocks):
        await fake.say("hi")

    harness.brains["brain"] = brain
    msgs = [{"role": "user", "content": "one"}]
    final, _ = _run(harness.client, msgs)
    msgs += [{"role": "assistant", "content": final.content}, {"role": "user", "content": "two"}]
    _run(harness.client, msgs, system="You are Jarvis, in another folder now.")
    spawns = [e for e in harness.log if e[0] == "spawn"]
    assert len(spawns) == 2
    assert spawns[1][1] == harness.brains["fakes"][0].sid  # resumed Claude Code's own saved session
    assert _text(harness.brains["fakes"][1].queries[0]) == "two"


def test_unknown_history_starts_with_a_checkpoint(harness):
    async def brain(fake, blocks):
        await fake.say("continuing")

    harness.brains["brain"] = brain
    msgs = [
        {"role": "user", "content": "fix the bug"},
        {"role": "assistant", "content": [{"type": "text", "text": "Looking."},
                                          {"type": "tool_use", "id": "x1", "name": "read_file",
                                           "input": {"path": "a.py"}}]},
        {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "x1", "content": "print(1)"}]},
        {"role": "assistant", "content": [{"type": "text", "text": "It prints 1."}]},
        {"role": "user", "content": "now make it print 2"},
    ]
    _run(harness.client, msgs)
    first = harness.brains["fakes"][0].queries[0]
    cp = first[0]["text"]
    assert cp.startswith(f"<{cac.CHECKPOINT_TAG}>")
    assert "User: fix the bug" in cp and "read_file(" in cp and "Tool result: print(1)" in cp
    assert first[-1]["text"] == "now make it print 2"


def test_send_now_text_rides_on_the_last_tool_result(harness):
    got = {}

    async def brain(fake, blocks):
        if "go" in _text(blocks):
            got["r"] = await fake.call_tools([("get_time", {"tz": "UTC"})])
            await fake.say("done")

    harness.brains["brain"] = brain
    msgs = [{"role": "user", "content": "go"}]
    final, _ = _run(harness.client, msgs)
    tid = final.content[0].id
    msgs += [{"role": "assistant", "content": final.content},
             {"role": "user", "content": [{"type": "tool_result", "tool_use_id": tid, "content": "10:00"},
                                          {"type": "text", "text": "[Sent by the user…] use IST instead"}]}]
    _run(harness.client, msgs)
    texts = [c["text"] for c in got["r"][0]["content"]]
    assert texts[0] == "10:00" and "use IST instead" in texts[-1]


def test_tool_result_images_pass_through(harness):
    got = {}

    async def brain(fake, blocks):
        if "shot" in _text(blocks):
            got["r"] = await fake.call_tools([("get_time", {"tz": "UTC"})])
            await fake.say("seen")

    harness.brains["brain"] = brain
    msgs = [{"role": "user", "content": "shot"}]
    final, _ = _run(harness.client, msgs)
    msgs += [{"role": "assistant", "content": final.content},
             {"role": "user", "content": [{"type": "tool_result", "tool_use_id": final.content[0].id, "content": [
                 {"type": "text", "text": "screenshot"},
                 {"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": "AAAA"}}]}]}]
    _run(harness.client, msgs)
    assert got["r"][0]["content"][1] == {"type": "image", "data": "AAAA", "mimeType": "image/png"}


def test_signed_out_error_becomes_a_clear_card(harness):
    async def brain(fake, blocks):
        await fake.emit(AssistantMessage([TextBlock("Not logged in · Please run /login")],
                                         error="authentication_failed"))
        await fake.emit(ResultMessage(fake.sid, is_error=True, subtype="success"))

    harness.brains["brain"] = brain
    with pytest.raises(cac.ClaudeAgentError) as ei:
        _run(harness.client, [{"role": "user", "content": "hi"}])
    assert ei.value.status_code == 401
    assert "claude auth login" in str(ei.value)

    from jarvis.repl import api_errors

    rep = api_errors.describe("auth", provider="Claude Code CLI", provider_id="claude_agent", status=401)
    assert rep.title == "Claude Code is signed out"
    assert any(a.command == "/claude-code" for a in rep.actions)
    assert "never sees it" in rep.message


def test_interrupted_reply_continues_in_the_same_session(harness):
    started = asyncio.Event

    async def brain(fake, blocks):
        if "long" in _text(blocks):
            await fake.emit(StreamEvent({"type": "message_start", "message": {"usage": {}}}))
            await fake.emit(StreamEvent({"type": "content_block_start", "index": 0,
                                         "content_block": {"type": "text", "text": ""}}))
            await fake.emit(StreamEvent({"type": "content_block_delta", "index": 0,
                                         "delta": {"type": "text_delta", "text": "partial…"}}))
            # …then waits until interrupted (FakeClaude.interrupt ends the turn)
        else:
            await fake.say("fresh answer")

    harness.brains["brain"] = brain
    msgs = [{"role": "user", "content": "long please"}]
    with harness.client.messages.stream(model="sonnet", messages=msgs, tools=TOOLS, system="s") as st:
        for kind, d in st.delta_stream:
            if kind == "text":
                break
    # Esc → stream closed; Jarvis keeps no partial reply and the user asks again.
    msgs.append({"role": "user", "content": "short instead"})
    final, _ = _run(harness.client, msgs, system="s")
    assert final.content[0].text == "fresh answer"
    fake = harness.brains["fakes"][0]
    assert len(harness.brains["fakes"]) == 1 and ("interrupt",) in harness.log
    assert fake.queries[-1][0]["text"].startswith("[The user stopped")
    assert _text(fake.queries[-1]).endswith("short instead")


def test_spec_maps_thinking_and_strips_cache_control():
    spec = cac.build_spec(model="opus", system=[{"type": "text", "text": "A", "cache_control": {"type": "x"}},
                                                {"type": "text", "text": "B"}],
                          tools=[{"name": "t", "description": "d", "input_schema": {"type": "object"},
                                  "cache_control": {"type": "ephemeral"}}],
                          thinking={"type": "adaptive", "effort": "high"})
    assert spec.system == "A\n\nB" and spec.effort == "high" and spec.thinking == "adaptive"
    assert spec.tool_names == ["t"]
    off = cac.build_spec(model="opus", system="", tools=[], thinking={"type": "disabled"})
    assert off.thinking == "disabled" and off.effort == ""


# ─── Status: asks the binary, keeps no secrets ────────────────────────────


def test_auth_status_keeps_only_safe_fields(monkeypatch):
    raw = {"loggedIn": True, "authMethod": "claude.ai", "email": "a@b.c", "subscriptionType": "max",
           "accessToken": "sk-ant-oat01-SECRET", "refreshToken": "SECRET", "orgId": "x"}
    monkeypatch.setattr(ca, "_run", lambda cli, *args: (0, json.dumps(raw), ""))
    kept = ca.auth_status("/bin/claude")
    assert "SECRET" not in json.dumps(kept)
    assert kept == {"loggedIn": True, "authMethod": "claude.ai", "email": "a@b.c", "subscriptionType": "max"}


def test_probe_and_problems(monkeypatch, tmp_path):
    cli = tmp_path / "claude"
    cli.write_text("#!/bin/sh\n")
    cli.chmod(0o755)
    monkeypatch.setattr(ca, "_candidates", lambda: [str(cli)])
    monkeypatch.setattr(ca, "sdk_version", lambda: "0.2.176")
    answers = {"--version": (0, "2.1.316 (Claude Code)", ""),
               "auth": (1, json.dumps({"loggedIn": False}), "")}
    monkeypatch.setattr(ca, "_run", lambda c, *a: answers[a[0]])
    s = ca.refresh()
    assert s.cli_ok and not s.ready
    assert s.problem()[0] == "signed_out" and s.problem()[2] == "claude auth login"
    answers["auth"] = (0, json.dumps({"loggedIn": True, "authMethod": "claude.ai",
                                      "subscriptionType": "pro", "email": "me@x.io"}), "")
    s = ca.refresh()
    assert s.ready and s.subscription and s.account_line() == "me@x.io · Claude Pro"
    answers["--version"] = (0, "2.0.1 (Claude Code)", "")
    assert ca.refresh().problem()[0] == "cli_old"
    monkeypatch.setattr(ca, "sdk_version", lambda: None)
    answers["--version"] = (0, "2.1.316", "")
    assert ca.refresh().problem()[0] == "no_sdk"


def test_child_env_names_jarvis_and_never_carries_credentials():
    env = ca.child_env()
    assert env["CLAUDE_AGENT_SDK_CLIENT_APP"].startswith("jarvis/")
    assert not any(k in env for k in ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN", "ANTHROPIC_BASE_URL",
                                      "ANTHROPIC_CUSTOM_HEADERS", "CLAUDE_CODE_OAUTH_TOKEN"))


def test_options_disable_builtin_tools_and_user_settings(monkeypatch):
    pytest.importorskip("claude_agent_sdk")
    monkeypatch.setattr(ca, "find_cli", lambda: "/usr/bin/true")
    spec = cac.build_spec(model="sonnet", system="hi", tools=TOOLS, thinking={"type": "adaptive", "effort": "low"})
    opts = cac._Session(cac._Engine(), spec)._options()
    assert opts.tools == [] and opts.setting_sources == [] and opts.strict_mcp_config
    assert opts.permission_mode == "dontAsk" and opts.allowed_tools == [cac.PREFIX + "get_time"]
    assert opts.system_prompt["preset"] == "claude_code" and "hi" in opts.system_prompt["append"]
    assert opts.verbatim_prompts and opts.effort == "low" and opts.thinking["type"] == "adaptive"
    assert set(opts.mcp_servers) == {cac.SERVER}
    for k in ("ANTHROPIC_API_KEY", "ANTHROPIC_BASE_URL", "ANTHROPIC_AUTH_TOKEN", "CLAUDE_CODE_OAUTH_TOKEN"):
        assert k not in opts.env


# ─── Provider wiring ──────────────────────────────────────────────────────


@pytest.fixture
def ready(monkeypatch):
    s = ca.Status(cli_path="/x/claude", cli_version="2.1.316", sdk_version="0.2.176", logged_in=True,
                  auth_method="claude.ai", plan="max", email="me@x.io", checked_at=time.time())
    monkeypatch.setitem(ca._memo, "status", s)
    ca.store_models([
        {"value": "default", "resolvedModel": "claude-opus-5-5", "displayName": "Default (recommended)",
         "description": "Opus 5.5", "supportedEffortLevels": ["low", "medium", "high", "xhigh", "max"],
         "supportsThinkingOff": False},
        {"value": "claude-haiku-4-5-20251001", "displayName": "Haiku 4.5", "supportsThinkingOff": True},
    ])
    return s


def test_models_pricing_and_thinking_come_from_claude_code(ready):
    from jarvis.auth.thinking_caps import thinking_caps
    from jarvis.constants import providers as p
    from jarvis.repl import thinking

    assert [m for m, _ in p.claude_agent_models_for_picker()] == ["default", "claude-haiku-4-5-20251001"]
    assert p.PROVIDER_CLAUDE_AGENT in p.connected_model_sources()
    assert p.model_pricing("default", p.PROVIDER_CLAUDE_AGENT) == (0.0, 0.0)  # a Claude plan
    assert p.normalize_model_for_provider("gpt-5", p.PROVIDER_CLAUDE_AGENT) == "default"
    caps = thinking_caps("default", p.PROVIDER_CLAUDE_AGENT)
    assert caps.levels == ("low", "medium", "high", "xhigh", "max") and not caps.can_off
    kw: dict = {}
    thinking.apply(kw, provider=p.PROVIDER_CLAUDE_AGENT, model="default", client=None,
                   think_mode=False, effort="high")
    assert kw["thinking"] == {"type": "adaptive", "effort": "low"}  # can't be off: lowest level
    kw = {}
    thinking.apply(kw, provider=p.PROVIDER_CLAUDE_AGENT, model="claude-haiku-4-5-20251001", client=None,
                   think_mode=False, effort="high")
    assert kw["thinking"] == {"type": "disabled"}


def test_api_key_login_is_priced(ready, monkeypatch):
    from jarvis.constants import providers as p

    ready.auth_method, ready.plan = "console", ""
    price = p.model_pricing("default", p.PROVIDER_CLAUDE_AGENT)
    assert price is None or price == p.PRICING.get("claude-opus-5-5")[:2]


def test_web_card_and_use(ready, monkeypatch):
    from jarvis.web import providers_api as pv

    rows = {r["id"]: r for r in pv.list_providers()["providers"]}
    row = rows["claude_agent"]
    assert row["kind"] == "cli" and row["connected"] and row["cli"]["account"] == "me@x.io · Claude Max"
    assert "token" not in json.dumps(row).lower()
    # Subscription sign-ins say whether the provider allows them.
    assert {i: rows[i]["safety"] for i in ("claude_agent", "openai_codex", "anthropic", "antigravity")} == {
        "claude_agent": "safe", "openai_codex": "safe", "anthropic": "risky", "antigravity": "risky"}
    assert "banned" in rows["anthropic"]["safety_note"]
    ready.logged_in = False
    res = pv.use_card("claude_agent")
    assert not res["ok"] and "signed in" in res["error"]


def test_tui_routes_claude_code_commands():
    from jarvis.tui.app_commands import _is_claude_code_command

    for cmd in ("/claude-code", "/provider claude", "/provider claude-code", "/PROVIDER  Claude"):
        assert _is_claude_code_command(cmd), cmd
    assert not _is_claude_code_command("/provider openrouter")
    assert not _is_claude_code_command("/claude-code use")


def test_client_refuses_cleanly_when_not_set_up():
    client = cac.ClaudeAgentClient(engine_=cac._Engine())
    with pytest.raises(cac.ClaudeAgentError) as ei:
        client.messages.stream(model="sonnet", messages=[{"role": "user", "content": "x"}], tools=[], system="")
    assert ei.value.status_code == 424 and "install" in str(ei.value).lower()


# ─── Plan usage (what Claude Code's /usage shows) ─────────────────────────

USAGE_RAW = {
    "session": {"total_cost_usd": 0},
    "subscription_type": "team",
    "rate_limits_available": True,
    "rate_limits": {
        "five_hour": {"utilization": 41, "resets_at": "2026-10-31T19:20:00+00:00"},
        "seven_day": {"utilization": 25, "resets_at": "2026-11-06T05:00:00+00:00"},
        "extra_usage": {"is_enabled": False},
        "limits": [
            {"kind": "weekly_all", "group": "weekly", "percent": 25, "severity": "normal",
             "resets_at": "2026-11-06T05:00:00+00:00", "is_active": False},
            {"kind": "session", "group": "session", "percent": 93, "severity": "normal",
             "resets_at": "2026-10-31T19:20:00+00:00", "is_active": True},
        ],
        "spend": {"used": {"amount_minor": 1250, "currency": "USD", "exponent": 2},
                  "limit": {"amount_minor": 5000, "currency": "USD", "exponent": 2},
                  "percent": 25, "enabled": True},
    },
}


def test_usage_parses_claude_codes_answer():
    from jarvis.auth import claude_agent_usage as u

    d = u.parse(USAGE_RAW)
    assert d["plan_label"] == "Claude Team" and d["available"]
    assert [(x["kind"], x["percent"], x["level"]) for x in d["limits"]] == [
        ("session", 93, "low"), ("weekly_all", 25, "ok")]
    assert d["limits"][0]["label"] == "Current session"
    assert d["extra"] == {"used": 12.5, "limit": 50.0, "currency": "USD", "percent": 25}
    # An older Claude Code without `limits`: the per-window fields.
    old = json.loads(json.dumps(USAGE_RAW))
    del old["rate_limits"]["limits"]
    assert [(x["kind"], x["percent"]) for x in u.parse(old)["limits"]] == [("session", 41), ("weekly_all", 25)]


def test_usage_follows_rate_limit_events_and_hides_for_other_providers(ready, monkeypatch):
    from jarvis import state
    from jarvis.auth import claude_agent_usage as u

    monkeypatch.setattr(u, "_ask", lambda: u.parse(USAGE_RAW))
    monkeypatch.setattr(state, "provider", "openrouter")
    assert u.public() is None
    monkeypatch.setattr(state, "provider", "claude_agent")
    u.refresh(wait=True)
    snap = u.public(kick=False)
    assert snap["status"] == "ok" and snap["session"]["percent"] == 93
    # A reply's RateLimitEvent updates the window at once (fraction 0–1).
    u.note_rate_limit(NS(rate_limit_type="five_hour", utilization=0.5, resets_at=1793560800,
                         status="allowed"))
    snap = u.public(kick=False)
    assert snap["session"]["percent"] == 50 and snap["session"]["level"] == "ok"
    assert snap["session"]["resets_at"].startswith("2026-")
    u.note_rate_limit(NS(rate_limit_type="seven_day", utilization=0.95, resets_at=None, status="rejected"))
    weekly = [x for x in u.public(kick=False)["limits"] if x["kind"] == "weekly_all"][0]
    assert weekly["percent"] == 95 and weekly["level"] == "low"
    assert weekly["resets_at"] == "2026-11-06T05:00:00+00:00"  # kept from the last ask

    from jarvis.web.state_api import _claude_usage_fields

    assert _claude_usage_fields()["plan_label"] == "Claude Team"


def test_usage_asks_a_running_process(harness):
    """A live `claude` answers the control request (no extra process)."""
    asked = []

    async def brain(fake, blocks):
        await fake.say("hi")

    harness.brains["brain"] = brain
    _run(harness.client, [{"role": "user", "content": "hi"}])
    fake = harness.brains["fakes"][0]

    class _Q:
        async def _send_control_request(self, req):
            asked.append(req)
            return USAGE_RAW

    fake._query = _Q()
    sess = harness.engine.live_session()
    assert sess is not None
    assert sess.command("control", {"subtype": "get_usage"}) == USAGE_RAW
    # (the window lookup after the first reply asks too: get_context_usage)
    assert [r for r in asked if r["subtype"] == "get_usage"] == [{"subtype": "get_usage"}]


# ─── This chat: context · model · cost (Claude Code's status line) ───────


def test_chat_cost_adds_claude_codes_running_total(harness):
    totals = iter([0.5, 1.25, 0.2])  # a running total per process; 0.2 = it was reset

    async def brain(fake, blocks):
        await fake.say("ok", result=False)
        r = ResultMessage(fake.sid)
        r.total_cost_usd = next(totals)
        await fake.emit(r)

    harness.brains["brain"] = brain
    msgs = [{"role": "user", "content": "a"}]
    costs = []
    for text in ("b", "c", None):
        final, _ = _run(harness.client, msgs)
        costs.append(final.cost_usd)
        if text:
            msgs += [{"role": "assistant", "content": final.content}, {"role": "user", "content": text}]
    assert costs == [0.5, 0.75, 0.2]


def test_context_window_and_model_name_from_claude_code(ready, monkeypatch):
    from jarvis import state
    from jarvis.repl import context_budget
    from jarvis.web.state_api import _claude_chat_fields

    ca.note_context("default", {"maxTokens": 1_000_000, "model": "claude-opus-5-5", "percentage": 3})
    ca.store_models([{"value": "default", "displayName": "Default (recommended)", "resolvedModel": "claude-opus-5-5"},
                     {"value": "opus", "displayName": "Opus 5.5", "resolvedModel": "claude-opus-5-5"}])
    assert ca.context_window("default") == 1_000_000 and ca.display_name("default") == "Opus 5.5"
    monkeypatch.setattr(state, "provider", "claude_agent")
    monkeypatch.setattr(state, "MODEL", "default")
    assert context_budget.context_window("default") == 1_000_000  # Jarvis's own guard agrees
    monkeypatch.setattr(state, "total_in", 510_000)
    monkeypatch.setattr(state, "claude_cost_usd", 19.71914)
    assert _claude_chat_fields() == {"context_tokens": 510_000, "context_max": 1_000_000, "context_pct": 51,
                                     "model_label": "Opus 5.5", "cost_usd": 19.7191, "subscription": True}
    monkeypatch.setattr(state, "provider", "openrouter")
    assert _claude_chat_fields() is None
