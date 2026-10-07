"""Which thinking efforts a model takes: discovery, clamping, request shape,
refusal recovery, and what the terminal / web show.

Every provider's own catalog is the source (Anthropic Models API, Codex,
OpenRouter, models.dev); a model nothing is recorded for must behave exactly as
before this existed. Caches are redirected to a temp dir for every test.
"""
from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace

import httpx
import pytest

from jarvis import state
from jarvis.auth import anthropic_models, catalog_cache, codex_catalog, models_dev, openrouter_catalog
from jarvis.auth import thinking_caps as tc
from jarvis.constants import providers as pv
from jarvis.repl import thinking

AN, CX, OR, ZEN, GO = (pv.PROVIDER_ANTHROPIC, pv.PROVIDER_OPENAI_CODEX, pv.PROVIDER_OPENROUTER,
                       pv.PROVIDER_OPENCODE_ZEN, pv.PROVIDER_OPENCODE)


@pytest.fixture(autouse=True)
def _caches(tmp_path, monkeypatch):
    monkeypatch.setattr(catalog_cache, "CACHE_DIR", tmp_path / "model_catalog")
    thinking._announced.clear()
    tc._memo.clear()
    yield
    thinking._announced.clear()
    tc._memo.clear()


def _seed_models_dev(provider_id: str, models: list[dict], *, native: str | None = None):
    raw = {provider_id: {
        "id": provider_id, "name": provider_id, "npm": "@ai-sdk/openai-compatible",
        "api": "https://api.example.com/v1", "env": ["X_API_KEY"],
        "models": {m["id"]: m for m in models},
    }}
    models_dev.store(models_dev.trim(raw))


def _md(mid, options="missing", *, reasoning=True):
    row = {"id": mid, "name": mid, "tool_call": True, "reasoning": reasoning,
           "modalities": {"input": ["text"], "output": ["text"]}, "limit": {"context": 1000, "output": 100},
           "release_date": "2026-01-01"}
    if options != "missing":
        row["reasoning_options"] = options
    return row


# ── discovery ─────────────────────────────────────────────────────────────────

def test_unknown_model_changes_nothing():
    caps = tc.thinking_caps("never-heard-of-it", ZEN)
    assert caps is tc.UNKNOWN and caps.mode == tc.MODE_UNKNOWN
    # the legacy list is offered, "none" included, and nothing is clamped
    assert [r["value"] for r in tc.choice_rows(caps)] == ["xhigh", "high", "medium", "low", "minimal", "none"]
    eff = thinking.effective("never-heard-of-it", ZEN, True, "xhigh")
    assert (eff.on, eff.effort, eff.note) == (True, "xhigh", "")


def test_models_dev_effort_levels_toggle_fixed_and_none():
    _seed_models_dev("prov", [
        _md("lv", [{"type": "effort", "values": ["low", "medium", "high"]}]),
        _md("lvoff", [{"type": "effort", "values": ["none", "high", "max"]}]),
        _md("sw", [{"type": "toggle"}, {"type": "budget_tokens"}]),
        _md("fx", []),
        _md("nothink", reasoning=False),
        _md("silent"),                         # reasons, but models.dev doesn't say how
    ])
    p = "md:prov"
    lv = tc.thinking_caps("lv", p)
    assert lv.mode == tc.MODE_LEVELS and lv.levels == ("low", "medium", "high") and not lv.can_off
    lo = tc.thinking_caps("lvoff", p)
    assert lo.levels == ("high", "max") and lo.can_off          # "none" is not a level: it's can_off
    assert tc.thinking_caps("sw", p).mode == tc.MODE_TOGGLE
    assert tc.thinking_caps("fx", p).mode == tc.MODE_FIXED
    assert tc.thinking_caps("nothink", p).mode == tc.MODE_NONE
    assert tc.thinking_caps("silent", p) is tc.UNKNOWN          # never guess from silence


