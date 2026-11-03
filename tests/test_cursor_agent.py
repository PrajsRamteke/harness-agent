"""Cursor provider (``cursor``): status, key handling, the tool bridge, routing.

Nothing here talks to Cursor (and no key is read): the client is driven by a
fake Cursor agent that speaks the SDK's shapes — ``on_delta`` updates, usage
messages, a run result — and calls the session's custom-tool ``execute``
callbacks the way the SDK's tool server does.
"""
from __future__ import annotations

import json
import threading
import time
from types import SimpleNamespace as NS

import pytest

from jarvis.auth import cursor_agent as ca
from jarvis.auth import cursor_agent_client as cac
from jarvis.constants import providers as P


# ─── A fake Cursor agent ──────────────────────────────────────────────────


class FakeRun:
    def __init__(self, agent, steps, on_delta):
        self.agent, self.steps, self.on_delta = agent, steps, on_delta
        self.cancelled = threading.Event()
        self.result = NS(status="finished", usage=NS(input_tokens=0, output_tokens=0), error_code="",
                         error_message="", result="")

    def events(self):
        for step in self.steps:
            if self.cancelled.is_set():
                self.result.status = "cancelled"
                return
            kind = step[0]
            if kind == "text":
                self.on_delta(NS(type="text-delta", text=step[1]))
            elif kind == "think":
                self.on_delta(NS(type="thinking-delta", text=step[1]))
            elif kind == "usage":
                yield NS(sdk_message=NS(type="usage", usage=NS(input_tokens=step[1], output_tokens=step[2])))
                continue
            elif kind == "tool":
                _k, name, args, tid = step
                out = self.agent.sess.executor(name)(args, NS(tool_call_id=tid))
                self.agent.results.append(out)
            elif kind == "wait":  # until cancelled
                while not self.cancelled.wait(0.05):
                    pass
                self.result.status = "cancelled"
                return
            elif kind == "error":
                self.result.status = "error"
                self.result.error_message = step[1]
                self.result.error_code = step[2] if len(step) > 2 else ""
                return
            yield NS(sdk_message=None)

    def wait(self):
        return self.result

    def cancel(self):
        self.cancelled.set()


class FakeAgent:
    def __init__(self, sess, brain, log):
        self.sess, self.brain, self.log = sess, brain, log
        self.results: list = []
        self.closed = False

    def send(self, message, on_delta=None):
        text, images = message
        self.log.append({"agent": self.sess.n, "text": text, "images": images})
        return FakeRun(self, self.brain(len(self.log), text), on_delta)

    def close(self):
        self.closed = True


@pytest.fixture
def harness():
    brains = {"brain": lambda n, text: [("text", "ok")]}
    log: list = []
    agents: list[FakeAgent] = []

    def factory(sess):
        agent = FakeAgent(sess, lambda n, t: brains["brain"](n, t), log)
        agents.append(agent)
        return agent

    eng = cac._Engine(factory=factory)
    client = cac.CursorAgentClient(engine_=eng)
    yield NS(brains=brains, log=log, agents=agents, eng=eng, client=client)
    eng.close_all()


SYSTEM = [{"type": "text", "text": "Be brief.", "cache_control": {"type": "ephemeral"}}]
TOOLS = [{"name": "read_file", "description": "Read a file.",
          "input_schema": {"type": "object", "properties": {"path": {"type": "string"}}, "required": ["path"]}},
         {"name": "run_bash", "description": "Run a command.",
          "input_schema": {"type": "object", "properties": {"cmd": {"type": "string"}}}}]


def user(text):
    return {"role": "user", "content": [{"type": "text", "text": text}]}


def ask(client, messages, **kw):
    return client.messages.stream(model=kw.pop("model", "composer-2.5"), messages=messages, system=SYSTEM,
                                  tools=TOOLS, thinking=kw.pop("thinking", None), max_tokens=100)


def drain(stream):
    return list(stream.delta_stream), stream.get_final_message()


def assistant_of(final):
    content = []
    for b in final.content:
        if b.type == "text":
            content.append({"type": "text", "text": b.text})
        elif b.type == "tool_use":
            content.append({"type": "tool_use", "id": b.id, "name": b.name, "input": b.input})
    return {"role": "assistant", "content": content}


def tool_result(tid, text, error=False):
    return {"role": "user", "content": [{"type": "tool_result", "tool_use_id": tid, "content": text,
                                         "is_error": error}]}


# ─── The tool bridge ──────────────────────────────────────────────────────


