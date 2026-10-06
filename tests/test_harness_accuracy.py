"""Read/edit accuracy, tool-name repair, and prompt-cache stability.

Covers: numbered + bounded read_file, truncation notes, bash head+tail,
edit fallbacks (line-number prefixes, closest-match hint), syntax warnings,
stale-file guard, tool-name aliases, sticky tool groups, the per-turn system
prompt, stepped trimming, cache breakpoints and the request-loop recoveries.
"""
from __future__ import annotations

import os
import time
from types import SimpleNamespace

import anthropic
import httpx
import pytest

from jarvis import state
from jarvis.constants import MAX_TOOL_OUTPUT, READ_MAX_LINES
from jarvis.repl import prompt_cache
from jarvis.tools import files


@pytest.fixture
def proj(tmp_path, monkeypatch):
    from jarvis import constants
    from jarvis.constants import set_cwd

    orig = constants.CWD
    monkeypatch.chdir(tmp_path)
    set_cwd(tmp_path.resolve())
    monkeypatch.setattr(files, "project_scope_error", lambda *a, **k: None)
    yield tmp_path.resolve()
    set_cwd(orig)


# ── read_file ────────────────────────────────────────────────────────────


def test_read_returns_numbered_lines(proj):
    (proj / "a.py").write_text("x = 1\ny = 2\n")
    assert files.read_file_tool("a.py") == "1\tx = 1\n2\ty = 2"


def test_long_file_is_bounded_with_a_continue_note(proj):
    n = READ_MAX_LINES + 500
    (proj / "big.txt").write_text("".join(f"line {i}\n" for i in range(n)))
    out = files.read_file_tool("big.txt")
    rows = out.split("\n")
    assert rows[0] == "1\tline 0"
    assert rows[-2] == f"{READ_MAX_LINES}\tline {READ_MAX_LINES - 1}"
    assert f"of {n}" in rows[-1] and f"offset={READ_MAX_LINES}" in rows[-1]
    nxt = files.read_file_tool("big.txt", offset=READ_MAX_LINES)
    assert nxt.split("\n")[0] == f"{READ_MAX_LINES + 1}\tline {READ_MAX_LINES}"
    assert "[showing" not in nxt  # the rest fits


def test_whole_mid_sized_file_reaches_the_model(proj):
    """The old 6,000-char cap in render.py cut files at ~170 lines, silently."""
    from jarvis.repl.render import _cap_tool_output

    (proj / "m.py").write_text("".join(f"value_{i} = {i}  # some comment text\n" for i in range(900)))
    out = files.read_file_tool("m.py")
    assert len(out) > MAX_TOOL_OUTPUT
    assert _cap_tool_output("read_file", out) == out
    assert out.split("\n")[-1].startswith("900\t")


def test_slice_and_edge_cases(proj):
    (proj / "s.txt").write_text("a\nb\nc\n")
    assert files.read_file_tool("s.txt", offset=1, limit=1) == "2\tb"
    assert "past the end" in files.read_file_tool("s.txt", offset=10)
    (proj / "e.txt").write_text("")
    assert files.read_file_tool("e.txt") == "[empty file]"
    assert files.read_file_tool("nope.txt").startswith("ERROR")


def test_very_long_lines_are_clipped(proj):
    (proj / "min.js").write_text("x" * 10_000 + "\n")
    out = files.read_file_tool("min.js")
    assert "line truncated: 10,000 chars" in out and len(out) < 3000


def test_internal_read_file_stays_raw(proj):
    (proj / "r.txt").write_text("hello\nworld\n")
    assert files.read_file("r.txt") == "hello\nworld\n"


def test_func_routes_to_numbered_reader():
    from jarvis.tools import FUNC

    assert FUNC["read_file"] is files.read_file_tool


# ── tool output caps ─────────────────────────────────────────────────────