def test_models_dev_native_gateways_zen_go_and_free_tier():
    raw = {"opencode": {"id": "opencode", "name": "OpenCode Zen", "npm": "@ai-sdk/openai-compatible",
                        "api": "https://opencode.ai/zen/v1", "env": [],
                        "models": {"gpt-x": _md("gpt-x", [{"type": "effort", "values": ["minimal", "low", "high"]}])}}}
    models_dev.store(models_dev.trim(raw))
    assert tc.thinking_caps("gpt-x", ZEN).levels == ("minimal", "low", "high")
    # the free tier is Zen's gateway: same models, same answer
    assert tc.thinking_caps("gpt-x", pv.PROVIDER_HARNESS_AGENT).levels == ("minimal", "low", "high")


def test_codex_levels_default_and_old_cache():
    entry = {"slug": "gpt-t", "visibility": "list", "priority": 1, "default_reasoning_level": "medium",
             "supported_reasoning_levels": [{"effort": "low"}, {"effort": "medium"}, {"effort": "ultra"}]}
    m = codex_catalog._to_model(entry)
    assert m.efforts == ("low", "medium", "ultra") and m.default_effort == "medium"
    catalog_cache.write(codex_catalog.CACHE_NAME, codex_catalog._encode([m]))
    caps = tc.thinking_caps("gpt-t", CX)
    assert caps.levels == ("low", "medium", "ultra") and caps.default == "medium" and not caps.can_off
    # a cache written before levels were kept says nothing — and nothing is assumed
    catalog_cache.write(codex_catalog.CACHE_NAME, [{"id": "gpt-t", "label": "x", "priority": 0}])
    assert tc.thinking_caps("gpt-t", CX) is tc.UNKNOWN
    # a "none" level means thinking can be switched off
    off = codex_catalog._to_model({**entry, "supported_reasoning_levels": [{"effort": "none"}, {"effort": "low"}]})
    catalog_cache.write(codex_catalog.CACHE_NAME, codex_catalog._encode([off]))
    assert tc.thinking_caps("gpt-t", CX).can_off


def test_openrouter_reasoning_block():
    entries = [
        {"id": "a/with", "reasoning": {"mandatory": False, "supported_efforts": ["max", "high", "low"], "default_effort": "high"}},
        {"id": "a/forced", "reasoning": {"mandatory": True, "supported_efforts": ["high", "medium"]}},
        {"id": "a/rfixed", "reasoning": {"mandatory": True}},
        {"id": "a/params", "supported_parameters": ["include_reasoning"]},
        {"id": "a/plain", "supported_parameters": ["tools"]},
    ]
    catalog_cache.write(openrouter_catalog.REASONING_CACHE, openrouter_catalog.reasoning_map(entries))
    c = tc.thinking_caps("a/with", OR)
    assert c.levels == ("low", "high", "max") and c.can_off and c.default == "high"
    assert not tc.thinking_caps("a/forced", OR).can_off
    assert tc.thinking_caps("a/rfixed", OR).mode == tc.MODE_FIXED
    assert tc.thinking_caps("a/plain", OR).mode == tc.MODE_NONE
    assert tc.thinking_caps("a/missing", OR) is tc.UNKNOWN


def test_anthropic_capabilities_and_builtin_fallback():
    class M:  # what client.models.list() yields: pydantic-like
        def __init__(self, d):
            self._d = d

        def model_dump(self):
            return self._d

    caps = {"thinking": {"supported": True, "types": {"adaptive": {"supported": True}, "enabled": {"supported": False}}},
            "effort": {"supported": True, "low": {"supported": True}, "high": {"supported": True},
                       "xhigh": {"supported": False}, "max": {"supported": True}}}
    row = anthropic_models._capability_row(M({"id": "claude-x", "capabilities": caps}))
    assert row == ("claude-x", {"on": 1, "e": ["low", "high", "max"], "a": 1, "b": 0})
    haiku = {"thinking": {"supported": True, "types": {"adaptive": {"supported": False}, "enabled": {"supported": True}}},
             "effort": {"supported": False}}
    plain = {"thinking": {"supported": False, "types": {}}, "effort": {"supported": False}}
    assert anthropic_models._capability_row(M({"id": "no-caps"})) is None          # older API / proxy
    catalog_cache.write(anthropic_models.CAPS_CACHE, {
        "claude-x": row[1],
        "claude-haiku-9": anthropic_models._capability_row(M({"id": "h", "capabilities": haiku}))[1],
        "claude-old": anthropic_models._capability_row(M({"id": "o", "capabilities": plain}))[1],
    })
    x = tc.thinking_caps("claude-x", AN)
    assert x.levels == ("low", "high", "max") and not x.can_off    # adaptive-only: always sent a level
    assert tc.thinking_caps("claude-haiku-9", AN).mode == tc.MODE_TOGGLE
    assert tc.thinking_caps("claude-old", AN).mode == tc.MODE_NONE
    # nothing fetched yet: the documented adaptive line-up still gets its ladder
    catalog_cache.write(anthropic_models.CAPS_CACHE, {})
    b = tc.thinking_caps("claude-opus-5-5", AN)
    assert b.levels == ("low", "medium", "high", "xhigh", "max") and b.source == "built-in"
    assert tc.thinking_caps("claude-haiku-4-5", AN) is tc.UNKNOWN