def test_plain_reply_streams_text_thinking_and_usage(harness):
    harness.brains["brain"] = lambda n, t: [("think", "hm"), ("text", "Hel"), ("text", "lo"),
                                           ("usage", 120, 7)]
    deltas, final = drain(ask(harness.client, [user("hi")]))
    assert deltas == [("thinking", "hm"), ("text", "Hel"), ("text", "lo")]
    assert final.stop_reason == "end_turn"
    assert [b.type for b in final.content] == ["thinking", "text"]
    assert final.content[1].text == "Hello"
    assert (final.usage.input_tokens, final.usage.output_tokens, final.usage.total_tokens) == (120, 7, 127)


def test_first_message_carries_jarvis_instructions(harness):
    drain(ask(harness.client, [user("hi")]))
    text = harness.log[0]["text"]
    assert text.startswith("<jarvis-instructions>")
    assert "Be brief." in text and text.rstrip().endswith("hi")
    assert "Cursor's own built-in tools are not available" in text


def test_tool_call_round_trip_through_jarvis(harness):
    harness.brains["brain"] = lambda n, t: (
        [("text", "Reading."), ("tool", "read_file", {"path": "a.py"}, "call_1"), ("text", "Done.")]
        if n == 1 else [("text", "unused")])
    msgs = [user("read a.py")]
    deltas, final = drain(ask(harness.client, msgs))
    assert final.stop_reason == "tool_use"
    tools = [b for b in final.content if b.type == "tool_use"]
    assert [(t.id, t.name, t.input) for t in tools] == [("call_1", "read_file", {"path": "a.py"})]
    assert ("tool_input", (0, "read_file", json.dumps({"path": "a.py"}))) in deltas

    msgs += [assistant_of(final), tool_result("call_1", "print('x')")]
    deltas2, final2 = drain(ask(harness.client, msgs))
    assert final2.stop_reason == "end_turn"
    assert "".join(c for k, c in deltas2 if k == "text") == "Done."
    # Cursor got Jarvis's output as the tool's result; one agent served both requests.
    assert harness.agents[0].results == [{"content": [{"type": "text", "text": "print('x')"}], "isError": False}]
    assert len(harness.agents) == 1 and len(harness.log) == 1


def test_parallel_tool_calls_arrive_as_one_batch(harness):
    def brain(n, t):
        return [("tool", "read_file", {"path": "a"}, "t1")]

    harness.brains["brain"] = brain
    # Two calls from two callback threads at once, like the SDK's tool server.
    orig_send = FakeAgent.send

    def send(self, message, on_delta=None):
        run = orig_send(self, message, on_delta)
        second = threading.Thread(target=lambda: self.results.append(
            self.sess.executor("run_bash")({"cmd": "ls"}, NS(tool_call_id="t2"))), daemon=True)
        second.start()
        return run

    FakeAgent.send = send
    try:
        _d, final = drain(ask(harness.client, [user("go")]))
    finally:
        FakeAgent.send = orig_send
    assert final.stop_reason == "tool_use"
    assert sorted(b.id for b in final.content if b.type == "tool_use") == ["t1", "t2"]
    msgs = [user("go"), assistant_of(final),
            {"role": "user", "content": [
                {"type": "tool_result", "tool_use_id": "t1", "content": "A"},
                {"type": "tool_result", "tool_use_id": "t2", "content": "B", "is_error": True}]}]
    harness.brains["brain"] = lambda n, t: [("text", "fin")]
    _d, final2 = drain(ask(harness.client, msgs))
    assert final2.stop_reason == "end_turn"
    got = {r["content"][0]["text"]: r["isError"] for r in harness.agents[0].results}
    assert got == {"A": False, "B": True}


def test_next_turn_continues_the_same_agent(harness):
    msgs = [user("one")]
    _d, final = drain(ask(harness.client, msgs))
    msgs += [assistant_of(final), user("two")]
    drain(ask(harness.client, msgs))
    assert len(harness.agents) == 1
    assert harness.log[1]["text"] == "two"          # no instructions or checkpoint again
    assert harness.log[1]["agent"] == harness.log[0]["agent"]


def test_tool_change_starts_a_new_agent_with_a_checkpoint(harness):
    msgs = [user("one")]
    _d, final = drain(ask(harness.client, msgs))
    msgs += [assistant_of(final), user("two")]
    stream = harness.client.messages.stream(model="composer-2.5", messages=msgs, system=SYSTEM,
                                            tools=TOOLS[:1], thinking=None)
    drain(stream)
    assert len(harness.agents) == 2
    text = harness.log[1]["text"]
    assert "<conversation-checkpoint>" in text and "User: one" in text and text.rstrip().endswith("two")