def test_other_tools_say_when_output_was_cut():
    from jarvis.repl.render import _cap_tool_output

    out = _cap_tool_output("search_code", "x" * (MAX_TOOL_OUTPUT + 50))
    assert out.startswith("x" * MAX_TOOL_OUTPUT)
    assert "output truncated" in out and f"{MAX_TOOL_OUTPUT + 50:,}" in out
    assert _cap_tool_output("search_code", "short") == "short"


def test_bash_keeps_head_and_tail_within_the_cap():
    from jarvis.tools.shell import _format_result

    body = "FIRST ERROR\n" + "noise\n" * 5000 + "3 failed, 10 passed\n"
    out = _format_result("pytest", 1, body)
    assert out.startswith("$ pytest\nexit=1\nFIRST ERROR")
    assert out.rstrip().endswith("3 failed, 10 passed")
    assert "chars of output omitted" in out
    assert len(out) <= MAX_TOOL_OUTPUT
    assert _format_result("ls", 0, "a\nb\n") == "$ ls\nexit=0\na\nb\n"


def test_bash_long_command_echo_is_shortened():
    from jarvis.tools.shell import _format_result

    out = _format_result("cat <<EOF\n" + "y" * 20_000 + "\nEOF", 0, "ok")
    assert "command truncated" in out and out.endswith("ok") and len(out) <= MAX_TOOL_OUTPUT


# ── edits ────────────────────────────────────────────────────────────────


def test_edit_accepts_old_str_pasted_with_line_numbers(proj):
    (proj / "f.py").write_text("def f():\n    return 1\n")
    out = files.edit_file("f.py", "1\tdef f():\n2\t    return 1", "1\tdef f():\n2\t    return 2")
    assert out.startswith("EDITED") and "line-number prefixes" in out
    assert (proj / "f.py").read_text() == "def f():\n    return 2\n"


def test_line_number_stripping_needs_consecutive_numbers():
    assert files._looks_line_numbered("3\ta\n4\tb")
    assert not files._looks_line_numbered("3\ta\n9\tb")
    assert not files._looks_line_numbered("id\tname\n1\tbob")


def test_not_found_shows_the_closest_region(proj):
    (proj / "g.py").write_text("import os\n\ndef greet(name):\n    print('hi', name)\n    return name\n")
    out = files.edit_file("g.py", "def greet(name):\n    print('hello', name)", "x")
    assert out.startswith("ERROR: old_str not found")
    assert "Closest match (lines 3–4" in out and "3\tdef greet(name):" in out


def test_edit_that_breaks_python_is_flagged(proj):
    (proj / "h.py").write_text("x = 1\n")
    out = files.edit_file("h.py", "x = 1", "x = (1")
    assert out.startswith("EDITED") and "no longer parses" in out and "SyntaxError" in out
    # Already-broken files don't nag.
    out2 = files.edit_file("h.py", "x = (1", "x = (2")
    assert "no longer parses" not in out2


def test_write_bad_json_is_flagged(proj):
    out = files.write_file("c.json", '{"a": 1,}')
    assert out.startswith("WROTE") and "invalid JSON" in out
    assert "no longer parses" not in files.write_file("ok.json", '{"a": 1}')


def test_overwrite_after_outside_change_is_refused(proj):
    p = proj / "k.txt"
    p.write_text("v1\n")
    files.read_file_tool("k.txt")
    time.sleep(0.01)
    p.write_text("user edit\n")
    os.utime(p, None)
    out = files.write_file("k.txt", "model version\n")
    assert out.startswith("ERROR") and "changed on disk" in out
    assert p.read_text() == "user edit\n"
    files.read_file_tool("k.txt")
    assert files.write_file("k.txt", "model version\n").startswith("WROTE")


def test_write_of_unread_or_own_file_is_allowed(proj):
    (proj / "n.txt").write_text("old\n")
    assert files.write_file("n.txt", "new\n").startswith("WROTE")  # never read: as before
    assert files.write_file("n.txt", "newer\n").startswith("WROTE")  # own write is "seen"


