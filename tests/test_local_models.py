"""Local models (``jarvis/auth/local_models.py``): discovery, the native Ollama
client, the OpenAI-compatible path, provider switching, error cards, the web
API and the terminal dialog — against ``tests/local_model_server.py``."""
from __future__ import annotations

import asyncio
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from types import SimpleNamespace

import pytest

from jarvis import state
from jarvis.auth import local_models as lm
from local_model_server import FakeLocalServer

TOOLS = [{"name": "read_file", "description": "read a file",
          "input_schema": {"type": "object", "properties": {"path": {"type": "string"}}}}]


@pytest.fixture
def ollama(monkeypatch):
    """A detected Ollama: the built-in runtime pointed at a fake server."""
    srv = FakeLocalServer("ollama").start()
    rt = lm.Runtime("ollama", "Ollama", srv.url, lm.KIND_OLLAMA, "blurb", "ollama serve",
                    "https://ollama.com/download", "Ol", pull="ollama pull qwen3",
                    how="or just open the Ollama app")
    monkeypatch.setattr(lm, "RUNTIMES", (rt,))
    monkeypatch.setattr(lm, "RUNTIME_BY_ID", {"ollama": rt})
    lm._bump()
    lm.scan()
    yield srv
    srv.stop()


@pytest.fixture
def lmstudio():
    srv = FakeLocalServer("lmstudio").start()
    res = lm.add_server("Studio", srv.url)
    assert res["ok"], res
    yield SimpleNamespace(srv=srv, provider=res["id"])
    srv.stop()


@pytest.fixture
def session(monkeypatch):
    monkeypatch.setattr(state, "provider", "opencode_zen")
    monkeypatch.setattr(state, "MODEL", "big-pickle")
    monkeypatch.setattr(state, "harness_agent_free", True)
    monkeypatch.setattr(state, "messages", [{"role": "user", "content": "hi"}])
    monkeypatch.setattr(state, "last_api_error", None)
    monkeypatch.setattr(state, "current_session_id", None)
    import jarvis.storage.prefs as prefs

    monkeypatch.setattr(prefs, "save_last_model", lambda *a, **k: None)
    import jarvis.commands.control as control

    monkeypatch.setattr(control, "save_last_model", lambda *a, **k: None, raising=False)
    monkeypatch.setattr(control, "header_panel", lambda *a, **k: None, raising=False)


# ─── discovery ───────────────────────────────────────────────────────────


def test_detects_ollama_models_with_their_capabilities(ollama):
    ms = {m.id: m for m in lm.models("local:ollama")}
    assert set(ms) == {"qwen3:8b", "llava:7b", "gpt-oss:20b"}  # the embedding model is left out
    assert ms["qwen3:8b"].tools and ms["qwen3:8b"].think and ms["qwen3:8b"].loaded
    assert ms["llava:7b"].vision and ms["llava:7b"].tools is False
    assert ms["gpt-oss:20b"].levels == ("low", "medium", "high")
    # Usable first, loaded first among those; the chat-only model last.
    assert [m.id for m in lm.models("local:ollama")][0] == "qwen3:8b"
    assert lm.models("local:ollama")[-1].id == "llava:7b"
    assert lm.online_providers() == ["local:ollama"]


def test_ollama_window_is_the_setting_capped_at_the_model(ollama):
    assert lm.served_context("local:ollama", "qwen3:8b") == 32_768
    assert lm.served_context("local:ollama", "llava:7b") == 4_096       # its own max
    lm.set_context(65_536)
    assert lm.served_context("local:ollama", "qwen3:8b") == 40_960      # capped at the model
    assert lm.served_context("local:ollama", "gpt-oss:20b") == 65_536
    label = dict(lm.picker_rows("local:ollama"))["gpt-oss:20b"]
    assert "64K ctx" in label                                            # what the request gets


def test_lm_studio_is_added_by_url_and_read_from_its_own_listing(lmstudio):
    ms = {m.id: m for m in lm.models(lmstudio.provider)}
    assert set(ms) == {"qwen2.5-coder-14b-instruct", "gemma-3-12b"}     # embeddings dropped
    assert ms["gemma-3-12b"].vision and ms["gemma-3-12b"].ctx == 131_072
    assert ms["qwen2.5-coder-14b-instruct"].tools and ms["qwen2.5-coder-14b-instruct"].loaded
    srv = lm.get_server(lmstudio.provider)
    assert srv.kind == lm.KIND_OPENAI and srv.url.endswith("/v1") and srv.custom


