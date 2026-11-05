"""Usage ledger (storage/usage_log.py), the Claude Code CLI's live cost and
plan-usage refresh rules."""
from __future__ import annotations

import time

import pytest

from jarvis.storage import usage_log


@pytest.fixture
def ledger(tmp_path, monkeypatch):
    monkeypatch.setattr(usage_log, "SESSIONS_DB", tmp_path / "sessions.db")
    monkeypatch.setattr(usage_log, "on_plan", lambda provider: provider == "claude_agent")
    usage_log.reset_cache()
    yield usage_log
    usage_log.reset_cache()


def test_request_cost_prices_cache_reads_and_writes():
    # $4 / $20 per 1M: 1M uncached + 1M read (0.1×) + 1M written (1.25×) + 1M out.
    assert usage_log.request_cost((4.0, 20.0), uncached=1_000_000, cache_read=1_000_000,
                                  cache_write=1_000_000, output=1_000_000) == pytest.approx(4 + 0.4 + 5 + 20)
    assert usage_log.request_cost(None, uncached=10, cache_read=0, cache_write=0, output=10) == 0.0


def test_records_add_up_per_chat_and_per_provider(ledger, monkeypatch):
    monkeypatch.setattr(ledger, "price_for", lambda model, provider: (2.0, 10.0))
    ledger.record(provider="anthropic", model="claude-sonnet-5-5", session_id=7,
                  uncached=1000, cache_read=9000, cache_write=0, output=500)
    ledger.record(provider="anthropic", model="claude-sonnet-5-5", session_id=7,
                  uncached=500, cache_read=10_000, cache_write=100, output=200, source="subagent")
    ledger.record(provider="claude_agent", model="opus", session_id=7, uncached=10, cache_read=90,
                  output=5, cost=0.25)
    ledger.record(provider="openrouter", model="x/y", session_id=8, uncached=100, output=100, cost=0.01)

    chat = ledger.chat_totals(7)
    assert chat["requests"] == 3
    assert chat["input"] == 1510 and chat["cache_read"] == 19_090 and chat["output"] == 705
    assert chat["plan_cost"] == pytest.approx(0.25)  # Claude plan: API-equivalent, not billed
    assert chat["cost"] == pytest.approx((1000 * 2 + 9000 * 0.2 + 500 * 10 + 500 * 2 + 10_000 * 0.2
                                          + 100 * 2.5 + 200 * 10) / 1e6)
    assert chat["cache_pct"] == round(19_090 * 100 / (1510 + 19_090 + 100))

    # Fresh from the database, not the in-memory running total.
    ledger.reset_cache()
    assert ledger.chat_totals(7)["requests"] == 3
    assert ledger.chat_totals(999) is None

    s = ledger.summary(7, session_id=8)
    assert s["totals"]["requests"] == 4
    assert [p["provider"] for p in s["providers"]][0] == "claude_agent"  # most expensive first
    assert {(m["provider"], m["model"]) for m in s["models"]} == {
        ("anthropic", "claude-sonnet-5-5"), ("claude_agent", "opus"), ("openrouter", "x/y")}
    assert len(s["by_day"]) == 1 and s["by_day"][0]["requests"] == 4
    assert s["sources"]["subagent"]["requests"] == 1
    assert s["chat"]["requests"] == 1


def test_summary_window_skips_old_days(ledger):
    old = time.time() - 40 * 86400
    ledger.record(provider="anthropic", model="m", session_id=1, uncached=1, output=1, cost=1.0, ts=old)
    ledger.record(provider="anthropic", model="m", session_id=1, uncached=1, output=1, cost=2.0)
    assert ledger.summary(30)["totals"]["cost"] == pytest.approx(2.0)
    assert ledger.summary(0)["totals"]["cost"] == pytest.approx(3.0)
    assert ledger.summary(0)["first_ts"] == pytest.approx(old)


def test_recording_can_be_turned_off(ledger, monkeypatch):
    monkeypatch.setenv("HARNESS_USAGE_LOG", "0")
    ledger.record(provider="anthropic", model="m", session_id=3, uncached=1, output=1, cost=1.0)
    monkeypatch.delenv("HARNESS_USAGE_LOG")
    assert ledger.chat_totals(3) is None