def test_edit_after_outside_change_still_applies_with_a_note(proj):
    p = proj / "q.txt"
    p.write_text("alpha\nbeta\n")
    files.read_file_tool("q.txt")
    p.write_text("alpha\nbeta\ngamma\n")
    os.utime(p, (time.time() + 5, time.time() + 5))
    out = files.edit_file("q.txt", "beta", "BETA")
    assert out.startswith("EDITED") and "changed on disk" in out
    assert p.read_text() == "alpha\nBETA\ngamma\n"


# ── tool names ───────────────────────────────────────────────────────────


def test_foreign_tool_names_map_to_real_tools():
    from jarvis.repl.render import _resolve_tool_alias, _unknown_tool_hint

    assert _resolve_tool_alias("Read") == "read_file"
    assert _resolve_tool_alias("str_replace") == "edit_file"
    assert _resolve_tool_alias("Bash") == "run_bash"
    assert _resolve_tool_alias("Grep") == "search_code"
    assert _resolve_tool_alias("READ_FILE") == "read_file"
    assert _resolve_tool_alias("read_file") is None  # real names untouched
    assert _resolve_tool_alias("mcp__x__read") is None
    assert _resolve_tool_alias("teleport") is None
    assert "read_file" in _unknown_tool_hint("read_files")


def test_aliased_call_runs_and_says_so(proj, monkeypatch):
    from jarvis.repl import render

    (proj / "z.txt").write_text("hi\n")
    for name in ("emit_tool_start", "emit_tool_done", "report_turn_phase"):
        monkeypatch.setattr(render, name, lambda *a, **k: None)
    block = SimpleNamespace(id="t1", name="Read", input={"file_path": "z.txt"})
    _, _, _, out = render._run_tool(block)
    assert block.name == "read_file"
    assert out.startswith("1\thi") and "ran 'read_file' instead" in out


# ── sticky tool groups ───────────────────────────────────────────────────


def _names(messages):
    from jarvis.tools import router

    return [t["name"] for t in router.select_tools(messages)]


def test_tool_group_stays_once_added(monkeypatch):
    from jarvis.tools import router

    monkeypatch.setattr(router, "_coding_agent_active", lambda: False)
    msgs = [{"role": "user", "content": "search the web for the latest python release"}]
    first = _names(msgs)
    assert "web_search" in first
    # The trigger scrolls out of the last-4 window during a tool loop.
    for i in range(6):
        msgs.append({"role": "assistant", "content": [{"type": "text", "text": f"step {i}"}]})
        msgs.append({"role": "user", "content": [{"type": "tool_result", "tool_use_id": f"t{i}", "content": "ok"}]})
    assert _names(msgs) == first  # same tools, same order


def test_sticky_groups_reset_for_another_conversation(monkeypatch):
    from jarvis.tools import router

    monkeypatch.setattr(router, "_coding_agent_active", lambda: False)
    assert "web_search" in _names([{"role": "user", "content": "search the web please"}])
    assert "web_search" not in _names([{"role": "user", "content": "what is 2 + 2"}])


# ── per-turn system prompt ───────────────────────────────────────────────


@pytest.fixture
def sys_env(monkeypatch):
    from jarvis.repl import system

    monkeypatch.setattr(system, "_build_static_body", lambda: "BODY")
    monkeypatch.setattr(system, "_selected_model_block", lambda: "")
    monkeypatch.setattr(system, "_agent_addon_block", lambda: "")
    monkeypatch.setattr(state, "auth_mode", "api_key")
    monkeypatch.setattr(state, "plan_mode", False)
    jobs = {"text": ""}
    monkeypatch.setattr(system, "_background_jobs_block", lambda: jobs["text"])
    monkeypatch.setattr(system, "_loop_block", lambda: "")
    msgs = [{"role": "user", "content": "do it"}]
    monkeypatch.setattr(state, "messages", msgs)
    return SimpleNamespace(system=system, jobs=jobs, msgs=msgs)


