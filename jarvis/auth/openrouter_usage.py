"""OpenRouter spend for the key in use — ``GET /api/v1/key`` + ``/api/v1/credits``. UI-free.

Shown while OpenRouter is the provider: the TUI sidebar's *OpenRouter* section
and footer (``$17.90 left``), ``/usage`` (a panel in the terminal, a card on
the web) and the web sidebar's usage card. Every one of them reads
``public()``, which **never touches the network**: it hands back the last
answer and, when that is stale, starts one refresh on a daemon thread
(stale-while-revalidate). The UIs repaint on their own timers (TUI 2 s,
web ``StateWatcher`` 1 s), so a finished refresh shows up by itself.

``/key`` says what this key may spend (``effective_budget`` — newer — or
``limit`` / ``limit_remaining`` / ``limit_reset``), what it spent today /
this week / this month / ever, BYOK spend and the free-model request
allowance. ``/credits`` is the account's balance (bought − used); a key
that may not read it just leaves it out.

The key never leaves this module: rows carry the key's own masked ``label``.
``HARNESS_OPENROUTER_USAGE=0`` turns all of it off.
"""
from __future__ import annotations

import datetime as _dt
import hashlib
import json
import os
import threading
import time
import urllib.error
import urllib.request

KEY_URL = "https://openrouter.ai/api/v1/key"
CREDITS_URL = "https://openrouter.ai/api/v1/credits"
ACTIVITY_URL = "https://openrouter.ai/activity"
REQUEST_HEADERS = {"Accept": "application/json", "User-Agent": "harness-agent/1.0"}
TIMEOUT = 8.0

TTL = 60.0             # a fresh answer is reused this long
AFTER_TURN = 4.0       # a finished turn refetches this soon (OpenRouter books spend a moment later)
RETRY_NETWORK = 60.0   # after a failed fetch …
RETRY_REFUSED = 600.0  # … and after the key was refused

_PERIOD = {"daily": "today", "weekly": "this week", "monthly": "this month"}
_USAGE_FIELD = {"daily": "usage_daily", "weekly": "usage_weekly", "monthly": "usage_monthly"}

_lock = threading.Lock()
_data: dict | None = None       # last good answer (``parse``)
_key_id = ""                    # fingerprint of the key it belongs to
_fetched_at = 0.0
_due_at = 0.0                   # next refresh (0 = now)
_error: str | None = None
_inflight = False


# ─── key ─────────────────────────────────────────────────────────────────

def enabled() -> bool:
    return (os.getenv("HARNESS_OPENROUTER_USAGE", "") or "").strip().lower() not in ("0", "false", "no", "off")


def api_key() -> str:
    """The OpenRouter key Jarvis would use (env wins, like the client) — never prompts."""
    env = (os.getenv("OPENROUTER_API_KEY") or "").strip()
    if env:
        return env
    try:
        from ..constants import OPENROUTER_KEY_FILE

        if OPENROUTER_KEY_FILE.exists():
            k = OPENROUTER_KEY_FILE.read_text().strip()
            if k.startswith("sk-or-"):
                return k
    except Exception:
        pass
    return ""


def _fingerprint(key: str) -> str:
    return hashlib.sha256(key.encode()).hexdigest()[:16] if key else ""


def active() -> bool:
    """OpenRouter is the provider in use and there is a key to ask with."""
    if not enabled():
        return False
    try:
        from .. import state
        from ..constants import PROVIDER_OPENROUTER

        if state.provider != PROVIDER_OPENROUTER:
            return False
    except Exception:
        return False
    return bool(api_key())


# ─── parsing ─────────────────────────────────────────────────────────────