def test_fetch_failures_and_garbage_never_raise(tmp_path):
    class Boom:
        class models:
            @staticmethod
            def list(**_k):
                raise RuntimeError("offline")

    assert anthropic_models.fetch_anthropic_capabilities(Boom) == {}
    assert anthropic_models.refresh_anthropic_capabilities(Boom) is False
    for name in ("codex_models", "anthropic_caps", "openrouter_reasoning", tc.REFUSED_CACHE):
        (catalog_cache.CACHE_DIR).mkdir(parents=True, exist_ok=True)
        (catalog_cache.CACHE_DIR / f"{name}.json").write_text("{not json")
    for prov in (AN, CX, OR, ZEN, "md:zzz", ""):
        assert isinstance(tc.thinking_caps("some-model", prov), tc.ThinkCaps)
    assert tc.thinking_caps("", AN) is tc.UNKNOWN
    assert tc.refused_efforts(AN, "m") == set()


def test_memo_follows_cache_changes():
    catalog_cache.write(openrouter_catalog.REASONING_CACHE, {"m": {"on": 1, "e": ["low"]}})
    assert tc.thinking_caps("m", OR).levels == ("low",)
    catalog_cache.write(openrouter_catalog.REASONING_CACHE, {"m": {"on": 1, "e": ["low", "high", "max"]}})
    assert tc.thinking_caps("m", OR).levels == ("low", "high", "max")


# ── clamping / effective ──────────────────────────────────────────────────────

def test_nearest_level():
    lv = ("low", "medium", "high")
    assert tc.nearest_level("high", lv) == "high"
    assert tc.nearest_level("xhigh", lv) == "high" and tc.nearest_level("max", lv) == "high"
    assert tc.nearest_level("minimal", lv) == "low"              # nothing below: the lowest above
    assert tc.nearest_level("medium", ("low", "high")) == "low"  # at-or-below wins
    assert tc.nearest_level("high", ()) == ""


def _levels_model(levels, *, off=False):
    catalog_cache.write(openrouter_catalog.REASONING_CACHE, {"m": {"on": 1, "e": list(levels), **({} if off else {"m": 1})}})


def test_effective_per_mode():
    _levels_model(["low", "high"])
    e = thinking.effective("m", OR, True, "xhigh")
    assert (e.on, e.effort) == (True, "high") and "`xhigh`" in e.note and e.label == "high"
    e = thinking.effective("m", OR, False, "high")                # can't be switched off
    assert (e.on, e.effort) == (True, "low") and "can't turn thinking off" in e.note
    _levels_model(["low", "high"], off=True)
    e = thinking.effective("m", OR, False, "high")
    assert (e.on, e.label) == (False, "off") and e.note == ""
    catalog_cache.write(openrouter_catalog.REASONING_CACHE, {
        "n": {"on": 0}, "f": {"on": 1, "m": 1}, "t": {"on": 1}})
    n = thinking.effective("n", OR, True, "high")
    assert not n.on and "doesn't support thinking" in n.note
    f = thinking.effective("f", OR, False, "high")
    assert f.on and f.effort == "" and f.label == "on" and "always thinks" in f.note
    t = thinking.effective("t", OR, True, "high")
    assert t.on and t.effort == "" and t.note == ""
    assert not thinking.effective("t", OR, False, "high").on


