"""Claude plan usage while the Claude Code CLI provider is in use. UI-free.

What Claude Code's own ``/usage`` screen shows — the 5-hour session limit, the
weekly limits, extra-usage credits — asked of the user's ``claude`` with the
``get_usage`` control request (``claude_agent_client.control_request``). The
binary fetches it with its own login; Jarvis never sees a credential and makes
no model request for it. Between fetches the ``RateLimitEvent``s Claude Code
sends with every reply keep the numbers current (``note_rate_limit``).

Same shape of life as ``openrouter_usage``: ``public()`` never blocks — it
hands back the last answer and, when stale, starts one refresh on a daemon
thread. Refreshes stay modest: ``TTL`` while a ``claude`` process is running
(it answers at once), ``TTL_SPAWN`` when one would have to be started.
``HARNESS_CLAUDE_USAGE=0`` turns it off.
"""
from __future__ import annotations

import datetime as _dt
import os
import threading
import time
from typing import Any

TTL = 120.0          # a fresh answer is reused this long (a process is running)
TTL_SPAWN = 600.0    # … when asking means starting `claude` just for it
AFTER_TURN = 5.0     # a finished request refreshes this soon …
MIN_GAP = 20.0       # … but never sooner than this after the last ask (long tool loops)
RETRY = 120.0        # after a failed ask
USAGE_URL = "https://claude.ai/settings/usage"

_LABELS = {
    "session": "Current session",
    "five_hour": "Current session",
    "weekly_all": "Weekly · all models",
    "seven_day": "Weekly · all models",
    "weekly_opus": "Weekly · Opus",
    "seven_day_opus": "Weekly · Opus",
    "weekly_sonnet": "Weekly · Sonnet",
    "seven_day_sonnet": "Weekly · Sonnet",
}
_KIND = {"five_hour": "session", "seven_day": "weekly_all", "seven_day_opus": "weekly_opus",
         "seven_day_sonnet": "weekly_sonnet"}
_PLANS = {"pro": "Claude Pro", "max": "Claude Max", "team": "Claude Team", "enterprise": "Claude Enterprise",
          "free": "Claude Free"}

_lock = threading.Lock()
_data: dict | None = None
_fetched_at = 0.0
_due_at = 0.0
_error: str | None = None
_inflight = False


def enabled() -> bool:
    return os.getenv("HARNESS_CLAUDE_USAGE", "1").strip() != "0"


def active() -> bool:
    from .. import state

    if getattr(state, "provider", "") != "claude_agent" or not enabled():
        return False
    try:
        from .claude_agent import is_ready

        return is_ready()
    except Exception:
        return False


# ─── parsing ─────────────────────────────────────────────────────────────


def _pct(v: Any) -> int | None:
    try:
        return max(0, min(100, round(float(v))))
    except (TypeError, ValueError):
        return None


def level(percent: int | None) -> str:
    if percent is None:
        return "ok"
    return "ok" if percent < 70 else "warn" if percent < 90 else "low"


def _limit(kind: str, percent: Any, resets_at: Any, *, severity: str = "", active_: bool = False) -> dict:
    p = _pct(percent)
    return {"kind": kind, "label": _LABELS.get(kind, kind.replace("_", " ").capitalize()), "percent": p,
            "resets_at": resets_at or None, "level": level(p) if severity in ("", "normal") else (
                "low" if severity in ("critical", "error", "rejected") else "warn"),
            "active": bool(active_)}


def parse(raw: dict) -> dict:
    """``get_usage`` → what the UIs show (only plan/limit numbers — the
    answer carries no secrets, and nothing else of it is kept)."""
    raw = raw if isinstance(raw, dict) else {}
    plan = str(raw.get("subscription_type") or "")
    rl = raw.get("rate_limits") if isinstance(raw.get("rate_limits"), dict) else {}
    limits: list[dict] = []
    for item in rl.get("limits") or []:
        if isinstance(item, dict) and item.get("kind"):
            limits.append(_limit(str(item["kind"]), item.get("percent"), item.get("resets_at"),
                                 severity=str(item.get("severity") or ""), active_=bool(item.get("is_active"))))
    if not limits:  # older Claude Code: the per-window fields only
        for key, kind in _KIND.items():
            win = rl.get(key)
            if isinstance(win, dict) and win.get("utilization") is not None:
                limits.append(_limit(kind, win.get("utilization"), win.get("resets_at")))
    limits.sort(key=lambda x: (x["kind"] != "session", x["kind"]))
    extra = None
    spend = rl.get("spend") if isinstance(rl.get("spend"), dict) else None
    eu = rl.get("extra_usage") if isinstance(rl.get("extra_usage"), dict) else None
    if spend and spend.get("enabled"):
        def amount(m: Any) -> float | None:
            if not isinstance(m, dict) or m.get("amount_minor") is None:
                return None
            return float(m["amount_minor"]) / (10 ** int(m.get("exponent") or 2))
        used = amount(spend.get("used"))
        cap = amount(spend.get("limit"))
        extra = {"used": used, "limit": cap, "currency": (spend.get("used") or {}).get("currency") or "USD",
                 "percent": _pct(spend.get("percent"))}
    elif eu and eu.get("is_enabled"):
        extra = {"used": eu.get("used_credits"), "limit": eu.get("monthly_limit"),
                 "currency": eu.get("currency") or "USD", "percent": _pct(eu.get("utilization"))}
    return {
        "plan": plan,
        "plan_label": _PLANS.get(plan.lower(), f"Claude {plan.title()}" if plan else ""),
        "available": bool(raw.get("rate_limits_available", bool(limits))),
        "limits": limits,
        "extra": extra,
        "activity": _activity(raw.get("behaviors")),
    }