def test_unknown_history_starts_with_a_checkpoint(harness):
    msgs = [user("old question"), {"role": "assistant", "content": [{"type": "text", "text": "old answer"}]},
            user("new question")]
    drain(ask(harness.client, msgs))
    text = harness.log[0]["text"]
    assert "User: old question" in text and "Assistant: old answer" in text
    assert text.rstrip().endswith("new question")


def test_send_now_text_rides_on_the_last_tool_result(harness):
    harness.brains["brain"] = lambda n, t: ([("tool", "read_file", {"path": "a"}, "c1")] if n == 1 else [])
    msgs = [user("go")]
    _d, final = drain(ask(harness.client, msgs))
    msgs += [assistant_of(final), {"role": "user", "content": [
        {"type": "tool_result", "tool_use_id": "c1", "content": "data"},
        {"type": "text", "text": "also check b"}]}]
    drain(ask(harness.client, msgs))
    texts = [c["text"] for c in harness.agents[0].results[0]["content"]]
    assert texts[0] == "data" and "also check b" in texts[1]


def test_images_reach_cursor(harness):
    msg = {"role": "user", "content": [
        {"type": "text", "text": "what is this"},
        {"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": "QUJD"}}]}
    drain(ask(harness.client, [msg]))
    assert harness.log[0]["images"] == [("image/png", "QUJD")]


def test_interrupted_reply_continues_in_the_same_agent(harness):
    harness.brains["brain"] = lambda n, t: [("text", "start"), ("wait",)] if n == 1 else [("text", "back")]
    msgs = [user("long task")]
    stream = ask(harness.client, msgs)
    it = iter(stream.delta_stream)
    assert next(it) == ("text", "start")
    stream.close()                       # Esc
    msgs += [user("continue")]            # Jarvis keeps no partial reply: the user asks again
    deltas, _final = drain(ask(harness.client, msgs))
    assert len(harness.agents) == 1
    assert "back" in "".join(c for k, c in deltas if k == "text")
    assert "[The user stopped your previous reply.]" in harness.log[1]["text"]


def test_stopped_tool_call_is_released_as_an_error(harness):
    harness.brains["brain"] = lambda n, t: [("tool", "read_file", {"path": "a"}, "c1"), ("text", "x")]
    stream = ask(harness.client, [user("go")])
    _d, final = drain(stream)
    assert final.stop_reason == "tool_use"
    harness.eng.sessions[0].interrupt()
    time.sleep(0.3)
    assert harness.agents[0].results and harness.agents[0].results[0]["isError"] is True


# ─── Errors ───────────────────────────────────────────────────────────────


@pytest.mark.parametrize("message,code,status", [
    ("Invalid API key", "unauthenticated", 401),
    ("rate limit exceeded", "", 429),
    ("prompt exceeds the context limit", "", 400),
    ("something broke", "", 500),
])
def test_run_errors_become_status_errors(harness, message, code, status):
    harness.brains["brain"] = lambda n, t: [("error", message, code)]
    with pytest.raises(cac.CursorAgentError) as e:
        drain(ask(harness.client, [user("hi")]))
    assert e.value.status_code == status
    assert message in str(e.value)


def test_sdk_exceptions_map_to_statuses():
    class AuthenticationError(Exception):
        pass

    class RateLimitError(Exception):
        pass

    assert cac.sdk_error(AuthenticationError("nope")).status_code == 401
    assert "API key" in str(cac.sdk_error(AuthenticationError("nope")))
    assert cac.sdk_error(RateLimitError("slow down")).status_code == 429
    assert cac.sdk_error(RuntimeError("x")).status_code == 500


def test_a_failed_start_is_an_error_not_a_hang(harness):
    def boom(sess):
        raise RuntimeError("bridge didn't start")

    eng = cac._Engine(factory=boom)
    with pytest.raises(cac.CursorAgentError) as e:
        cac.CursorAgentClient(engine_=eng).messages.stream(model="composer-2.5", messages=[user("hi")])
    assert "didn't start" in str(e.value)


def test_client_refuses_cleanly_when_not_set_up(no_key, monkeypatch):
    monkeypatch.setattr(ca, "sdk_version", lambda: "1.2.0")
    with pytest.raises(cac.CursorAgentError) as e:
        cac.CursorAgentClient(engine_=cac._Engine()).messages.stream(model="composer-2.5", messages=[user("hi")])
    assert e.value.status_code == 401 and "API key" in str(e.value)
    monkeypatch.setattr(ca, "sdk_version", lambda: None)
    with pytest.raises(cac.CursorAgentError) as e:
        cac.CursorAgentClient(engine_=cac._Engine()).messages.stream(model="composer-2.5", messages=[user("hi")])
    assert e.value.status_code == 424 and "pip install cursor-sdk" in str(e.value)