def _num(v) -> float | None:
    if isinstance(v, bool) or v is None:
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _next_reset(reset: str | None, now: _dt.datetime | None = None) -> str | None:
    """Where OpenRouter doesn't say: limits reset at 00:00 UTC (daily / 1st of the month)."""
    now = now or _dt.datetime.now(_dt.timezone.utc)
    if reset == "daily":
        at = (now + _dt.timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
    elif reset == "monthly":
        y, m = (now.year + 1, 1) if now.month == 12 else (now.year, now.month + 1)
        at = _dt.datetime(y, m, 1, tzinfo=_dt.timezone.utc)
    else:
        return None
    return at.strftime("%Y-%m-%dT%H:%M:%S.000Z")


def parse(key_data: dict, credits_data: dict | None = None) -> dict:
    """``/key`` (+ ``/credits``) → the plain dict every UI reads (JSON-safe)."""
    d = key_data.get("data", key_data) if isinstance(key_data, dict) else {}
    budget = d.get("effective_budget") if isinstance(d.get("effective_budget"), dict) else {}

    limit = _num(budget.get("limit_usd")) if budget else None
    if limit is None:
        limit = _num(d.get("limit"))
    reset = (budget.get("reset_interval") if budget else None) or d.get("limit_reset") or None
    reset = reset if reset in _PERIOD else None
    remaining = _num(budget.get("remaining_usd")) if budget else None
    if remaining is None:
        remaining = _num(d.get("limit_remaining"))
    spent = _num(budget.get("spend_usd")) if budget else None
    if spent is None and limit is not None and remaining is not None:
        spent = max(0.0, limit - remaining)
    if spent is None and limit is not None:
        spent = _num(d.get(_USAGE_FIELD.get(reset, "usage")))
    if remaining is None and limit is not None and spent is not None:
        remaining = max(0.0, limit - spent)

    usage = {
        "day": _num(d.get("usage_daily")),
        "week": _num(d.get("usage_weekly")),
        "month": _num(d.get("usage_monthly")),
        "total": _num(d.get("usage")),
    }
    byok = {
        "day": _num(d.get("byok_usage_daily")),
        "week": _num(d.get("byok_usage_weekly")),
        "month": _num(d.get("byok_usage_monthly")),
        "total": _num(d.get("byok_usage")),
    }
    free = d.get("free_model_daily_requests")
    free_requests = None
    if isinstance(free, dict) and _num(free.get("limit")):
        used = int(_num(free.get("used")) or 0)
        cap = int(_num(free.get("limit")) or 0)
        left = _num(free.get("remaining"))
        free_requests = {"used": used, "limit": cap, "remaining": int(left) if left is not None else max(0, cap - used)}

    credits = None
    c = (credits_data or {}).get("data", credits_data) if isinstance(credits_data, dict) else None
    if isinstance(c, dict):
        total, used = _num(c.get("total_credits")), _num(c.get("total_usage"))
        if total is not None and used is not None:
            credits = {"total": total, "used": used, "balance": total - used}

    return {
        "label": str(d.get("label") or ""),
        "limit": limit,
        "spent": spent,
        "remaining": remaining,
        # share of the limit already spent, 0–1
        "fraction": (min(1.0, max(0.0, spent / limit)) if limit and spent is not None else None),
        "reset": reset,
        "period": _PERIOD.get(reset or "", "in total"),
        "resets_at": (budget.get("resets_at") if budget else None) or _next_reset(reset),
        "usage": usage,
        "byok": byok if any(v for v in byok.values()) else None,
        "free_tier": bool(d.get("is_free_tier")),
        "free_requests": free_requests,
        "credits": credits,
        "expires_at": d.get("expires_at") or None,
    }


# ─── fetching ────────────────────────────────────────────────────────────

class UsageError(Exception):
    def __init__(self, message: str, refused: bool = False):
        super().__init__(message)
        self.refused = refused


def _get(url: str, key: str) -> dict:
    """One authorised GET → JSON (tests swap this out)."""
    req = urllib.request.Request(url, headers={**REQUEST_HEADERS, "Authorization": f"Bearer {key}"})
    with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
        return json.load(resp)


def fetch(key: str) -> dict:
    """Ask OpenRouter now; ``UsageError`` with a plain reason when it can't say."""
    try:
        key_data = _get(KEY_URL, key)
    except urllib.error.HTTPError as e:
        if e.code in (401, 403):
            raise UsageError("OpenRouter refused this key", refused=True) from e
        raise UsageError(f"OpenRouter answered {e.code}") from e
    except (urllib.error.URLError, TimeoutError, OSError) as e:
        raise UsageError("Couldn't reach OpenRouter") from e
    except ValueError as e:
        raise UsageError("OpenRouter sent something unreadable") from e
    try:
        credits_data = _get(CREDITS_URL, key)
    except Exception:
        credits_data = None  # the balance is a bonus; some keys may not read it
    return parse(key_data, credits_data)


def refresh(*, wait: bool = True) -> dict | None:
    """Fetch now (``wait``) or in the background; returns ``public(kick=False)``."""
    global _inflight
    key = api_key()
    if not key or not enabled():
        return None
    with _lock:
        if _inflight and not wait:
            return _snapshot_locked()
        _inflight = True
    if wait:
        _run(key)
    else:
        threading.Thread(target=_run, args=(key,), name="openrouter-usage", daemon=True).start()
    return public(kick=False, any_provider=True)


def _run(key: str) -> None:
    global _data, _key_id, _fetched_at, _due_at, _error, _inflight
    fp = _fingerprint(key)
    try:
        data = fetch(key)
    except UsageError as e:
        with _lock:
            if _key_id != fp:
                _data, _key_id = None, fp
            _error = str(e)
            _due_at = time.time() + (RETRY_REFUSED if e.refused else RETRY_NETWORK)
            _inflight = False
        return
    except Exception:
        with _lock:
            _error = "Couldn't read OpenRouter usage"
            _due_at = time.time() + RETRY_NETWORK
            _inflight = False
        return
    with _lock:
        _data, _key_id, _error = data, fp, None
        _fetched_at = time.time()
        _due_at = _fetched_at + TTL
        _inflight = False


def invalidate(after: float = AFTER_TURN) -> None:
    """Spend just changed (a turn ended): refetch soon, not at the next TTL."""
    global _due_at
    with _lock:
        if _data is not None:
            _due_at = min(_due_at, time.time() + after)


def reset() -> None:
    """Forget everything (tests)."""
    global _data, _key_id, _fetched_at, _due_at, _error, _inflight
    with _lock:
        _data, _key_id, _fetched_at, _due_at, _error, _inflight = None, "", 0.0, 0.0, None, False


def _snapshot_locked() -> dict:
    out = dict(_data) if _data else {}
    out.update({
        "status": "ok" if _data else ("error" if _error else "loading"),
        "error": _error,
        "loading": _inflight,
        "fetched_at": _fetched_at or None,
        "activity_url": ACTIVITY_URL,
    })
    return out


def public(*, kick: bool = True, any_provider: bool = False) -> dict | None:
    """What the UIs show, or ``None`` while OpenRouter isn't the provider
    (``any_provider``: whenever there is a key — ``/usage`` asks that way).

    Cheap — no network; with ``kick`` a stale answer starts one refresh in
    the background.
    """
    global _data, _due_at, _error, _inflight
    if not (active() if not any_provider else (enabled() and api_key())):
        return None
    key = api_key()
    fp = _fingerprint(key)
    start = False
    with _lock:
        if _key_id and _key_id != fp:  # another key now: nothing of the old one applies
            _data, _error, _due_at = None, None, 0.0
        if kick and not _inflight and time.time() >= _due_at:
            _inflight = start = True
        snap = _snapshot_locked()
    if start:
        threading.Thread(target=_run, args=(key,), name="openrouter-usage", daemon=True).start()
    return snap


# ─── formatting (terminal; the web formats its own) ──────────────────────

def money(v: float | None) -> str:
    """``20`` → ``$20``, ``17.897`` → ``$17.90``, ``0.004`` → ``<$0.01``."""
    if v is None:
        return "—"
    if v == 0:
        return "$0"
    if 0 < abs(v) < 0.01:
        return "<$0.01"
    if abs(v - round(v)) < 0.005:
        return f"${v:,.0f}"
    return f"${v:,.2f}"


def _parse_iso(s: str | None) -> _dt.datetime | None:
    if not s:
        return None
    try:
        return _dt.datetime.fromisoformat(str(s).replace("Z", "+00:00"))
    except ValueError:
        return None


def when(iso: str | None, now: _dt.datetime | None = None) -> str:
    """``2026-11-01T00:00Z`` → ``Nov 1 (in 3 days)``, in local time."""
    at = _parse_iso(iso)
    if at is None:
        return ""
    now = now or _dt.datetime.now(_dt.timezone.utc)
    local = at.astimezone()
    day = f"{local.strftime('%b')} {local.day}"
    secs = (at - now).total_seconds()
    if secs <= 0:
        return day
    if secs < 3600:
        rel = f"in {max(1, int(secs // 60))}m"
    elif secs < 86400:
        rel = f"in {int(secs // 3600)}h"
    else:
        days = round(secs / 86400)
        rel = "tomorrow" if days == 1 else f"in {days} days"
    return f"{day} ({rel})"


def ago(ts: float | None) -> str:
    if not ts:
        return ""
    secs = max(0, int(time.time() - ts))
    if secs < 10:
        return "just now"
    if secs < 60:
        return f"{secs}s ago"
    if secs < 3600:
        return f"{secs // 60}m ago"
    return f"{secs // 3600}h ago"


def left_level(info: dict) -> str:
    """``ok`` · ``warn`` · ``low`` for what's left (key limit, else account balance)."""
    limit, remaining = info.get("limit"), info.get("remaining")
    if limit and remaining is not None:
        share = remaining / limit
        return "ok" if share >= 0.4 else "warn" if share >= 0.15 else "low"
    credits = info.get("credits") or {}
    bal = credits.get("balance")
    if bal is not None:
        return "ok" if bal >= 5 else "warn" if bal >= 1 else "low"
    return "ok"


def headline(info: dict) -> str | None:
    """The one number worth a glance: ``$17.90 left`` (key) or ``$97.13 credit`` (account)."""
    if info.get("limit") and info.get("remaining") is not None:
        return f"{money(info['remaining'])} left"
    credits = info.get("credits")
    if credits:
        return f"{money(credits['balance'])} credit"
    return None