def test_system_prompt_has_no_clock_time(sys_env):
    out = sys_env.system.build_system()
    assert "CURRENT DATE:" in out and " at " not in out.split("CURRENT DATE:")[1].split("\n")[0]


def test_system_prompt_is_stable_within_a_turn(sys_env):
    first = sys_env.system.build_system()
    sys_env.jobs["text"] = "\n\nBACKGROUND JOBS: #1 running for 3s"
    sys_env.msgs += [
        {"role": "assistant", "content": [{"type": "tool_use", "id": "a", "name": "run_bg", "input": {}}]},
        {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "a", "content": "ok"}]},
    ]
    assert sys_env.system.build_system() == first
    # Next user turn picks the change up.
    sys_env.msgs.append({"role": "assistant", "content": [{"type": "text", "text": "done"}]})
    sys_env.msgs.append({"role": "user", "content": "and now?"})
    assert "BACKGROUND JOBS" in sys_env.system.build_system()


def test_plan_mode_change_applies_mid_turn(sys_env, monkeypatch):
    sys_env.system.build_system()
    monkeypatch.setattr(state, "plan_mode", True)
    assert "PLAN MODE" in sys_env.system.build_system()


# ── stepped trimming ─────────────────────────────────────────────────────


def test_trim_point_moves_in_steps_and_keeps_enough():
    from jarvis.repl import trim

    def convo(n_rounds):
        msgs = [{"role": "user", "content": "start"}]
        for i in range(n_rounds):
            msgs.append({"role": "assistant", "content": [{"type": "tool_use", "id": f"t{i}", "name": "x", "input": {}}]})
            msgs.append({"role": "user", "content": [{"type": "tool_result", "tool_use_id": f"t{i}", "content": "y" * 20_000}]})
        return msgs

    def stubbed(msgs):
        out = trim.trim_messages(msgs)
        return sum(
            1 for m in out if isinstance(m.get("content"), list)
            and any(b.get("content") == "[output trimmed to save context]" for b in m["content"]))

    counts = [stubbed(convo(n)) for n in range(20, 40)]
    assert len(set(counts)) <= 4  # moves a few times, not on every request
    for n, c in zip(range(20, 40), counts):
        assert n - c >= trim.KEEP_TURNS - 1  # never fewer than KEEP_TURNS kept


# ── cache breakpoints ────────────────────────────────────────────────────


def _count_markers(kwargs):
    n = sum(1 for t in kwargs.get("tools") or [] if "cache_control" in t)
    sys_ = kwargs.get("system")
    if isinstance(sys_, list):
        n += sum(1 for b in sys_ if "cache_control" in b)
    for m in kwargs["messages"]:
        if isinstance(m.get("content"), list):
            n += sum(1 for b in m["content"] if isinstance(b, dict) and "cache_control" in b)
    return n


def test_breakpoints_land_on_tools_system_and_last_message():
    tools = [{"name": "a"}, {"name": "b"}]
    msgs = [
        {"role": "user", "content": "hi"},
        {"role": "assistant", "content": [{"type": "thinking", "thinking": "", "signature": "s"},
                                          {"type": "tool_use", "id": "1", "name": "a", "input": {}}]},
        {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "1", "content": "r"},
                                     {"type": "text", "text": ""}]},
    ]
    kwargs = {"tools": tools, "system": "SYS", "messages": msgs}
    prompt_cache.apply(kwargs)
    assert kwargs["tools"][-1]["cache_control"] == {"type": "ephemeral"}
    assert kwargs["system"] == [{"type": "text", "text": "SYS", "cache_control": {"type": "ephemeral"}}]
    last = kwargs["messages"][-1]["content"]
    assert "cache_control" in last[0] and "cache_control" not in last[1]  # empty text skipped
    assert kwargs["messages"][0]["content"] == [{"type": "text", "text": "hi"}]
    assert _count_markers(kwargs) == 3
    # Inputs untouched (history never collects markers).
    assert "cache_control" not in tools[-1] and msgs[0]["content"] == "hi"
    assert "cache_control" not in msgs[-1]["content"][0]
    prompt_cache.apply(kwargs)  # idempotent
    assert _count_markers(kwargs) == 3
    prompt_cache.strip(kwargs)
    assert _count_markers(kwargs) == 0 and not prompt_cache.has_markers(kwargs)