def test_choice_rows_list_unsupported_levels_greyed():
    caps = tc.ThinkCaps(known=True, source="x", reasoning=True, levels=("low", "medium", "high"), can_off=False)
    rows = {r["value"]: r for r in tc.choice_rows(caps)}
    assert [v for v, r in rows.items() if r["available"]] == ["high", "medium", "low"]
    assert rows["xhigh"]["available"] is False and "not supported" in rows["xhigh"]["why"]
    assert rows["none"]["available"] is False and "always thinks" in rows["none"]["why"]
    assert "max" not in rows and "ultra" not in rows            # extremes only when the model takes them
    top = tc.ThinkCaps(known=True, reasoning=True, levels=("low", "max", "ultra"), can_off=True)
    assert [r["value"] for r in tc.choice_rows(top)][:2] == ["ultra", "max"]


# ── the request ───────────────────────────────────────────────────────────────

def _kw(provider, model, mode=True, effort="high", client=None):
    kwargs: dict = {}
    eff = thinking.apply(kwargs, provider=provider, model=model, client=client or SimpleNamespace(),
                         think_mode=mode, effort=effort)
    return kwargs, eff


def test_claude_adaptive_request_is_clamped_to_the_models_levels():
    catalog_cache.write(anthropic_models.CAPS_CACHE, {
        "claude-sonnet-5-5": {"on": 1, "e": ["low", "medium", "high"], "a": 1, "b": 0}})
    kw, eff = _kw(AN, "claude-sonnet-5-5", effort="max")
    assert kw["output_config"] == {"effort": "high"} and kw["thinking"]["type"] == "adaptive" and eff.adjusted
    kw, _ = _kw(AN, "claude-sonnet-5-5", mode=False)              # "off": the lowest level, never `disabled`
    assert kw["output_config"] == {"effort": "low"} and kw["thinking"]["type"] == "adaptive"
    assert "disabled" not in json.dumps(kw)


def test_claude_max_is_sent_as_max_not_squeezed():
    kw, _ = _kw(AN, "claude-opus-5-5", effort="max")
    assert kw["output_config"] == {"effort": "max"}


def test_openai_style_providers_get_the_checked_level_verbatim():
    _seed_models_dev("opencode", [_md("gpt-x", [{"type": "effort", "values": ["minimal", "high", "xhigh"]}])])
    kw, _ = _kw(ZEN, "gpt-x", effort="xhigh")
    assert kw["thinking"]["effort"] == "xhigh" and kw["thinking"]["effort_exact"] is True
    kw, _ = _kw(ZEN, "gpt-x", effort="medium")                   # no medium: nearest at/below → minimal
    assert kw["thinking"]["effort"] == "minimal"
    # the client then sends exactly that — no xhigh→high squeeze for a model that takes xhigh
    from jarvis.auth.opencode_client import _opencode_reasoning_options

    assert _opencode_reasoning_options("gpt-x", {"type": "enabled", "effort": "xhigh", "effort_exact": True}) == {"reasoning_effort": "xhigh"}
    assert _opencode_reasoning_options("gpt-x", {"type": "enabled", "effort": "xhigh"}) == {"reasoning_effort": "high"}
    assert _opencode_reasoning_options("gpt-x", {"type": "enabled", "effort": "", "effort_exact": True}) == {}


def test_models_without_levels_are_sent_no_level_and_no_pointless_thinking():
    _seed_models_dev("opencode", [_md("always", []), _md("plain", reasoning=False),
                                  _md("switch", [{"type": "toggle"}])])
    assert _kw(ZEN, "always")[0] == {}                               # always thinks by itself
    assert _kw(ZEN, "plain", mode=True)[0] == {}                     # can't think: send nothing
    assert _kw(ZEN, "plain", mode=False)[0] == {}                    # …and no `disabled` either
    kw, _ = _kw(ZEN, "switch")
    assert kw["thinking"]["type"] == "enabled" and kw["thinking"]["effort"] == "" and kw["thinking"]["effort_exact"]
    assert _kw(ZEN, "switch", mode=False)[0] == {"thinking": {"type": "disabled"}}


