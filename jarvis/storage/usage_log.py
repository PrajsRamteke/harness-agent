"""Usage ledger: one row per model request, for every provider and model.

``stream.py`` records each finished request (main chat), ``subagents/runner``
each subagent request. A row keeps the token split (uncached input, cache
read, cache write, output) and what the request cost:

* ``cost`` — USD. Real money on a pay-per-use provider (API key, OpenRouter,
  OpenCode …); on a plan (Claude Code CLI signed in to a subscription, Codex,
  Cursor …) it's the *API-equivalent* figure, flagged with ``plan=1``. The
  Claude Code CLI's figure is Claude Code's own estimate (``total_cost_usd``),
  shown live per step and settled when its turn ends (a settling row may be
  negative; sums are right).

``summary(days)`` totals rows by provider, model and day for the web Usage
dialog and the terminal's ``/cost``; ``chat_totals(session_id)`` is what this
chat has used so far — answered from memory, cheap enough to poll every second.
Same SQLite file as the sessions (``sessions.db``). ``HARNESS_USAGE_LOG=0``
turns recording off.
"""
from __future__ import annotations

import datetime as _dt
import os
import sqlite3
import threading
import time
from typing import Any

from ..constants import SESSIONS_DB

# Anthropic's prompt-cache prices, as multiples of the input price (5-minute
# cache writes). Other providers report no cache split, so these don't apply.
CACHE_READ_FACTOR = 0.1
CACHE_WRITE_FACTOR = 1.25
SOURCE_CHAT = "chat"
SOURCE_SUBAGENT = "subagent"
_MAX_DAYS = 3650

_SQL = """CREATE TABLE IF NOT EXISTS usage_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts REAL NOT NULL,
    day TEXT NOT NULL,
    session_id INTEGER,
    provider TEXT NOT NULL DEFAULT '',
    model TEXT NOT NULL DEFAULT '',
    source TEXT NOT NULL DEFAULT 'chat',
    input_tokens INTEGER NOT NULL DEFAULT 0,
    cache_read INTEGER NOT NULL DEFAULT 0,
    cache_write INTEGER NOT NULL DEFAULT 0,
    output_tokens INTEGER NOT NULL DEFAULT 0,
    cost REAL NOT NULL DEFAULT 0,
    plan INTEGER NOT NULL DEFAULT 0,
    requests INTEGER NOT NULL DEFAULT 1
)"""

_lock = threading.Lock()
_ready: set[str] = set()
_chat_cache: dict[int, dict[str, Any]] = {}


def enabled() -> bool:
    return os.getenv("HARNESS_USAGE_LOG", "1").strip() != "0"


def _conn() -> sqlite3.Connection:
    SESSIONS_DB.parent.mkdir(parents=True, exist_ok=True)
    c = sqlite3.connect(str(SESSIONS_DB), timeout=5)
    c.row_factory = sqlite3.Row
    key = str(SESSIONS_DB)
    if key not in _ready:
        with c:
            c.execute(_SQL)
            c.execute("CREATE INDEX IF NOT EXISTS idx_usage_day ON usage_events(day)")
            c.execute("CREATE INDEX IF NOT EXISTS idx_usage_session ON usage_events(session_id)")
        _ready.add(key)
    return c


def _empty() -> dict[str, Any]:
    return {"requests": 0, "input": 0, "cache_read": 0, "cache_write": 0, "output": 0,
            "cost": 0.0, "plan_cost": 0.0}


def _add(into: dict[str, Any], row: dict[str, Any]) -> None:
    into["requests"] += int(row.get("requests") or 0)
    into["input"] += int(row.get("input") or 0)
    into["cache_read"] += int(row.get("cache_read") or 0)
    into["cache_write"] += int(row.get("cache_write") or 0)
    into["output"] += int(row.get("output") or 0)
    cost = float(row.get("cost") or 0)
    if row.get("plan"):
        into["plan_cost"] += cost
    else:
        into["cost"] += cost


# ─── cost ────────────────────────────────────────────────────────────────


def request_cost(prices: tuple[float, float] | None, *, uncached: int, cache_read: int,
                 cache_write: int, output: int) -> float:
    """USD for one request at ``prices`` (USD per 1M input / output tokens)."""
    if not prices:
        return 0.0
    p_in, p_out = float(prices[0] or 0), float(prices[1] or 0)
    return (uncached * p_in + cache_read * p_in * CACHE_READ_FACTOR
            + cache_write * p_in * CACHE_WRITE_FACTOR + output * p_out) / 1_000_000