def test_adding_checks_the_address_first(tmp_path):
    bad = lm.add_server("", "http://127.0.0.1:9")
    assert not bad["ok"] and "Nothing answered" in bad["error"]
    assert lm.add_server("", "not a url ::")["ok"] is False
    forced = lm.add_server("Garage", "http://127.0.0.1:9", force=True)
    assert forced["ok"] and lm.status(forced["id"]) == "offline"
    assert lm.remove_server(forced["id"]) and lm.get_server(forced["id"]) is None


def test_saved_keys_stay_private(tmp_path):
    srv = FakeLocalServer("lmstudio", api_key="sekrit").start()
    try:
        no_key = lm.add_server("Box", srv.url)
        assert not no_key["ok"] and "API key" in no_key["error"]
        res = lm.add_server("Box", srv.url, key="sekrit")
        assert res["ok"] and res["models"] == 2
        assert oct(lm.CONFIG_FILE.stat().st_mode & 0o777) == "0o600"
        public = json.dumps(lm.public())
        assert "sekrit" not in public and '"has_key": true' in public
    finally:
        srv.stop()


def test_another_app_on_a_runtime_port_is_not_that_runtime(monkeypatch):
    """macOS AirPlay answers a bare 403 on :5000 (text-generation-webui's port)."""

    class AirPlay(BaseHTTPRequestHandler):
        def log_message(self, *_a):
            pass

        def do_GET(self):
            self.send_response(403)
            self.send_header("Content-Length", "0")
            self.end_headers()

    httpd = ThreadingHTTPServer(("127.0.0.1", 0), AirPlay)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    try:
        rt = lm.Runtime("textgen", "text-generation-webui", f"http://127.0.0.1:{httpd.server_address[1]}",
                        lm.KIND_OPENAI, "b", "", "", "TG")
        monkeypatch.setattr(lm, "RUNTIMES", (rt,))
        monkeypatch.setattr(lm, "RUNTIME_BY_ID", {"textgen": rt})
        assert lm.scan()["textgen"]["status"] == "offline"
    finally:
        httpd.shutdown()
        httpd.server_close()


def test_a_stopped_server_keeps_its_models_as_offline(ollama, monkeypatch):
    ollama.stop()
    lm.scan()
    assert lm.online_providers() == []
    ms = lm.models("local:ollama")
    assert ms and all(m.offline for m in ms)
    assert lm.picker_providers("local:ollama") == ["local:ollama"]  # the one in use stays listed
    assert lm.picker_providers("anthropic") == []


def test_detection_can_be_switched_off_per_runtime(ollama):
    assert lm.set_detect("ollama", False)
    assert [s.id for s in lm.servers()] == []
    assert lm.public()["servers"][0]["status"] == "off"
    lm.set_detect("ollama", True)
    assert [s.id for s in lm.servers()] == ["ollama"]


def test_turned_off_entirely(ollama, monkeypatch):
    monkeypatch.setenv("HARNESS_LOCAL_MODELS", "0")
    assert lm.servers() == [] and lm.scan() == {} and lm.cached() == {}


def test_ollama_host_env_moves_the_runtime(monkeypatch):
    monkeypatch.setenv("OLLAMA_HOST", "0.0.0.0:12345")
    assert lm._ollama_root_from_env() == "http://127.0.0.1:12345"
    monkeypatch.setenv("OLLAMA_HOST", "https://gpu.lan")
    assert lm._ollama_root_from_env() == "https://gpu.lan:11434"


# ─── the clients ─────────────────────────────────────────────────────────


def _stream(client, **kw):
    with client.messages.stream(**kw) as st:
        events = list(st.delta_stream)
        return events, st.get_final_message()