def test_unknown_model_request_is_exactly_the_old_one():
    kw, _ = _kw(ZEN, "mystery", effort="xhigh")
    assert kw == {"thinking": {"type": "enabled", "budget_tokens": 4000, "effort": "xhigh"}}
    assert _kw(ZEN, "mystery", mode=False)[0] == {"thinking": {"type": "disabled"}}
    kw, _ = _kw(OR, "mystery")                                        # OpenRouter: budget form, no effort key
    assert kw == {"thinking": {"type": "enabled", "budget_tokens": 4000}}


def test_codex_sends_the_chosen_effort_not_always_high():
    entry = {"slug": "gpt-t", "visibility": "list", "supported_reasoning_levels": [{"effort": "low"}, {"effort": "high"}]}
    catalog_cache.write(codex_catalog.CACHE_NAME, codex_catalog._encode([codex_catalog._to_model(entry)]))
    kw, _ = _kw(CX, "gpt-t", effort="low")
    assert kw["thinking"]["effort"] == "low"
    kw, _ = _kw(CX, "gpt-t", effort="medium")                        # not offered → nearest below
    assert kw["thinking"]["effort"] == "low"


def test_note_is_said_once():
    _levels_model(["low", "high"])
    eff = thinking.effective("m", OR, True, "max")
    assert thinking.take_note(eff, OR, "m") and thinking.take_note(eff, OR, "m") == ""


# ── refusals ──────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("text,kind", [
    ("Unsupported value: 'xhigh' is not supported for reasoning.effort", "effort"),
    ("invalid reasoning_effort: must be one of low, medium, high", "effort"),
    ("thinking is not supported for this model", "thinking"),
    ("Extra inputs are not permitted: thinking", "thinking"),
    ("prompt is too long: 250000 tokens > 200000 maximum", ""),
    ("tools.0.input_schema: invalid type", ""),
    ("", ""),
])
def test_refusal_detection_is_narrow(text, kind):
    assert tc.looks_like_thinking_refusal(text) == kind


def test_recover_remembers_the_level_and_resends_one_step_down():
    _seed_models_dev("opencode", [_md("gpt-x", [{"type": "effort", "values": ["low", "high", "xhigh"]}])])
    kwargs, _ = _kw(ZEN, "gpt-x", effort="xhigh")
    assert kwargs["thinking"]["effort"] == "xhigh"
    said = thinking.recover(RuntimeError("Unsupported value: 'xhigh' for reasoning effort is not supported"),
                            kwargs, provider=ZEN, model="gpt-x", client=SimpleNamespace(),
                            think_mode=True, effort="xhigh")
    assert "xhigh" in said and "retrying" in said
    assert kwargs["thinking"]["effort"] == "high"                    # the next level down
    assert tc.refused_efforts(ZEN, "gpt-x") == {"xhigh"}
    caps = tc.thinking_caps("gpt-x", ZEN)
    assert caps.levels == ("low", "high") and caps.learned
    # …and it sticks: the next request never offers it again
    assert _kw(ZEN, "gpt-x", effort="xhigh")[0]["thinking"]["effort"] == "high"
    tc.clear_refused()
    assert tc.thinking_caps("gpt-x", ZEN).levels == ("low", "high", "xhigh")


def test_recover_drops_thinking_when_the_provider_refuses_it_outright():
    kwargs, _ = _kw(ZEN, "mystery")
    said = thinking.recover(RuntimeError("thinking is not supported for this model"), kwargs,
                            provider=ZEN, model="mystery", client=SimpleNamespace(), think_mode=True, effort="high")
    assert "without thinking" in said and "thinking" not in kwargs
    assert tc.thinking_caps("mystery", ZEN).mode == tc.MODE_NONE