def on_plan(provider: str) -> bool:
    """Requests on this provider are covered by a subscription (a cost shown
    for them is what the same tokens would cost on the API)."""
    from ..constants.providers import (PROVIDER_ANTIGRAVITY, PROVIDER_CLAUDE_AGENT, PROVIDER_CURSOR,
                                       PROVIDER_OPENAI_CODEX)

    if provider == PROVIDER_CLAUDE_AGENT:
        try:
            from ..auth import claude_agent

            return bool(claude_agent.status().subscription)
        except Exception:
            return True
    return provider in (PROVIDER_OPENAI_CODEX, PROVIDER_ANTIGRAVITY, PROVIDER_CURSOR)


def price_for(model: str, provider: str) -> tuple[float, float] | None:
    """USD per 1M (input, output) for ``model`` — the API price even on a plan
    (an API-equivalent figure), ``None`` when unknown."""
    from ..constants.providers import PROVIDER_CLAUDE_AGENT, claude_agent_api_pricing, model_pricing

    if provider == PROVIDER_CLAUDE_AGENT:
        return claude_agent_api_pricing(model)
    try:
        return model_pricing(model, provider)
    except Exception:
        return None


# ─── recording ───────────────────────────────────────────────────────────


def record(*, provider: str, model: str, session_id: Any = None, uncached: int = 0, cache_read: int = 0,
           cache_write: int = 0, output: int = 0, cost: float | None = None, source: str = SOURCE_CHAT,
           requests: int = 1, ts: float | None = None) -> None:
    """One finished request. ``cost`` None: priced here from the model's
    price list. Never raises — usage numbers must not break a turn."""
    if not enabled():
        return
    try:
        uncached, cache_read, cache_write, output = (max(0, int(x or 0)) for x in
                                                     (uncached, cache_read, cache_write, output))
        if cost is None:
            cost = request_cost(price_for(model, provider), uncached=uncached, cache_read=cache_read,
                                cache_write=cache_write, output=output)
        plan = on_plan(provider)
        now = time.time() if ts is None else float(ts)
        day = _dt.datetime.fromtimestamp(now).strftime("%Y-%m-%d")
        sid = int(session_id) if session_id else None
        row = {"requests": requests, "input": uncached, "cache_read": cache_read, "cache_write": cache_write,
               "output": output, "cost": float(cost or 0), "plan": plan}
        with _lock:
            c = _conn()
            try:
                with c:
                    c.execute(
                        "INSERT INTO usage_events (ts, day, session_id, provider, model, source, input_tokens, "
                        "cache_read, cache_write, output_tokens, cost, plan, requests) "
                        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                        (now, day, sid, provider or "", model or "", source, uncached, cache_read, cache_write,
                         output, float(cost or 0), 1 if plan else 0, int(requests)),
                    )
            finally:
                c.close()
            if sid is not None and sid in _chat_cache:
                _add(_chat_cache[sid], row)
                _chat_cache[sid]["last"] = {"provider": provider, "model": model}
    except Exception:
        pass


# ─── reading ─────────────────────────────────────────────────────────────


def chat_totals(session_id: Any) -> dict[str, Any] | None:
    """What chat ``session_id`` has used (every request, subagents too);
    ``None`` when nothing was recorded for it."""
    if not session_id or not enabled():
        return None
    try:
        sid = int(session_id)
    except (TypeError, ValueError):
        return None
    with _lock:
        hit = _chat_cache.get(sid)
        if hit is None:
            hit = _empty()
            try:
                c = _conn()
                try:
                    rows = c.execute(
                        "SELECT SUM(requests) r, SUM(input_tokens) i, SUM(cache_read) cr, SUM(cache_write) cw, "
                        "SUM(output_tokens) o, plan, SUM(cost) cost FROM usage_events WHERE session_id=? "
                        "GROUP BY plan", (sid,)).fetchall()
                finally:
                    c.close()
            except sqlite3.Error:
                return None
            for r in rows:
                _add(hit, {"requests": r["r"], "input": r["i"], "cache_read": r["cr"], "cache_write": r["cw"],
                           "output": r["o"], "cost": r["cost"], "plan": r["plan"]})
            if len(_chat_cache) > 64:
                _chat_cache.clear()
            _chat_cache[sid] = hit
        if not hit["requests"]:
            return None
        out = dict(hit)
    out["prompt_total"] = out["input"] + out["cache_read"] + out["cache_write"]
    out["cache_pct"] = round(out["cache_read"] * 100 / out["prompt_total"]) if out["prompt_total"] else 0
    out["cost"] = round(out["cost"], 6)
    out["plan_cost"] = round(out["plan_cost"], 6)
    return out