# ─── Spec ─────────────────────────────────────────────────────────────────


def test_spec_maps_thinking_and_strips_cache_control():
    on = cac.build_spec(model="m", system=SYSTEM, tools=TOOLS, thinking={"type": "adaptive", "effort": "high"})
    off = cac.build_spec(model="m", system=SYSTEM, tools=TOOLS, thinking={"type": "disabled"})
    junk = cac.build_spec(model="m", system="s", tools=None, thinking={"type": "adaptive", "effort": "bogus"})
    assert on.effort == "high" and off.effort == "" and junk.effort == ""
    assert on.system == "Be brief." and on.key != off.key or on.effort != off.effort
    assert [t[0] for t in on.tools] == ["read_file", "run_bash"]
    assert "cache_control" not in on.tools[0][2]


# ─── Status, key, models ──────────────────────────────────────────────────


@pytest.fixture
def no_key(monkeypatch, tmp_path):
    """No key, no cached status or models: every file Cursor's module reads is in tmp_path."""
    monkeypatch.delenv("CURSOR_API_KEY", raising=False)
    monkeypatch.setattr(ca, "CURSOR_KEY_FILE", tmp_path / "cursor_key")
    from jarvis.constants.api_keys import api_key_spec

    monkeypatch.setitem(api_key_spec("cursor"), "file_path", tmp_path / "cursor_key")  # the web / key dialog's copy
    monkeypatch.setattr(ca.catalog_cache, "CACHE_DIR", tmp_path / "model_catalog")
    ca.reset()
    yield tmp_path / "cursor_key"
    ca.reset()


def test_key_comes_from_env_then_file(no_key, monkeypatch):
    assert ca.api_key() == "" and ca.key_source() == "" and not ca.has_key()
    no_key.write_text("crsr_file\n")
    assert (ca.api_key(), ca.key_source()) == ("crsr_file", "file")
    monkeypatch.setenv("CURSOR_API_KEY", " crsr_env ")
    assert (ca.api_key(), ca.key_source()) == ("crsr_env", "env")


def test_problems_come_in_order(no_key, monkeypatch):
    monkeypatch.setattr(ca, "sdk_version", lambda: None)
    s = ca.status()
    code, msg, fix = s.problem()
    assert code == "no_sdk" and "pip install cursor-sdk" in fix and not s.ready
    monkeypatch.setattr(ca, "sdk_version", lambda: "1.2.0")
    assert ca.status().problem()[0] == "no_key"
    no_key.write_text("crsr_x")
    s = ca.status()
    assert s.ready and s.problem() == ("", "", "")
    s.rejected = True
    assert s.problem()[0] == "bad_key" and not s.ready


def test_the_key_is_never_in_status_or_its_cache(no_key, monkeypatch):
    monkeypatch.setattr(ca, "sdk_version", lambda: "1.2.0")
    no_key.write_text("crsr_supersecret")
    s = ca.status()
    blob = json.dumps(s.public()) + json.dumps(s.to_json())
    assert "supersecret" not in blob


def test_status_is_cached_between_calls(no_key, monkeypatch):
    monkeypatch.setattr(ca, "sdk_version", lambda: "1.2.0")
    no_key.write_text("crsr_x")
    saved = ca.Status(checked_at=123.0, email="me@x.com", rejected=False)
    ca.catalog_cache.write(ca.STATUS_CACHE, saved.to_json())
    ca.reset()
    s = ca.status()
    assert s.email == "me@x.com" and s.checked_at == 123.0 and s.ready
    assert ca.status().account_line() == "me@x.com"


def test_models_come_from_cursor_then_the_seed(no_key):
    assert [m["id"] for m in ca.models()] == ["composer-2.5", "auto-smart"]
    assert ca.default_model() == "composer-2.5" and not ca.models_are_live()
    rows = ca.store_models([
        NS(id="gpt-x", display_name="GPT X", description="fast", variants=(), parameters=(
            NS(id="reasoning_effort", display_name="Effort",
               values=(NS(value="low"), NS(value="high"), NS(value="extra-high"), NS(value="none"))),)),
        {"id": "gpt-x", "label": "dup"},
        NS(id="sonnet-y", display_name="Sonnet Y", description="", variants=(), parameters=()),
    ])
    assert [r["id"] for r in rows] == ["gpt-x", "sonnet-y"]
    ca.reset()
    assert ca.models_are_live() and ca.default_model() == "gpt-x"
    assert ca.effort_levels("gpt-x") == ("low", "high", "xhigh")
    assert ca.get_model("gpt-x")["thinking_off"] is True
    assert ca.effort_levels("sonnet-y") == () and ca.get_model("sonnet-y")["thinking_off"] is None
    assert ca.models_for_picker()[0] == ("gpt-x", "GPT X · fast · Other models")