def test_ollama_native_request_and_tool_round_trip(ollama):
    client = lm.build_client("local:ollama")
    events, final = _stream(client, model="qwen3:8b", messages=[{"role": "user", "content": "please use a tool"}],
                            tools=TOOLS, system="sys", max_tokens=32_000,
                            thinking={"type": "enabled", "effort": "high"})
    sent = ollama.requests[-1]
    assert sent["options"] == {"num_ctx": 32_768, "num_predict": 8_192}
    assert sent["think"] is True and sent["keep_alive"] == "30m"
    assert sent["messages"][0] == {"role": "system", "content": "sys"}
    assert ("thinking", "pondering") in events
    assert any(kind == "tool_input" for kind, _ in events)
    tool = final.content[-1]
    assert final.stop_reason == "tool_use" and tool.name == "read_file" and tool.input == {"path": "README.md"}
    assert final.usage.input_tokens == 120 and final.usage.output_tokens == 7

    history = [
        {"role": "user", "content": "please use a tool"},
        {"role": "assistant", "content": [{"type": "tool_use", "id": tool.id, "name": tool.name, "input": tool.input}]},
        {"role": "user", "content": [{"type": "tool_result", "tool_use_id": tool.id, "content": "# Readme"}]},
    ]
    _events, final = _stream(client, model="qwen3:8b", messages=history, tools=TOOLS, system="sys")
    assert final.content[-1].text.strip() == "tool said: # Readme"
    call_msg, result_msg = ollama.requests[-1]["messages"][-2:]
    assert call_msg["tool_calls"][0]["function"] == {"name": "read_file", "arguments": {"path": "README.md"}}
    assert result_msg["role"] == "tool" and result_msg["tool_name"] == "read_file"


def test_ollama_levels_and_chat_only_models(ollama):
    client = lm.build_client("local:ollama")
    _stream(client, model="gpt-oss:20b", messages=[{"role": "user", "content": "hi"}],
            thinking={"type": "enabled", "effort": "medium"})
    assert ollama.requests[-1]["think"] == "medium"
    # A model without tools: the request is retried without them, once.
    _e, final = _stream(client, model="llava:7b", messages=[{"role": "user", "content": "hi"}], tools=TOOLS)
    assert "hello from llava:7b" in final.content[-1].text and "tools" not in ollama.requests[-1]
    assert "llava:7b" in client.no_tools
    # Images go as bare base64.
    img = {"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": "QUJD"}}
    _stream(client, model="llava:7b", messages=[{"role": "user", "content": [img, {"type": "text", "text": "see"}]}])
    assert ollama.requests[-1]["messages"][-1] == {"role": "user", "content": "see", "images": ["QUJD"]}


def test_openai_compatible_server_streams_tools(lmstudio):
    client = lm.build_client(lmstudio.provider)
    _e, final = _stream(client, model="qwen2.5-coder-14b-instruct", messages=[{"role": "user", "content": "use a tool"}],
                        tools=TOOLS, system="s", max_tokens=32_000,
                        thinking={"type": "enabled", "effort": "high"})
    assert final.stop_reason == "tool_use" and final.content[-1].input == {"path": "README.md"}
    sent = lmstudio.srv.requests[-1]
    assert sent["max_tokens"] == 8_192                  # a quarter of its 32K window
    assert "reasoning_effort" not in sent               # unknown thinking: nothing sent
    assert all("reasoning_content" not in m for m in sent["messages"])
    assert client.read_timeout == lm.local_timeout()


# ─── the request path + error cards ──────────────────────────────────────


@pytest.fixture
def stream_env(monkeypatch, session):
    import jarvis.repl.stream as stream

    monkeypatch.setattr(stream, "build_system", lambda: "sys")
    monkeypatch.setattr(stream, "select_tools", lambda msgs: TOOLS)
    monkeypatch.setattr(stream, "trim_messages", lambda msgs: msgs)
    monkeypatch.setattr(stream, "_heal_message_history", lambda: None)
    monkeypatch.setattr(stream, "report_turn_phase", lambda *a, **k: None)
    shown, printed = [], []
    monkeypatch.setattr(stream, "console", SimpleNamespace(
        print=lambda m="", *a, **k: printed.append(str(m)), show_error=shown.append))
    monkeypatch.setattr(stream.time, "sleep", lambda s: None)
    monkeypatch.setattr(state, "stream_reply_live", False)
    monkeypatch.setattr(state, "think_mode", True)
    monkeypatch.setattr(state, "think_effort", "high")
    state.cancel_requested.clear()
    stream._got_first_delta = False
    return SimpleNamespace(stream=stream, shown=shown, printed=printed)


