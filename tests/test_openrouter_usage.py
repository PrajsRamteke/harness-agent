"""OpenRouter spend: parsing /key + /credits, the background refresh, the
terminal panel and the web state field (jarvis/auth/openrouter_usage.py)."""
from __future__ import annotations

import datetime as dt
import io
import time
import urllib.error

import pytest
from rich.console import Console

from jarvis import state
from jarvis.auth import openrouter_usage as oru

KEY = {
    "data": {
        "label": "sk-or-v1-bec...082",
        "limit": 20,
        "limit_reset": "monthly",
        "limit_remaining": 17.897124755,
        "effective_budget": {
            "scope": "key_limit",
            "limit_usd": 20,
            "spend_usd": 2.102875245,
            "remaining_usd": 17.897124755,
            "reset_interval": "monthly",
            "resets_at": "2026-11-01T00:00:00.000Z",
        },
        "usage": 20.021506994,
        "usage_daily": 0,
        "usage_weekly": 0.01998,
        "usage_monthly": 2.102875245,
        "byok_usage": 0,
        "byok_usage_daily": 0,
        "byok_usage_weekly": 0,
        "byok_usage_monthly": 0,
        "is_free_tier": False,
        "expires_at": None,
        "free_model_daily_requests": {"used": 0, "limit": 1000, "remaining": 1000},
    }
}
CREDITS = {"data": {"total_credits": 2200, "total_usage": 2102.866830948}}