def test_model_selection_only_uses_parameters_cursor_listed(no_key):
    pytest.importorskip("cursor_sdk")
    ca.store_models([NS(id="gpt-x", display_name="GPT X", description="", variants=(), parameters=(
        NS(id="reasoning_effort", display_name="Effort", values=(NS(value="low"), NS(value="extra-high"))),))])
    ca.reset()
    sel = ca.model_selection("gpt-x", "xhigh")
    assert sel.id == "gpt-x" and [(p.id, p.value) for p in sel.params] == [("reasoning_effort", "extra-high")]
    assert ca.model_selection("gpt-x", "medium") == "gpt-x"      # not offered → the bare id
    assert ca.model_selection("composer-2.5", "high") == "composer-2.5"
    assert ca.model_selection("gpt-x", "") == "gpt-x"


# ─── Provider registration ────────────────────────────────────────────────


def test_provider_is_registered_everywhere_a_provider_must_be():
    assert P.PROVIDER_CURSOR == "cursor" and "cursor" in P.PROVIDERS
    assert P.PROVIDER_LABELS["cursor"] == "Cursor" and P.MODEL_SOURCE_LABELS["cursor"] == "Cursor"
    assert "cursor" in P.MODEL_SOURCES
    from jarvis.constants.api_keys import api_key_spec_for_provider

    spec = api_key_spec_for_provider("cursor")
    assert spec["env_var"] == "CURSOR_API_KEY" and spec["file_path"].name == "cursor_key"


def test_models_are_strictly_cursors_own(no_key):
    assert P.model_belongs_to_provider("composer-2.5", "cursor")
    assert not P.model_belongs_to_provider("claude-sonnet-4", "cursor")   # ids overlap Anthropic's: never guessed
    assert P.normalize_model_for_provider("claude-sonnet-4", "cursor") == "composer-2.5"
    assert P.normalize_model_for_provider("auto-smart", "cursor") == "auto-smart"
    assert dict(P.models_for("cursor")).keys() >= {"composer-2.5"}
    assert dict(P.models_for_source("cursor")).keys() >= {"composer-2.5"}


def test_cursor_usage_is_the_plan_not_a_per_token_price():
    assert P.model_pricing("composer-2.5", "cursor") == (0.0, 0.0)
    assert P.model_supports_images("composer-2.5", "cursor") is True


def test_cursor_is_connected_only_when_ready(no_key, monkeypatch):
    monkeypatch.setattr(ca, "sdk_version", lambda: "1.2.0")
    assert "cursor" not in P.connected_model_sources()
    no_key.write_text("crsr_x")
    assert "cursor" in P.connected_model_sources() and "cursor" in P.connected_providers()


def test_thinking_effort_follows_the_models_own_parameter(no_key):
    from jarvis.auth import thinking_caps as tc

    ca.store_models([NS(id="gpt-x", display_name="GPT X", description="", variants=(), parameters=(
        NS(id="reasoning_effort", display_name="Effort", values=(NS(value="low"), NS(value="high"))),)),
        NS(id="plain", display_name="Plain", description="", variants=(), parameters=())])
    ca.reset()
    caps = tc.thinking_caps("gpt-x", "cursor")
    assert caps.known and caps.levels == ("low", "high") and caps.source == "Cursor"
    assert not tc.thinking_caps("plain", "cursor").known       # nothing to steer: no "doesn't think" notes


# ─── Key changes, the web card, error cards, startup ──────────────────────


def test_a_new_key_clears_the_old_keys_verdict(no_key, monkeypatch):
    monkeypatch.setattr(ca, "sdk_version", lambda: "1.2.0")
    no_key.write_text("crsr_old_key_value")
    ca.catalog_cache.write(ca.STATUS_CACHE, ca.Status(
        rejected=True, key_fp=ca._fingerprint("crsr_old_key_value"), email="a@b.c", checked_at=5.0).to_json())
    ca.reset()
    assert ca.status().rejected and not ca.status().ready          # Cursor refused *that* key
    no_key.write_text("crsr_new_key_value")
    s = ca.status()
    assert not s.rejected and s.ready and s.checked_at == 0 and s.email == ""   # …not this one
    assert "old_key" not in json.dumps(ca.catalog_cache.read(ca.STATUS_CACHE)[0])