def test_a_turn_runs_end_to_end_on_ollama(ollama, stream_env, monkeypatch):
    monkeypatch.setattr(state, "provider", "local:ollama")
    monkeypatch.setattr(state, "MODEL", "qwen3:8b")
    monkeypatch.setattr(state, "messages", [{"role": "user", "content": "please use a tool"}])
    monkeypatch.setattr(state, "client", lm.build_client("local:ollama"))
    final = stream_env.stream.call_claude_stream()
    assert final.stop_reason == "tool_use"
    sent = ollama.requests[-1]
    assert sent["think"] is True and sent["options"]["num_ctx"] == 32_768
    assert [t["function"]["name"] for t in sent["tools"]] == ["read_file"]


def test_stopped_server_fails_fast_with_how_to_start_it(ollama, stream_env, monkeypatch):
    from jarvis.console import HarnessAPIError

    monkeypatch.setattr(state, "provider", "local:ollama")
    monkeypatch.setattr(state, "MODEL", "qwen3:8b")
    monkeypatch.setattr(state, "client", lm.build_client("local:ollama"))
    ollama.stop()
    with pytest.raises(HarnessAPIError):
        stream_env.stream.call_claude_stream()
    (rep,) = stream_env.shown
    assert rep.title == "Ollama isn't running"
    assert "ollama serve" in rep.message
    assert not any("reconnecting" in p for p in stream_env.printed)  # nothing to wait for
    assert [a.command for a in rep.actions][:2] == ["/retry", "/local-models"]


def test_missing_model_says_how_to_pull_it(ollama, stream_env, monkeypatch):
    from jarvis.console import HarnessAPIError

    monkeypatch.setattr(state, "provider", "local:ollama")
    monkeypatch.setattr(state, "MODEL", "mistral:7b")
    monkeypatch.setattr(state, "client", lm.build_client("local:ollama"))
    with pytest.raises(HarnessAPIError):
        stream_env.stream.call_claude_stream()
    (rep,) = stream_env.shown
    assert rep.kind == "model" and "ollama pull mistral:7b" in rep.message


def test_out_of_memory_suggests_a_smaller_model(lmstudio, stream_env, monkeypatch):
    from jarvis.console import HarnessAPIError

    lmstudio.srv.fail_chat = 500
    monkeypatch.setattr(state, "provider", lmstudio.provider)
    monkeypatch.setattr(state, "MODEL", "gemma-3-12b")
    monkeypatch.setattr(state, "client", lm.build_client(lmstudio.provider))
    with pytest.raises(HarnessAPIError):
        stream_env.stream.call_claude_stream()
    (rep,) = stream_env.shown
    assert rep.kind == "server" and "smaller model" in rep.message
    assert len(lmstudio.srv.requests) == 1  # no retry loop on a local 5xx


# ─── providers, thinking, context ────────────────────────────────────────


def test_local_models_join_the_provider_registry(ollama, session):
    from jarvis.constants import providers as p

    assert "local:ollama" in p.connected_model_sources()
    rows = [r for r in p.all_model_picker_rows(cached=True) if r[0] == "local:ollama"]
    assert [r[1] for r in rows][0] == "qwen3:8b"
    assert p.provider_label("local:ollama") == "Ollama"
    assert p.model_pricing("qwen3:8b", "local:ollama") == (0.0, 0.0)
    assert p.model_is_free("qwen3:8b", "local:ollama")
    assert p.model_supports_images("llava:7b", "local:ollama")
    assert not p.model_supports_images("qwen3:8b", "local:ollama")
    assert p.model_belongs_to_provider("gpt-oss:20b", "local:ollama")
    assert not p.model_belongs_to_provider("claude-opus-5-5", "local:ollama")
    assert p.normalize_model_for_provider("claude-opus-5-5", "local:ollama") == "qwen3:8b"