def test_oauth_system_blocks_mark_only_the_last():
    kwargs = {"system": [{"type": "text", "text": "id"}, {"type": "text", "text": "body"}], "messages": []}
    prompt_cache.apply(kwargs)
    assert "cache_control" not in kwargs["system"][0] and "cache_control" in kwargs["system"][1]


def test_caching_only_for_endpoints_that_understand_it():
    claude = anthropic.Anthropic(api_key="x")
    assert prompt_cache.enabled_for("anthropic", "claude-sonnet-5-5", claude)
    assert prompt_cache.enabled_for("openrouter", "anthropic/claude-sonnet-5-5", claude)
    assert not prompt_cache.enabled_for("openrouter", "google/gemini-3-pro", claude)
    assert not prompt_cache.enabled_for("md:minimax", "MiniMax-M2", claude)
    assert not prompt_cache.enabled_for("anthropic", "claude-x", SimpleNamespace())
    prompt_cache.disable("test")
    assert not prompt_cache.enabled_for("anthropic", "claude-sonnet-5-5", claude)


def test_drop_thinking_blocks():
    msgs = [
        {"role": "assistant", "content": [{"type": "thinking", "thinking": "x", "signature": "s"},
                                          {"type": "text", "text": "a"}]},
        {"role": "assistant", "content": [SimpleNamespace(type="redacted_thinking")]},
        {"role": "user", "content": "q"},
    ]
    assert prompt_cache.drop_thinking_blocks(msgs) == 2
    assert msgs[0]["content"] == [{"type": "text", "text": "a"}]
    assert msgs[1]["content"] == [{"type": "text", "text": "…"}]


# ── request loop ─────────────────────────────────────────────────────────


def _bad_request(text):
    def raise_it():
        req = httpx.Request("POST", "https://api.anthropic.com/v1/messages")
        raise anthropic.BadRequestError(text, response=httpx.Response(400, request=req), body=None)
    return raise_it


def _final(**usage):
    return SimpleNamespace(
        usage=SimpleNamespace(**{"input_tokens": 10, "output_tokens": 5, **usage}),
        content=[SimpleNamespace(type="text", text="ok")],
        stop_reason="end_turn",
    )


class _Ctx:
    def __init__(self, behavior):
        self._behavior = behavior

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def get_final_message(self):
        return self._behavior()


@pytest.fixture
def loop_env(monkeypatch):
    import jarvis.repl.stream as stream

    for name, value in {
        "build_system": lambda: "sys",
        "select_tools": lambda msgs: [{"name": "t", "input_schema": {"type": "object"}}],
        "_heal_message_history": lambda: None,
        "report_turn_phase": lambda *a, **k: None,
    }.items():
        monkeypatch.setattr(stream, name, value)
    monkeypatch.setattr(stream.time, "sleep", lambda *a, **k: None)
    monkeypatch.setattr(stream, "console", SimpleNamespace(print=lambda *a, **k: None))
    monkeypatch.setattr(stream.prompt_cache, "enabled_for", lambda *a: prompt_cache._disabled_reason is None)
    monkeypatch.setattr(state, "stream_reply_live", False)
    monkeypatch.setattr(state, "think_mode", False)
    monkeypatch.setattr(state, "provider", "anthropic")
    monkeypatch.setattr(state, "auth_mode", "api_key")
    monkeypatch.setattr(state, "MODEL", "claude-test")
    state.cancel_requested.clear()
    sent: list[dict] = []

    def install(script, messages):
        monkeypatch.setattr(state, "messages", messages)

        def stream_fn(**kwargs):
            import copy

            sent.append(copy.deepcopy(kwargs))
            return _Ctx(script[min(len(sent), len(script)) - 1])

        monkeypatch.setattr(state, "client", SimpleNamespace(messages=SimpleNamespace(stream=stream_fn)))

    return SimpleNamespace(stream=stream, install=install, sent=sent)