@pytest.fixture
def on_openrouter(monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-or-v1-test")
    monkeypatch.setattr(state, "provider", "openrouter")


def _serve(monkeypatch, key=KEY, credits=CREDITS, calls=None):
    def fake(url, k):
        if calls is not None:
            calls.append(url)
        assert k == "sk-or-v1-test"
        if url == oru.KEY_URL:
            if isinstance(key, Exception):
                raise key
            return key
        if isinstance(credits, Exception):
            raise credits
        return credits

    monkeypatch.setattr(oru, "_get", fake)


def _wait_idle(timeout=2.0):
    end = time.time() + timeout
    while time.time() < end:
        if not oru.public(kick=False, any_provider=True).get("loading"):
            return
        time.sleep(0.01)


# ─── parse ───────────────────────────────────────────────────────────────

def test_parse_budget_shape():
    d = oru.parse(KEY, CREDITS)
    assert d["limit"] == 20
    assert d["remaining"] == pytest.approx(17.897, abs=1e-3)
    assert d["spent"] == pytest.approx(2.103, abs=1e-3)
    assert d["fraction"] == pytest.approx(0.105, abs=1e-3)
    assert d["reset"] == "monthly" and d["period"] == "this month"
    assert d["resets_at"] == "2026-11-01T00:00:00.000Z"
    assert d["usage"] == {"day": 0, "week": 0.01998, "month": 2.102875245, "total": 20.021506994}
    assert d["byok"] is None  # all zero → left out
    assert d["free_requests"] == {"used": 0, "limit": 1000, "remaining": 1000}
    assert d["credits"]["balance"] == pytest.approx(97.133, abs=1e-3)


def test_parse_legacy_shape_without_budget():
    d = oru.parse({"data": {"limit": 10, "limit_remaining": 4, "limit_reset": "daily", "usage": 30}})
    assert d["spent"] == 6 and d["remaining"] == 4
    assert d["period"] == "today"
    assert d["resets_at"].endswith("T00:00:00.000Z")  # next UTC midnight
    assert d["credits"] is None


def test_parse_no_limit():
    d = oru.parse({"data": {"limit": None, "usage": 3.5, "usage_monthly": 1}})
    assert d["limit"] is None and d["fraction"] is None and d["remaining"] is None
    assert d["period"] == "in total"
    assert oru.headline(d) is None


def test_next_reset_monthly_rolls_the_year():
    at = oru._next_reset("monthly", dt.datetime(2026, 12, 15, tzinfo=dt.timezone.utc))
    assert at.startswith("2027-01-01T00:00:00")


def test_money_and_headline():
    assert oru.money(20) == "$20"
    assert oru.money(17.897) == "$17.90"
    assert oru.money(0) == "$0"
    assert oru.money(0.004) == "<$0.01"
    assert oru.money(2102.87) == "$2,102.87"
    assert oru.money(None) == "—"
    d = oru.parse(KEY, CREDITS)
    assert oru.headline(d) == "$17.90 left"
    assert oru.left_level(d) == "ok"
    assert oru.left_level({**d, "remaining": 2}) == "low"
    no_limit = oru.parse({"data": {"usage": 1}}, CREDITS)
    assert oru.headline(no_limit) == "$97.13 credit"


def test_when_names_the_day_and_how_long():
    now = dt.datetime(2026, 10, 29, 0, tzinfo=dt.timezone.utc)
    assert "in 3 days" in oru.when("2026-11-01T00:00:00.000Z", now)
    assert "tomorrow" in oru.when("2026-10-30T10:00:00Z", now)
    assert oru.when(None) == ""


# ─── refresh / public ────────────────────────────────────────────────────

def test_hidden_unless_openrouter_is_the_provider(monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-or-v1-test")
    monkeypatch.setattr(state, "provider", "anthropic")
    assert oru.public() is None
    # /usage still answers for a saved key
    _serve(monkeypatch)
    assert oru.refresh(wait=True)["remaining"] == pytest.approx(17.897, abs=1e-3)


def test_hidden_without_a_key(monkeypatch):
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    monkeypatch.setattr(state, "provider", "openrouter")
    assert oru.public() is None


def test_switch_off(monkeypatch, on_openrouter):
    monkeypatch.setenv("HARNESS_OPENROUTER_USAGE", "0")
    assert oru.public() is None


def test_public_kicks_one_background_refresh(monkeypatch, on_openrouter):
    calls: list[str] = []
    _serve(monkeypatch, calls=calls)
    first = oru.public()
    assert first["status"] == "loading" and first["loading"] is True
    _wait_idle()
    info = oru.public()
    assert info["status"] == "ok" and info["remaining"] == pytest.approx(17.897, abs=1e-3)
    assert info["credits"]["balance"] == pytest.approx(97.133, abs=1e-3)
    assert info["fetched_at"] and info["error"] is None
    # fresh: no second fetch
    oru.public()
    assert calls == [oru.KEY_URL, oru.CREDITS_URL]
    # a finished turn makes it due soon
    oru.invalidate(after=0)
    oru.public()
    _wait_idle()
    assert calls.count(oru.KEY_URL) == 2


def test_refused_key(monkeypatch, on_openrouter):
    _serve(monkeypatch, key=urllib.error.HTTPError(oru.KEY_URL, 401, "nope", {}, None))
    info = oru.refresh(wait=True)
    assert info["status"] == "error" and "refused" in info["error"]
    assert oru.public()["loading"] is False  # backs off, no retry loop


def test_network_error_keeps_last_numbers(monkeypatch, on_openrouter):
    _serve(monkeypatch)
    oru.refresh(wait=True)
    _serve(monkeypatch, key=urllib.error.URLError("offline"))
    info = oru.refresh(wait=True)
    assert info["status"] == "ok" and info["remaining"] is not None
    assert info["error"] == "Couldn't reach OpenRouter"


def test_credits_are_optional(monkeypatch, on_openrouter):
    _serve(monkeypatch, credits=urllib.error.HTTPError(oru.CREDITS_URL, 403, "no", {}, None))
    info = oru.refresh(wait=True)
    assert info["status"] == "ok" and info["credits"] is None


def test_another_key_drops_the_old_numbers(monkeypatch, on_openrouter):
    _serve(monkeypatch)
    oru.refresh(wait=True)
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-or-v1-other")
    monkeypatch.setattr(oru, "_get", lambda *_a: (_ for _ in ()).throw(OSError("x")))
    info = oru.public(kick=False)
    assert info["status"] == "loading" and info.get("remaining") is None


def test_key_never_leaves_the_module(monkeypatch, on_openrouter):
    _serve(monkeypatch)
    info = oru.refresh(wait=True)
    assert "sk-or-v1-test" not in repr(info)


# ─── UIs ─────────────────────────────────────────────────────────────────

def _render(renderable) -> str:
    buf = io.StringIO()
    Console(file=buf, width=100, color_system=None).print(renderable)
    return buf.getvalue()


def test_usage_panel(monkeypatch, on_openrouter):
    from jarvis.commands.usage import usage_panel

    _serve(monkeypatch)
    text = _render(usage_panel(oru.refresh(wait=True)))
    assert "OpenRouter usage" in text
    assert "$17.90 left" in text and "of $20" in text and "monthly limit" in text
    assert "$2.10 spent this month" in text and "resets Nov 1" in text
    assert "all time $20.02" in text
    assert "$97.13 credit left" in text
    assert "sk-or-v1-bec...082" in text


def test_usage_panel_error():
    from jarvis.commands.usage import usage_panel

    text = _render(usage_panel({"status": "error", "error": "OpenRouter refused this key"}))
    assert "refused" in text


def test_usage_command_sends_web_card(monkeypatch, on_openrouter):
    from jarvis.commands import usage as cmd

    _serve(monkeypatch)
    seen = {}

    class Hooked:
        def show_usage(self, panel, info):
            seen["info"] = info

        def print(self, *a, **k):
            raise AssertionError("the hook should draw it")

    monkeypatch.setattr(cmd, "console", Hooked())
    handled, _ = cmd.handle_usage("/usage", "")
    assert handled and seen["info"]["status"] == "ok"
    assert cmd.handle_usage("/stats", "") == (False, None)


def test_usage_command_without_key(monkeypatch):
    from jarvis.commands import usage as cmd

    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    out = []

    class Plain:
        def print(self, *a, **k):
            out.append(" ".join(map(str, a)))

    monkeypatch.setattr(cmd, "console", Plain())
    cmd.handle_usage("/usage", "")
    assert "/provider openrouter" in out[0]


def test_state_fields_carry_it(monkeypatch, on_openrouter):
    from jarvis.web.state_api import state_fields

    _serve(monkeypatch)
    oru.refresh(wait=True)
    assert state_fields()["openrouter"]["remaining"] == pytest.approx(17.897, abs=1e-3)
    monkeypatch.setattr(state, "provider", "anthropic")
    assert state_fields()["openrouter"] is None


def test_dispatch_knows_usage(monkeypatch, on_openrouter):
    from jarvis.commands import usage as cmd
    from jarvis.commands.dispatch import handle_slash

    called = []
    monkeypatch.setattr(cmd, "show_usage", lambda **k: called.append(k))
    assert handle_slash("/usage")[0] == "ok"
    assert called


# ─── /key row button → usage dialog (TUI) ────────────────────────────────

from tests.test_tui_dialogs import hermetic_app  # noqa: E402,F401  (fixture)


def test_key_list_usage_tag_click_and_u_open_the_dialog(hermetic_app, monkeypatch, on_openrouter):
    import asyncio

    import jarvis.tui.key_modal as km
    from jarvis.tui.usage_modal import UsageScreen

    _serve(monkeypatch)

    async def run() -> None:
        app = hermetic_app()
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause(0.3)
            app.push_screen(km.KeyModalScreen(focus="openrouter"))
            await pilot.pause(0.3)
            opts = app.screen.query_one("#key_list")
            idx = opts.get_option_index(km._key_id("openrouter"))
            # only OpenRouter's row carries the tag
            texts = {opts.get_option_at_index(i).id: _render(opts.get_option_at_index(i).prompt)
                     for i in range(opts.option_count)}
            assert "◎ usage" in texts[km._key_id("openrouter")]
            assert all("◎ usage" not in t for k, t in texts.items() if k != km._key_id("openrouter"))

            # a click on the tag opens the dialog (not the edit-key prompt)
            row_y = opts.region.y + opts.styles.padding.top + idx - opts.scroll_offset.y
            tag_x = opts.content_region.right - 4
            await pilot.click(offset=(tag_x, row_y))
            await pilot.pause(0.5)
            assert isinstance(app.screen, UsageScreen)
            body = _render(app.screen.query_one("#usage_body").content)
            assert "$17.90" in body and "left" in body
            await pilot.press("escape")
            await pilot.pause(0.2)
            assert isinstance(app.screen, km.KeyModalScreen)

            # u on the highlighted OpenRouter row does the same
            opts.highlighted = idx
            await pilot.press("u")
            await pilot.pause(0.3)
            assert isinstance(app.screen, UsageScreen)

    asyncio.run(run())


def test_web_usage_route(monkeypatch, on_openrouter):
    """GET /api/usage?refresh=1 asks OpenRouter on the handler thread."""
    from jarvis.web import handler

    _serve(monkeypatch)
    src = __import__("inspect").getsource(handler)
    assert '"/api/usage"' in src
    info = oru.refresh(wait=True)
    assert oru.public(kick=False, any_provider=True)["remaining"] == info["remaining"]