def test_verify_key_reads_cursors_answer(monkeypatch):
    class AuthenticationError(Exception):
        pass

    class Bridge:
        def __init__(self, error):
            self.error = error

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def me(self, api_key=None):
            if self.error:
                raise self.error

    monkeypatch.setattr(ca, "sdk_version", lambda: "1.2.0")
    for error, want in ((None, "ok"), (AuthenticationError("Invalid User API Key"), "rejected"),
                        (ConnectionError("offline"), "unknown")):
        monkeypatch.setattr(ca, "_bridge_client", lambda e=error: Bridge(e))
        assert ca.verify_key("crsr_x") == want
    monkeypatch.setattr(ca, "sdk_version", lambda: None)
    assert ca.verify_key("crsr_x") == "unknown"


def test_web_card_needs_the_key_and_the_sdk(no_key, monkeypatch):
    from jarvis.web import providers_api as pv

    monkeypatch.setattr(ca, "sdk_version", lambda: None)
    row = {r["id"]: r for r in pv.list_providers()["providers"]}["cursor"]
    assert row["kind"] == "key" and not row["connected"] and row["env_var"] == "CURSOR_API_KEY"
    assert row["checked"] and row["sdk"]["problem"] == "no_sdk" and "pip install cursor-sdk" in row["sdk"]["sdk_install_cmd"]
    assert "crsr_" not in json.dumps(row)

    no_key.write_text("crsr_abcdefghijklmnop")
    row = pv.card_row(pv.CARD_BY_ID["cursor"])
    assert row["connected"] and row["source"] == "file" and row["hint"] == "…mnop"
    res = pv.use_card("cursor")                      # key saved, SDK missing: say how to fix it
    assert not res["ok"] and res["problem"] == "no_sdk" and "pip install" in res["fix"]
    assert "crsr_abcdefghijklmnop" not in json.dumps(res)

    monkeypatch.setattr(ca, "sdk_version", lambda: "1.2.0")
    ca.status().rejected = True                      # Cursor refused it
    res = pv.use_card("cursor")
    assert not res["ok"] and res["problem"] == "bad_key"


def test_pasted_keys_are_checked_by_the_sdk_before_they_are_saved(no_key, monkeypatch):
    from jarvis.web import providers_api as pv

    seen = []
    monkeypatch.setattr(ca, "verify_key", lambda key: seen.append(key) or "rejected")
    res = pv.save_key("cursor", "export CURSOR_API_KEY=crsr_0123456789abcdef", use=True,
                      run_action=lambda *a: {"ok": True})
    assert seen == ["crsr_0123456789abcdef"] and not res["ok"] and res["rejected"]
    assert not no_key.exists()                       # a refused key is never written

    monkeypatch.setattr(ca, "verify_key", lambda key: "ok")
    calls = []
    res = pv.save_key("cursor", "crsr_0123456789abcdef", use=True,
                      run_action=lambda action, data: calls.append((action, data)) or {"ok": True, "message": "m"})
    assert res["ok"] and res["verified"] and calls == [("provider_key_saved", {"id": "cursor", "use": True,
                                                                              "replaced": False})]
    assert no_key.read_text() == "crsr_0123456789abcdef" and oct(no_key.stat().st_mode)[-3:] == "600"


def test_cursor_errors_point_at_the_key_not_a_jarvis_setting(monkeypatch):
    from jarvis import state
    from jarvis.repl import api_errors

    monkeypatch.setattr(state, "provider", "cursor")
    monkeypatch.setattr(state, "MODEL", "composer-2.5")
    rep = api_errors.classify(cac._error(401, "Invalid User API Key"))
    assert rep.title == "Cursor didn't accept the API key"
    assert [a.command for a in rep.actions][0] == "/key"
    rep = api_errors.classify(cac._error(429, "slow down"))
    assert rep.title == "Cursor limit reached"


def test_cursor_can_be_pinned_and_resumed_at_startup(no_key, monkeypatch):
    from jarvis.auth import client as auth_client

    monkeypatch.setattr(ca, "sdk_version", lambda: "1.2.0")
    monkeypatch.setenv("HARNESS_PROVIDER", "cursor")
    assert auth_client._resolve_provider(interactive=False) == "cursor"
    monkeypatch.delenv("HARNESS_PROVIDER")
    assert auth_client._build_cursor_client() is None                  # no key yet
    no_key.write_text("crsr_x")
    assert isinstance(auth_client._build_cursor_client(), cac.CursorAgentClient)
    client, provider, mode = auth_client.build_side_client("cursor")
    assert isinstance(client, cac.CursorAgentClient) and provider == "cursor"
    no_key.unlink()
    with pytest.raises(RuntimeError, match="Cursor SDK and an API key"):
        auth_client.build_side_client("cursor")