def _since(days: int) -> str:
    """First day (local, ``YYYY-MM-DD``) of a ``days``-day window ending today;
    ``days`` ≤ 0: everything."""
    if days <= 0:
        return "0000-00-00"
    start = _dt.date.today() - _dt.timedelta(days=min(days, _MAX_DAYS) - 1)
    return start.strftime("%Y-%m-%d")


def summary(days: int = 7, *, session_id: Any = None) -> dict[str, Any]:
    """Totals for the last ``days`` days (0 = all time), by provider, by
    model and by day — plus this chat's (``session_id``)."""
    since = _since(days)
    totals = _empty()
    providers: dict[str, dict[str, Any]] = {}
    models: dict[tuple[str, str], dict[str, Any]] = {}
    by_day: dict[str, dict[str, Any]] = {}
    sources: dict[str, dict[str, Any]] = {}
    first_ts = None
    if enabled():
        try:
            c = _conn()
            try:
                rows = c.execute(
                    "SELECT day, provider, model, source, plan, SUM(requests) r, SUM(input_tokens) i, "
                    "SUM(cache_read) cr, SUM(cache_write) cw, SUM(output_tokens) o, SUM(cost) cost "
                    "FROM usage_events WHERE day >= ? GROUP BY day, provider, model, source, plan",
                    (since,)).fetchall()
                first = c.execute("SELECT MIN(ts) FROM usage_events").fetchone()
                first_ts = float(first[0]) if first and first[0] else None
            finally:
                c.close()
        except sqlite3.Error:
            rows = []
        for r in rows:
            row = {"requests": r["r"], "input": r["i"], "cache_read": r["cr"], "cache_write": r["cw"],
                   "output": r["o"], "cost": r["cost"], "plan": r["plan"]}
            _add(totals, row)
            _add(providers.setdefault(r["provider"], _empty()), row)
            _add(models.setdefault((r["provider"], r["model"]), _empty()), row)
            _add(by_day.setdefault(r["day"], _empty()), row)
            _add(sources.setdefault(r["source"] or SOURCE_CHAT, _empty()), row)

    def label(provider: str) -> str:
        try:
            from ..constants.providers import provider_label

            return provider_label(provider) or provider
        except Exception:
            return provider

    def model_label(provider: str, model: str) -> str:
        """A Claude Code alias (``opus``) as the model it runs ("Opus 5.5")."""
        if provider == "claude_agent":
            try:
                from ..auth import claude_agent

                return claude_agent.display_name(model) or model
            except Exception:
                return model
        return model

    def finish(d: dict[str, Any]) -> dict[str, Any]:
        d = dict(d)
        d["cost"] = round(d["cost"], 6)
        d["plan_cost"] = round(d["plan_cost"], 6)
        d["tokens"] = d["input"] + d["cache_read"] + d["cache_write"] + d["output"]
        return d

    def weight(d: dict[str, Any]) -> tuple:
        return (d["cost"] + d["plan_cost"], d["input"] + d["cache_read"] + d["cache_write"] + d["output"])

    prov_rows = [{"provider": p, "label": label(p), **finish(v)}
                 for p, v in sorted(providers.items(), key=lambda kv: weight(kv[1]), reverse=True)]
    model_rows = [{"provider": p, "provider_label": label(p), "model": m, "model_label": model_label(p, m),
                   **finish(v)}
                  for (p, m), v in sorted(models.items(), key=lambda kv: weight(kv[1]), reverse=True)]
    day_rows = [{"day": d, **finish(v)} for d, v in sorted(by_day.items())]
    return {
        "days": days,
        "since": since if days > 0 else None,
        "first_ts": first_ts,
        "totals": finish(totals),
        "providers": prov_rows,
        "models": model_rows,
        "by_day": day_rows,
        "sources": {k: finish(v) for k, v in sources.items()},
        "chat": chat_totals(session_id),
        "enabled": enabled(),
    }


def reset_cache() -> None:
    """Tests: forget the per-chat totals and schema check."""
    with _lock:
        _chat_cache.clear()
        _ready.clear()