def test_request_carries_breakpoints_and_counts_cached_tokens(loop_env):
    loop_env.install([lambda: _final(cache_read_input_tokens=900, cache_creation_input_tokens=50)],
                     [{"role": "user", "content": "hi"}])
    loop_env.stream.call_claude_stream()
    kw = loop_env.sent[0]
    assert "cache_control" in kw["tools"][-1] and "cache_control" in kw["system"][-1]
    assert "cache_control" in kw["messages"][-1]["content"][-1]
    assert state.total_in == 960 and state.cache_read_tokens == 900 and state.cache_write_tokens == 50
    assert state.messages == [{"role": "user", "content": "hi"}]  # history unmarked


def test_refused_markers_retry_plain_and_stay_off(loop_env):
    loop_env.install([_bad_request("tools.0.cache_control: Extra inputs are not permitted"), lambda: _final()],
                     [{"role": "user", "content": "hi"}])
    loop_env.stream.call_claude_stream()
    assert len(loop_env.sent) == 2
    assert prompt_cache.has_markers(loop_env.sent[0]) and not prompt_cache.has_markers(loop_env.sent[1])
    assert prompt_cache._disabled_reason


def test_thinking_binding_error_drops_old_thinking_and_retries(loop_env):
    history = [
        {"role": "user", "content": "hi"},
        {"role": "assistant", "content": [{"type": "thinking", "thinking": "t", "signature": "sig"},
                                          {"type": "text", "text": "hello"}]},
        {"role": "user", "content": "more"},
    ]
    loop_env.install([
        _bad_request("messages.1.content.0: Invalid `signature` in `thinking` block. "
                     "The block is bound to a different conversation."),
        lambda: _final(),
    ], history)
    loop_env.stream.call_claude_stream()
    assert len(loop_env.sent) == 2
    assert all(b.get("type") != "thinking" for b in loop_env.sent[1]["messages"][1]["content"])
    assert prompt_cache.has_markers(loop_env.sent[1])
    assert state.messages[1]["content"] == [{"type": "text", "text": "hello"}]


def test_unrelated_400_is_not_swallowed(loop_env):
    loop_env.install([_bad_request("max_tokens: too large")], [{"role": "user", "content": "hi"}])
    with pytest.raises(anthropic.BadRequestError):
        loop_env.stream.call_claude_stream()
    assert len(loop_env.sent) == 1


# ── Codex prompt cache key ───────────────────────────────────────────────


def _codex_messages(create):
    from jarvis.auth import codex_client

    return codex_client._CodexMessages(SimpleNamespace(responses=SimpleNamespace(create=create)))


def test_codex_requests_carry_a_session_cache_key(monkeypatch):
    from jarvis.auth import codex_client

    monkeypatch.setattr(codex_client, "_cache_key_refused", False)
    monkeypatch.setattr(state, "current_session_id", 42)
    sent = []
    msgs = _codex_messages(lambda **p: sent.append(p) or iter(()))
    with msgs.stream(model="gpt-x", messages=[{"role": "user", "content": "hi"}], system="sys"):
        pass
    with msgs.stream(model="gpt-x", messages=[{"role": "user", "content": "hi"}], system="sys"):
        pass
    key = sent[0]["prompt_cache_key"]
    assert key.endswith("-42") and sent[1]["prompt_cache_key"] == key