def test_recover_ignores_unrelated_errors_and_requests_without_thinking():
    kwargs, _ = _kw(ZEN, "mystery")
    before = json.dumps(kwargs, sort_keys=True)
    assert thinking.recover(RuntimeError("prompt is too long"), kwargs, provider=ZEN, model="mystery",
                            client=SimpleNamespace(), think_mode=True, effort="high") == ""
    assert json.dumps(kwargs, sort_keys=True) == before and tc.refused_efforts(ZEN, "mystery") == set()
    off: dict = {}
    assert thinking.recover(RuntimeError("thinking is not supported"), off, provider=ZEN, model="plain",
                            client=SimpleNamespace(), think_mode=False, effort="none") == ""
    assert off == {} and tc.refused_efforts(ZEN, "plain") == set()   # we sent no thinking: not our doing


def test_recover_on_claude_adaptive_falls_back_to_the_lowest_effort():
    kwargs, _ = _kw(AN, "claude-opus-5-5", effort="max")
    said = thinking.recover(RuntimeError("effort: 'max' is not supported for this model"), kwargs,
                            provider=AN, model="claude-opus-5-5", client=SimpleNamespace(), think_mode=True, effort="max")
    assert said and kwargs["output_config"]["effort"] == "xhigh"


def test_main_request_loop_recovers_from_a_refused_effort(monkeypatch):
    """The real call_claude_stream: first request 400s on the effort, the second goes out lower."""
    import anthropic
    from jarvis.repl import stream

    catalog_cache.write(anthropic_models.CAPS_CACHE, {
        "claude-opus-5-5": {"on": 1, "e": ["low", "medium", "high", "xhigh", "max"], "a": 1, "b": 0}})
    sent: list[dict] = []

    def _bad_request(msg):
        req = httpx.Request("POST", "https://api.anthropic.com/v1/messages")
        return anthropic.BadRequestError(msg, response=httpx.Response(400, request=req), body=None)

    class _Ctx:
        def __init__(self, fail):
            self.fail = fail

        def __enter__(self):
            if self.fail:
                raise _bad_request("output_config.effort: 'max' is not supported for this model")
            return self

        def __exit__(self, *a):
            return False

        def __iter__(self):
            return iter(())

        def get_final_message(self):
            return SimpleNamespace(usage=SimpleNamespace(input_tokens=1, output_tokens=1),
                                   content=[], stop_reason="end_turn")

    class _Messages:
        def stream(self, **kwargs):
            sent.append(json.loads(json.dumps({k: kwargs[k] for k in ("thinking", "output_config") if k in kwargs})))
            return _Ctx(fail=len(sent) == 1)

    monkeypatch.setattr(stream, "build_system", lambda: "sys")
    monkeypatch.setattr(stream, "select_tools", lambda msgs: [])
    monkeypatch.setattr(stream, "_heal_message_history", lambda: None)
    monkeypatch.setattr(stream, "report_turn_phase", lambda *a, **k: None)
    monkeypatch.setattr(state, "messages", [{"role": "user", "content": "hi"}])
    monkeypatch.setattr(state, "stream_reply_live", False)
    monkeypatch.setattr(state, "show_internal", False)
    monkeypatch.setattr(state, "provider", AN)
    monkeypatch.setattr(state, "client", SimpleNamespace(messages=_Messages()))
    monkeypatch.setattr(state, "think_mode", True)
    monkeypatch.setattr(state, "think_effort", "max")
    monkeypatch.setattr(state, "MODEL", "claude-opus-5-5")
    stream._call_claude_stream()
    assert [s["output_config"]["effort"] for s in sent] == ["max", "xhigh"]
    assert tc.refused_efforts(AN, "claude-opus-5-5") == {"max"}


# ── commands, settings, web ───────────────────────────────────────────────────

@pytest.fixture()
def session(monkeypatch):
    monkeypatch.setattr(state, "provider", OR)
    monkeypatch.setattr(state, "MODEL", "m")
    monkeypatch.setattr(state, "think_mode", True)
    monkeypatch.setattr(state, "think_effort", "high")
    monkeypatch.setattr(state, "save_think_config", lambda: None)
    return state