def test_provider_command_switches_to_ollama(ollama, session):
    from jarvis.auth.ollama_client import OllamaClient
    from jarvis.commands.control import _apply_model_selection, _handle_provider

    _handle_provider("ollama")
    assert state.provider == "local:ollama" and state.MODEL == "qwen3:8b"
    assert isinstance(state.client, OllamaClient) and not state.harness_agent_free
    _apply_model_selection("gpt-oss:20b", source="local:ollama")
    assert state.MODEL == "gpt-oss:20b"


def test_switching_between_two_local_servers(ollama, lmstudio, session):
    from jarvis.auth.opencode_client import OpenCodeClient
    from jarvis.commands.control import _apply_model_selection

    _apply_model_selection("gemma-3-12b", source=lmstudio.provider)
    assert state.provider == lmstudio.provider and isinstance(state.client, OpenCodeClient)
    _apply_model_selection("llava:7b", source="local:ollama")
    assert state.provider == "local:ollama" and state.MODEL == "llava:7b"


def test_startup_restores_a_saved_local_model(ollama, monkeypatch):
    from jarvis.auth import client as auth_client
    from jarvis.auth.ollama_client import OllamaClient
    import jarvis.storage.prefs as prefs

    monkeypatch.setattr(prefs, "should_use_first_run_harness_defaults", lambda: False)
    monkeypatch.setattr(prefs, "load_saved_model", lambda: "gpt-oss:20b")
    monkeypatch.setattr(prefs, "load_saved_preferences", lambda: ("gpt-oss:20b", "local:ollama"))
    monkeypatch.setattr(auth_client, "_secure_write", lambda *a, **k: None)
    for name in ("provider", "MODEL", "client", "auth_mode"):
        monkeypatch.setattr(state, name, getattr(state, name))  # restored afterwards
    monkeypatch.setattr(state, "harness_agent_free", False)
    c = auth_client.make_client(interactive=False)
    assert isinstance(c, OllamaClient)
    assert state.provider == "local:ollama" and state.MODEL == "gpt-oss:20b"


def test_thinking_levels_come_from_ollama(ollama):
    from jarvis.auth.thinking_caps import thinking_caps

    oss = thinking_caps("gpt-oss:20b", "local:ollama")
    assert oss.known and oss.levels == ("low", "medium", "high") and not oss.can_off
    qwen = thinking_caps("qwen3:8b", "local:ollama")
    assert qwen.known and qwen.mode == "toggle"
    assert thinking_caps("llava:7b", "local:ollama").mode == "none"


def test_context_budget_uses_the_served_window(ollama, monkeypatch):
    from jarvis.repl import context_budget

    monkeypatch.setattr(state, "provider", "local:ollama")
    assert context_budget.context_window("qwen3:8b") == 32_768
    assert context_budget.context_window("llava:7b") == 4_096


# ─── text command, web API ───────────────────────────────────────────────


def test_local_models_command(ollama, session, monkeypatch):
    import jarvis.commands.local_models as cmd

    out = []
    monkeypatch.setattr(cmd, "console", SimpleNamespace(print=lambda *a, **k: out.append(str(a[0]) if a else "")))
    cmd.handle_local_models("context 16k")
    assert lm.context_setting() == 16_384
    cmd.handle_local_models("use ollama gpt-oss:20b")
    assert state.provider == "local:ollama" and state.MODEL == "gpt-oss:20b"
    cmd.handle_local_models("nonsense")
    assert any("unknown" in o for o in out)


def test_web_api(ollama, session):
    from jarvis.web.local_api import get_local, post_local

    data = get_local()
    srv = next(s for s in data["servers"] if s["id"] == "local:ollama")
    assert srv["status"] == "online" and srv["start"] == "ollama serve"
    assert [m["id"] for m in srv["models"]][0] == "qwen3:8b"
    res = post_local({"op": "use", "id": "local:ollama", "model": "gpt-oss:20b"})
    assert res["ok"] and state.MODEL == "gpt-oss:20b"
    active = next(m for s in res["local"]["servers"] for m in s["models"] if m["active"])
    assert active["id"] == "gpt-oss:20b"
    assert post_local({"op": "context", "tokens": 65_536})["local"]["context"] == 65_536
    assert post_local({"op": "detect", "server": "ollama", "on": False})["ok"]
    assert post_local({"op": "bogus"})["ok"] is False