def test_codex_cache_key_refusal_retries_without_it(monkeypatch):
    import openai

    from jarvis.auth import codex_client

    monkeypatch.setattr(codex_client, "_cache_key_refused", False)
    sent = []

    def create(**p):
        sent.append(dict(p))
        if "prompt_cache_key" in p:
            req = httpx.Request("POST", "https://chatgpt.com/backend-api/codex/responses")
            raise openai.BadRequestError("Unsupported parameter: prompt_cache_key",
                                         response=httpx.Response(400, request=req), body=None)
        return iter(())

    with _codex_messages(create).stream(model="gpt-x", messages=[{"role": "user", "content": "hi"}]):
        pass
    assert len(sent) == 2 and "prompt_cache_key" not in sent[1]
    assert codex_client._cache_key_refused


def test_codex_other_400s_are_not_swallowed(monkeypatch):
    import openai

    from jarvis.auth import codex_client

    monkeypatch.setattr(codex_client, "_cache_key_refused", False)

    def create(**p):
        req = httpx.Request("POST", "https://chatgpt.com/backend-api/codex/responses")
        raise openai.BadRequestError("model not supported", response=httpx.Response(400, request=req), body=None)

    with pytest.raises(openai.BadRequestError):
        with _codex_messages(create).stream(model="gpt-x", messages=[{"role": "user", "content": "hi"}]):
            pass


# ── what the terminal / web rows and stats show ──────────────────────────


def test_row_summaries_for_reads_and_edit_warnings():
    from jarvis.tui.tool_format import tool_summary

    out = "1\ta\n2\tb\n[showing lines 1–2 of 900 — call read_file with offset=2 to continue]"
    assert tool_summary("read_file", {"path": "x"}, out)[0] == ["Read lines 1–2 of 900 · more with offset"]
    assert tool_summary("read_file", {"path": "x"}, "1\ta\n2\tb")[0] == ["Read 2 lines"]
    assert tool_summary("read_file", {"path": "x"}, "[empty file]")[0] == ["Empty file"]

    edited = ("EDITED /p/h.py (1 replacement)\n[warning: h.py no longer parses — SyntaxError at line 1: "
              "'(' was never closed → x = (1. Fix this before moving on.]")
    lines, err = tool_summary("edit_file", {"path": "h.py"}, edited)
    assert lines[0] == "Applied 1 edit" and not err
    assert lines[1].startswith("⚠ h.py no longer parses — SyntaxError at line 1")
    stale = "EDITED /p/q.txt (1 replacement)\n[note: the file had changed on disk since you last read it — re-read]"
    assert "changed on disk" in tool_summary("edit_file", {"path": "q.txt"}, stale)[0][1]
    lines, err = tool_summary("write_file", {"path": "k.txt", "content": "x"},
                              "ERROR: k.txt changed on disk since you last read or wrote it")
    assert err and "changed on disk" in lines[0]


def test_stats_and_web_state_carry_cache_numbers(monkeypatch):
    from jarvis.commands.control import session_stats
    from jarvis.web.state_api import state_fields

    monkeypatch.setattr(state, "total_in", 1000)
    monkeypatch.setattr(state, "cache_read_tokens", 900)
    monkeypatch.setattr(state, "cache_write_tokens", 40)
    s = session_stats()
    assert s["cache_read"] == 900 and s["cache_write"] == 40
    assert state_fields()["tokens_cache_read"] == 900


def test_web_row_keeps_the_last_line_of_long_command_output():
    from jarvis.tools.shell import _format_result
    from jarvis.web.console_mux import tool_row_fields

    body = "FIRST\n" + "".join(f"compiling {i}\n" for i in range(4000)) + "LAST: 3 errors\n"
    summary = tool_row_fields("run_bash", {"cmd": "make"}, _format_result("make", 0, body))["summary"]
    assert summary.splitlines()[0].startswith("… +") and summary.splitlines()[-1] == "LAST: 3 errors"
    assert len(summary.splitlines()) == 4