def test_thinking_kwargs_for_cursor(no_key):
    from jarvis.repl import thinking

    ca.store_models([
        NS(id="gpt-x", display_name="GPT X", description="", variants=(), parameters=(
            NS(id="reasoning_effort", display_name="Effort", values=(NS(value="low"), NS(value="high"))),)),
        NS(id="gpt-y", display_name="GPT Y", description="", variants=(), parameters=(
            NS(id="reasoning_effort", display_name="Effort",
               values=(NS(value="none"), NS(value="low"), NS(value="high"))),)),
    ])
    ca.reset()
    kw: dict = {}
    thinking.apply(kw, provider="cursor", model="gpt-x", client=None, think_mode=True, effort="high")
    assert kw["thinking"] == {"type": "adaptive", "effort": "high"}
    kw = {}                                          # can't be switched off: its lowest level
    thinking.apply(kw, provider="cursor", model="gpt-x", client=None, think_mode=False, effort="high")
    assert kw["thinking"] == {"type": "adaptive", "effort": "low"}
    kw = {}                                          # lists "none": really off
    thinking.apply(kw, provider="cursor", model="gpt-y", client=None, think_mode=False, effort="high")
    assert kw["thinking"] == {"type": "disabled"}
    kw = {}                                          # a model with nothing to steer: effort passes through
    thinking.apply(kw, provider="cursor", model="composer-2.5", client=None, think_mode=True, effort="medium")
    assert kw["thinking"]["type"] == "adaptive"


def test_provider_command_switches_to_cursor(no_key, monkeypatch):
    from jarvis import state
    from jarvis.commands import control

    printed: list[str] = []
    monkeypatch.setattr(control.console, "print", lambda *a, **k: printed.append(" ".join(map(str, a))))
    monkeypatch.setattr(control, "header_panel", lambda: None)
    monkeypatch.setattr(control, "save_last_model", lambda: None)
    monkeypatch.setattr(control, "_secure_write", lambda *a, **k: None)
    monkeypatch.setattr(state, "provider", "opencode_zen")
    monkeypatch.setattr(state, "MODEL", "something")
    monkeypatch.setattr(ca, "sdk_version", lambda: "1.2.0")

    control._handle_provider("cursor")                        # no key: refused, provider unchanged
    assert state.provider == "opencode_zen" and any("No Cursor API key" in p for p in printed)

    no_key.write_text("crsr_x")
    monkeypatch.setattr(ca, "refresh", lambda: ca.status())   # the real one starts the bridge
    control._handle_provider("cursor")
    assert state.provider == "cursor" and state.MODEL == "composer-2.5"
    assert isinstance(state.client, cac.CursorAgentClient)


def test_saving_the_key_rebuilds_the_live_client(no_key, monkeypatch):
    from jarvis import state
    from jarvis.commands import control

    monkeypatch.setattr(state, "provider", "cursor")
    monkeypatch.setattr(ca, "sdk_version", lambda: "1.2.0")
    monkeypatch.setattr(ca, "refresh_in_background", lambda: None)
    no_key.write_text("crsr_x")
    assert control.apply_key_change("cursor") == "now in use for Cursor"
    assert isinstance(state.client, cac.CursorAgentClient)


# ─── Usage pools ──────────────────────────────────────────────────────────

LIMIT_TEXT = ("You've hit your usage limit Switch to a different model or set a Spend Limit to continue "
              "with Sonnet. Your usage limits will reset when your monthly cycle ends on 11/21/2026.")


def _seed_models(models):
    ca.store_models([NS(id=i, display_name=i.title(), description="", variants=(), parameters=()) for i in models])
    ca.reset()


def test_models_split_into_cursors_pool_and_other_models():
    for m in ("composer-2.5", "grok-4.7", "Grok-4.6", "auto", "auto-smart"):
        assert ca.pool_of(m) == "cursor", m
    for m in ("claude-sonnet-5-5", "gpt-5.6-sol", "gemini-4-argon", "muse-wonder", ""):
        assert ca.pool_of(m) == "other", m


def test_picker_lists_cursor_models_first_and_tags_each_pool(no_key):
    _seed_models(["claude-opus-5-5", "grok-4.7", "gpt-5.6-sol", "composer-2.5"])
    rows = ca.models_for_picker()
    assert [r[0] for r in rows] == ["grok-4.7", "composer-2.5", "claude-opus-5-5", "gpt-5.6-sol"]
    assert rows[0][1].endswith("Cursor models") and rows[2][1].endswith("Other models")