def test_web_removing_the_server_in_use_falls_back(lmstudio, session, monkeypatch):
    from jarvis.web.local_api import post_local
    import jarvis.web.providers_api as pv

    fell = []
    monkeypatch.setattr(pv, "_fall_back_to_free", lambda: fell.append(1))
    post_local({"op": "use", "id": lmstudio.provider, "model": "gemma-3-12b"})
    assert state.provider == lmstudio.provider
    res = post_local({"op": "remove", "id": lmstudio.provider})
    assert res["ok"] and fell == [1]


def test_web_model_list_marks_local_rows(ollama, session):
    from jarvis.web.pickers_api import list_models

    rows = [m for m in list_models()["models"] if m["source"] == "local:ollama"]
    assert rows and all(m["local"] and m["free"] for m in rows)
    assert next(m for m in rows if m["model_id"] == "llava:7b")["images"]


# ─── terminal dialogs ────────────────────────────────────────────────────


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


def test_tui_dialog_lists_models_and_enter_uses_one(ollama, hermetic_app, monkeypatch):
    from textual.widgets import OptionList

    from jarvis.tui.local_modal import USE_PREFIX, LocalModelsScreen

    monkeypatch.setattr(LocalModelsScreen, "_scan", lambda self: None)
    monkeypatch.setattr(state, "provider", "anthropic")
    monkeypatch.setattr(state, "MODEL", "claude-opus-5-5")
    picked = []

    async def run():
        app = hermetic_app()
        async with app.run_test(size=(130, 44)) as pilot:
            await pilot.pause(0.2)
            screen = LocalModelsScreen()
            app.push_screen(screen, picked.append)
            await pilot.pause(0.3)
            opts = screen.query_one("#lm_list", OptionList)
            ids = [str(opts.get_option_at_index(i).id) for i in range(opts.option_count)
                   if not opts.get_option_at_index(i).disabled]
            assert ids[:3] == [f"{USE_PREFIX}local:ollama::qwen3:8b", f"{USE_PREFIX}local:ollama::gpt-oss:20b",
                               f"{USE_PREFIX}local:ollama::llava:7b"]
            assert ids[-1] == "__add__"
            # c cycles the context window.
            await pilot.press("c")
            assert lm.context_setting() == 65_536
            await pilot.press("enter")
            await pilot.pause(0.2)

    asyncio.run(run())
    assert picked == [f"{USE_PREFIX}local:ollama::qwen3:8b"]
    # …and the app turns that into a switch to local:ollama.
    from jarvis.auth.local_models import provider_id

    server, _sep, model = picked[0][len(USE_PREFIX):].partition("::")
    assert (provider_id(server), model) == ("local:ollama", "qwen3:8b")


def test_tui_model_picker_offers_local_models(ollama, hermetic_app, monkeypatch):
    from textual.widgets import OptionList

    import jarvis.tui.model_modal as mm

    monkeypatch.setattr(mm.ModelPickerScreen, "_refresh_catalogs", lambda self: None)

    async def run():
        app = hermetic_app()
        async with app.run_test(size=(130, 44)) as pilot:
            await pilot.pause(0.2)
            screen = mm.ModelPickerScreen()
            app.push_screen(screen)
            await pilot.pause(0.3)
            opts = screen.query_one("#model_list", OptionList)
            ids = [str(opts.get_option_at_index(i).id) for i in range(opts.option_count)]
            assert "local:ollama::qwen3:8b" in ids and mm.LOCAL_ID in ids

    asyncio.run(run())


def test_provider_hub_has_local_models(hermetic_app):
    from textual.widgets import OptionList

    from jarvis.tui.provider_hub import ProviderHubScreen

    async def run():
        app = hermetic_app()
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause(0.2)
            screen = ProviderHubScreen()
            got = []
            app.push_screen(screen, got.append)
            await pilot.pause(0.2)
            opts = screen.query_one("#mode_list", OptionList)
            opts.highlighted = opts.get_option_index("local")
            await pilot.press("enter")
            await pilot.pause(0.1)
            assert got == ["local"]

    asyncio.run(run())