def _activity(raw: Any) -> dict | None:
    """Requests / sessions on this Claude account today and this week — every
    Claude Code session, not only Jarvis's (``behaviors`` in ``get_usage``)."""
    if not isinstance(raw, dict):
        return None
    out: dict[str, dict] = {}
    for key in ("day", "week"):
        win = raw.get(key)
        if not isinstance(win, dict):
            continue
        try:
            out[key] = {"requests": int(win.get("request_count") or 0),
                        "sessions": int(win.get("session_count") or 0)}
        except (TypeError, ValueError):
            continue
    return out or None


# ─── fetching ────────────────────────────────────────────────────────────


def _ask() -> dict:
    from .claude_agent_client import control_request

    return parse(control_request({"subtype": "get_usage"}))


def _run() -> None:
    global _data, _fetched_at, _due_at, _error, _inflight
    try:
        data = _ask()
    except Exception as e:  # noqa: BLE001 — the reason is shown, numbers kept
        with _lock:
            _error = "Couldn’t ask Claude Code for usage" + (f" ({str(e)[:120]})" if str(e) else "")
            _due_at = time.time() + RETRY
            _inflight = False
        return
    with _lock:
        _data, _error = data, None
        _fetched_at = time.time()
        # How long it stays fresh depends on whether a `claude` is running
        # *then* (``_stale``), not when this answer came in: one fetched while
        # nothing ran used to stay 10 minutes even through a busy session.
        _due_at = _fetched_at + TTL
        _inflight = False


def _live() -> bool:
    try:
        from .claude_agent_client import engine

        return engine().live_session() is not None
    except Exception:
        return False


def _stale(now: float, due: float, fetched: float) -> bool:
    """Due, and asking is cheap (a process is running) or the answer is old."""
    if now < due:
        return False
    return now - fetched >= TTL_SPAWN or _live()


def refresh(*, wait: bool = True) -> dict | None:
    """Ask now (``wait``) or in the background; returns ``public(kick=False)``."""
    global _inflight
    if not enabled():
        return None
    with _lock:
        if _inflight and not wait:
            return _snapshot_locked()
        _inflight = True
    if wait:
        _run()
    else:
        threading.Thread(target=_run, name="claude-usage", daemon=True).start()
    return public(kick=False, any_provider=True)


def invalidate(after: float = AFTER_TURN) -> None:
    """A request just used some of the plan: refresh soon (at most every
    ``MIN_GAP`` seconds, so a long tool loop keeps the numbers moving without
    asking after every step)."""
    global _due_at
    with _lock:
        if _data is not None:
            _due_at = min(_due_at, max(time.time() + after, _fetched_at + MIN_GAP))


def note_rate_limit(info: Any) -> None:
    """A ``RateLimitInfo`` Claude Code sent with a reply: update that window
    at once (no request)."""
    global _data
    if info is None:
        return
    kind = _KIND.get(str(getattr(info, "rate_limit_type", "") or ""))
    util = getattr(info, "utilization", None)
    if not kind or util is None:
        return
    try:
        pct = float(util) * 100 if float(util) <= 1 else float(util)
    except (TypeError, ValueError):
        return
    resets = getattr(info, "resets_at", None)
    iso = (_dt.datetime.fromtimestamp(float(resets), _dt.timezone.utc).isoformat() if resets else None)
    status = str(getattr(info, "status", "") or "")
    sev = "critical" if status == "rejected" else "warning" if status == "allowed_warning" else ""
    with _lock:
        data = dict(_data) if _data else {"plan": "", "plan_label": "", "available": True, "limits": [], "extra": None}
        limits = [dict(x) for x in data.get("limits") or []]
        new = _limit(kind, pct, iso, severity=sev)
        for i, x in enumerate(limits):
            if x["kind"] == kind:
                new["active"] = x.get("active", False)
                new["resets_at"] = new["resets_at"] or x.get("resets_at")
                limits[i] = new
                break
        else:
            limits.append(new)
        limits.sort(key=lambda x: (x["kind"] != "session", x["kind"]))
        data["limits"] = limits
        _data = data


def reset() -> None:
    global _data, _fetched_at, _due_at, _error, _inflight
    with _lock:
        _data, _fetched_at, _due_at, _error, _inflight = None, 0.0, 0.0, None, False


def _snapshot_locked() -> dict:
    out = dict(_data) if _data else {"limits": [], "extra": None, "plan": "", "plan_label": "", "available": True}
    session = next((x for x in out.get("limits") or [] if x["kind"] == "session"), None)
    out.update({
        "status": "ok" if _data else ("error" if _error else "loading"),
        "error": _error,
        "loading": _inflight,
        "fetched_at": _fetched_at or None,
        "session": session,
        "usage_url": USAGE_URL,
    })
    return out


def public(*, kick: bool = True, any_provider: bool = False) -> dict | None:
    """What the UIs show, or ``None`` while Claude Code CLI isn't the provider
    (``any_provider``: whenever it's set up). Never blocks."""
    global _inflight
    if not (active() if not any_provider else enabled()):
        return None
    start = False
    with _lock:
        due, fetched, busy = _due_at, _fetched_at, _inflight
    # Outside the lock: whether a process runs asks the engine (its own lock).
    want = kick and not busy and _stale(time.time(), due, fetched)
    with _lock:
        if want and not _inflight:
            _inflight = start = True
        snap = _snapshot_locked()
    if start:
        threading.Thread(target=_run, name="claude-usage", daemon=True).start()
    return snap


# ─── formatting (terminal) ───────────────────────────────────────────────


def headline(info: dict) -> str | None:
    """``34% · resets 2h`` for the session window."""
    s = (info or {}).get("session")
    if not s or s.get("percent") is None:
        return None
    return f"{s['percent']}% used"