def test_set_preference_refuses_what_the_model_cannot_take(session):
    _levels_model(["low", "high"])                                   # always thinks, no medium
    assert "doesn't support `medium`" in thinking.set_preference("medium")
    assert session.think_effort == "high"
    assert "always thinks" in thinking.set_preference("off") and session.think_mode is True
    assert "always thinks" in thinking.set_preference("none")
    assert thinking.set_preference("low") == "" and session.think_effort == "low"
    assert "unknown thinking setting" in thinking.set_preference("sideways")
    catalog_cache.write(openrouter_catalog.REASONING_CACHE, {"m": {"on": 0}})
    assert "doesn't support thinking" in thinking.set_preference("on")
    catalog_cache.write(openrouter_catalog.REASONING_CACHE, {"m": {"on": 1}})
    assert "only has thinking on or off — no `high` level" in thinking.set_preference("high")
    assert thinking.set_preference("on") == "" and session.think_mode is True


def test_think_command_says_why_and_lists_what_is_supported(session, monkeypatch):
    from jarvis.commands import control

    printed: list[str] = []
    monkeypatch.setattr(control.console, "print", lambda *a, **k: printed.append(str(a[0])))
    monkeypatch.setattr(control, "header_panel", lambda: None)
    _levels_model(["low", "high"])
    control._handle_think("medium")
    assert session.think_effort == "high"
    out = "\n".join(printed)
    assert "doesn't support `medium`" in out and "high · low" in out
    printed.clear()
    control._handle_think("mode")
    assert "high · low" in printed[0]


def test_web_settings_refuse_with_a_reason_and_keep_state(session):
    from jarvis.web import state_api

    _levels_model(["low", "high"])
    res = state_api.apply_settings({"think_effort": "xhigh"})
    assert "doesn't support `xhigh`" in res["error"] and session.think_effort == "high"
    res = state_api.apply_settings({"think_mode": False})
    assert "always thinks" in res["error"] and session.think_mode is True
    res = state_api.apply_settings({"think_effort": "low"})
    assert "error" not in res and session.think_effort == "low" and res["think_mode"] is True


def test_web_state_carries_what_the_model_takes(session):
    from jarvis.web import state_api

    _levels_model(["low", "medium", "high", "max"], off=True)
    t = state_api._think_fields()
    assert t["mode"] == "levels" and t["levels"] == ["low", "medium", "high", "max"] and t["can_off"]
    assert [c["value"] for c in t["choices"] if c["available"]] == ["max", "high", "medium", "low", "none"]
    assert t["current"] == "high" and t["note"] == ""
    session.think_effort = "xhigh"
    t = state_api._think_fields()
    assert (t["current"], t["effort"]) == ("high", "high")           # clamped to the model, with a note
    assert "`xhigh`" in t["note"]
    json.dumps(t)                                                     # the page gets plain JSON


def test_thinking_fields_never_break_the_state_poll(monkeypatch):
    from jarvis.web import state_api

    monkeypatch.setattr(thinking, "public", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom")))
    assert state_api._think_fields()["mode"] == "unknown"


# ── the terminal picker ───────────────────────────────────────────────────────

def test_picker_rows_grey_out_what_the_model_lacks(session):
    from jarvis.tui.think_modal import status_text, think_rows

    _levels_model(["low", "high", "max"])
    info = thinking.public()
    rows = think_rows(info)
    by = {r["value"]: r for r in rows}
    assert [r["value"] for r in rows] == ["max", "xhigh", "high", "medium", "low", "minimal", "none"]
    assert by["high"]["available"] and by["high"]["active"] and by["high"]["right"]
    assert not by["xhigh"]["available"] and "not supported" in by["xhigh"]["detail"] and by["xhigh"]["right"] == ""
    assert not by["none"]["available"]
    assert "max · high · low" in status_text(info) and "OpenRouter" in status_text(info)
    catalog_cache.write(openrouter_catalog.REASONING_CACHE, {"m": {"on": 1}})
    assert [r["value"] for r in think_rows(thinking.public())] == ["on", "none"]
    catalog_cache.write(openrouter_catalog.REASONING_CACHE, {"m": {"on": 0}})
    assert [r["value"] for r in think_rows(thinking.public())] == ["none"]


def test_footer_and_sidebar_show_what_is_really_sent(session):
    _levels_model(["low", "high"])
    session.think_effort = "max"
    eff = thinking.effective_now()
    assert eff.label == "high" and eff.adjusted