def test_a_429_marks_only_that_pool_with_the_reset_date(no_key):
    _seed_models(["grok-4.7", "claude-sonnet-5-5"])
    assert ca.limits_public() == []
    ca.note_limit("claude-sonnet-5-5", LIMIT_TEXT)
    lim = ca.limit_for("gpt-5.6-sol")                       # same (third-party) pool
    assert lim and time.localtime(lim["until"])[:3] == (2026, 11, 21)
    assert ca.limit_for("grok-4.7") is None                 # Cursor's own pool still has room
    rows = dict(ca.models_for_picker())
    assert "limit reached · resets Nov 21" in rows["claude-sonnet-5-5"] and "limit" not in rows["grok-4.7"]
    pub = ca.limits_public()
    assert [(p["pool"], p["label"], p["until_text"]) for p in pub] == [("other", "Other models", "Nov 21")]
    assert ca.other_pool_label("claude-sonnet-5-5") == "Cursor models"
    ca.note_limit("grok-4.7", "slow down")                  # no date: believed for hours, not forever
    assert ca.other_pool_label("claude-sonnet-5-5") == ""
    assert 0 < ca.limit_for("grok-4.7")["until"] - time.time() <= ca.LIMIT_FALLBACK_SECONDS


def test_a_limit_ends_at_its_reset_or_when_the_model_answers(no_key):
    ca.note_limit("claude-sonnet-5-5", "resets on 01/01/2020")   # already past
    assert ca.limit_for("claude-sonnet-5-5") is None
    ca.note_limit("claude-sonnet-5-5", LIMIT_TEXT)
    ca.clear_limit("claude-sonnet-5-5")
    assert ca.limit_for("claude-sonnet-5-5") is None


def test_the_stream_records_a_429_and_a_good_reply_clears_it(harness, no_key):
    harness.brains["brain"] = lambda n, t: [("error", LIMIT_TEXT)] if n == 1 else [("text", "hi")]
    with pytest.raises(cac.CursorAgentError):
        harness.brains["brain"] = lambda n, t: [("error", "rate limit: " + LIMIT_TEXT)]
        drain(ask(harness.client, [user("hi")], model="claude-sonnet-5-5"))
    assert ca.limit_for("claude-sonnet-5-5") and not ca.limit_for("composer-2.5")
    harness.brains["brain"] = lambda n, t: [("text", "ok")]
    drain(ask(harness.client, [user("again")], model="claude-sonnet-5-5"))
    assert ca.limit_for("claude-sonnet-5-5") is None


def test_the_limit_card_points_at_the_other_pool(no_key, monkeypatch):
    from jarvis import state
    from jarvis.repl import api_errors

    monkeypatch.setattr(state, "provider", "cursor")
    monkeypatch.setattr(state, "MODEL", "claude-sonnet-5-5")
    err = cac._error(429, LIMIT_TEXT)
    ca.note_limit("claude-sonnet-5-5", str(err))
    rep = api_errors.classify(err)
    assert rep.title == "Cursor limit reached"
    assert "Other models usage" in rep.message and "Cursor models draw on a separate pool" in rep.message
    assert [a.command for a in rep.actions][0] == "/model" and "provider" not in rep.message.lower()


def test_web_card_reports_the_limits(no_key, monkeypatch):
    from jarvis.web import providers_api as pv

    monkeypatch.setattr(ca, "sdk_version", lambda: "1.2.0")
    no_key.write_text("crsr_abcdefghijklmnop")
    ca.note_limit("claude-sonnet-5-5", LIMIT_TEXT)
    row = pv.card_row(pv.CARD_BY_ID["cursor"])
    assert row["sdk"]["limits"][0]["label"] == "Other models" and row["sdk"]["limits"][0]["until_text"] == "Nov 21"


def test_model_picker_marks_cursors_own_pool(no_key, monkeypatch):
    from jarvis.web import pickers_api

    _seed_models(["grok-4.7", "claude-opus-5-5"])
    monkeypatch.setattr(pickers_api, "model_picker_rows",
                        lambda: [("cursor", m, "d") for m in ("grok-4.7", "claude-opus-5-5")]
                        + [("openrouter", "grok-4.7", "d")])
    pools = [(m["source"], m["model_id"], m["pool"]) for m in pickers_api.list_models()["models"]]
    assert pools == [("cursor", "grok-4.7", "cursor"), ("cursor", "claude-opus-5-5", "other"),
                     ("openrouter", "grok-4.7", "")]