# ─── Claude Code CLI: a cost per step, settled with Claude Code's figure ───


def _session():
    from jarvis.auth import claude_agent_client as cac

    s = object.__new__(cac._Session)
    s.cost_seen = s.cost_pending = s.est_open = 0.0
    s.cost_settle = False
    return s


class _Result:
    def __init__(self, total):
        self.total_cost_usd = total


def test_tool_steps_report_estimates_and_the_turn_end_settles():
    s = _session()
    # Two tool steps: no result yet, so each reports its estimate.
    assert s.hand_out_cost(0.10, final=False) == pytest.approx(0.10)
    assert s.hand_out_cost(0.15, final=False) == pytest.approx(0.15)
    # The final step brings Claude Code's figure for the whole turn: 0.30.
    s.take_cost(_Result(0.30))
    assert s.hand_out_cost(0.07, final=True) == pytest.approx(0.05)  # 0.30 − 0.25 already shown
    # Next turn: the running total goes 0.30 → 0.50.
    assert s.hand_out_cost(0.12, final=False) == pytest.approx(0.12)
    s.take_cost(_Result(0.50))
    assert s.hand_out_cost(0.09, final=True) == pytest.approx(0.08)
    assert 0.10 + 0.15 + 0.05 + 0.12 + 0.08 == pytest.approx(0.50)


def test_no_figure_from_claude_code_keeps_the_estimates():
    s = _session()
    assert s.hand_out_cost(0.10, final=False) == pytest.approx(0.10)
    s.take_cost(_Result(0))
    assert s.hand_out_cost(0.04, final=True) == pytest.approx(0.04)
    assert s.est_open == 0.0


def test_an_interrupted_turn_settles_on_the_next_step():
    s = _session()
    s.hand_out_cost(0.20, final=False)
    s.take_cost(_Result(0.18))  # drained after Esc
    assert s.hand_out_cost(0.05, final=False) == pytest.approx(-0.02 + 0.05)
    assert s.est_open == pytest.approx(0.05)


# ─── Plan usage refresh: often enough during a session ───────────────────


def test_answer_goes_stale_after_ttl_only_while_a_process_runs(monkeypatch):
    from jarvis.auth import claude_agent_usage as u

    now = 10_000.0
    monkeypatch.setattr(u, "_live", lambda: True)
    assert not u._stale(now, now + 1, now - u.TTL + 1)
    assert u._stale(now, now, now - u.TTL)
    monkeypatch.setattr(u, "_live", lambda: False)
    assert not u._stale(now, now, now - u.TTL)  # starting `claude` for it: wait longer
    assert u._stale(now, now, now - u.TTL_SPAWN)


def test_invalidate_refreshes_soon_but_not_after_every_step(monkeypatch):
    from jarvis.auth import claude_agent_usage as u

    now = time.time()
    monkeypatch.setattr(u, "_data", {"limits": []})
    monkeypatch.setattr(u, "_fetched_at", now - 2)
    monkeypatch.setattr(u, "_due_at", now + u.TTL)
    u.invalidate()
    assert u._due_at == pytest.approx(now - 2 + u.MIN_GAP, abs=0.5)
    monkeypatch.setattr(u, "_fetched_at", now - 60)
    monkeypatch.setattr(u, "_due_at", now + u.TTL)
    u.invalidate()
    assert u._due_at == pytest.approx(now + u.AFTER_TURN, abs=0.5)


def test_parse_keeps_claude_codes_activity_counts():
    from jarvis.auth import claude_agent_usage as u

    d = u.parse({"subscription_type": "team", "rate_limits": {"limits": []},
                 "behaviors": {"day": {"request_count": 382, "session_count": 20},
                               "week": {"request_count": 4227, "session_count": 177}}})
    assert d["activity"] == {"day": {"requests": 382, "sessions": 20},
                             "week": {"requests": 4227, "sessions": 177}}
    assert u.parse({"rate_limits": {}})["activity"] is None
