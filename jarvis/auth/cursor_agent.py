"""Cursor as a provider — the user's Cursor subscription through the Cursor SDK.

UI-free. The provider (``cursor``) runs Cursor's agent loop with the official
``cursor-sdk`` Python package; every model request is made by Cursor's own
bridge with the user's API key (Cursor Dashboard → API Keys), never by Jarvis
(see ``cursor_agent_client.py``).

* The key comes from ``CURSOR_API_KEY`` or ``~/.config/harness-agent/cursor_key``
  (``constants/api_keys.py`` — the same key modal / web "Add a key" the other
  providers use). It is only ever handed to the SDK; it is never logged,
  cached in ``model_catalog/`` or shown (``Status`` has no key field).
* ``cursor-sdk`` is optional (it bundles a ~165 MB bridge): ``SDK_INSTALL_CMD``
  is what the UI shows when it's missing.

Status is cached (``model_catalog/cursor_status.json``) so startup never waits
on the bridge; ``refresh()`` asks Cursor again (worker threads only).
"""
from __future__ import annotations

import hashlib
import importlib
import os
import re
import shlex
import sys
import threading
import time
from dataclasses import dataclass, field
from typing import Any

from . import catalog_cache
from ..constants.paths import CURSOR_KEY_FILE

KEY_ENV = "CURSOR_API_KEY"
STATUS_CACHE = "cursor_status"
MODELS_CACHE = "cursor_models"
SDK_PACKAGE = "cursor-sdk"
# Spelled with *this* interpreter: a bare `pip` is whichever one is first on
# PATH, so the install used to land where Jarvis never looks.
SDK_INSTALL_CMD = f"{shlex.quote(sys.executable or 'python3')} -m pip install {SDK_PACKAGE}"
KEY_URL = "https://cursor.com/dashboard?tab=integrations"
DOCS_URL = "https://cursor.com/docs/sdk/python"
# Starting the bridge, `me()` and `models.list()` take a few seconds cold.
PROBE_TIMEOUT = 60.0

_lock = threading.Lock()
_memo: dict[str, Any] = {}


# ─── The key ──────────────────────────────────────────────────────────────


def api_key() -> str:
    """The user's Cursor API key: the environment, then the key file ('' when
    neither has one)."""
    env = os.environ.get(KEY_ENV, "").strip()
    if env:
        return env
    try:
        return CURSOR_KEY_FILE.read_text().strip()
    except OSError:
        return ""


def key_source() -> str:
    """``env`` | ``file`` | ``''`` — where :func:`api_key` finds the key."""
    if os.environ.get(KEY_ENV, "").strip():
        return "env"
    try:
        return "file" if CURSOR_KEY_FILE.read_text().strip() else ""
    except OSError:
        return ""


def has_key() -> bool:
    return bool(key_source())


def _fingerprint(key: str) -> str:
    """12 hex chars of the key's SHA-256: enough to tell "the key Cursor judged"
    from "a different key" in the cached status, useless for recovering it."""
    return hashlib.sha256(key.encode("utf-8")).hexdigest()[:12] if key else ""


# ─── The SDK ──────────────────────────────────────────────────────────────


def sdk_version() -> str | None:
    """Installed cursor-sdk version (without importing it)."""
    try:
        from importlib.metadata import version

        return version(SDK_PACKAGE)
    except Exception:
        return None


# ─── Status ───────────────────────────────────────────────────────────────


@dataclass
class Status:
    sdk_version: str = ""
    has_key: bool = False
    key_source: str = ""        # env | file
    rejected: bool = False      # Cursor said the key isn't valid
    key_fp: str = ""            # which key that verdict (and the account below) is about
    email: str = ""
    name: str = ""              # the API key's own name in the dashboard
    checked_at: float = 0.0
    error: str = ""
    extra: dict = field(default_factory=dict)

    @property
    def ready(self) -> bool:
        return bool(self.sdk_version) and self.has_key and not self.rejected

    def problem(self) -> tuple[str, str, str]:
        """``(code, message, fix)`` for the first thing that's missing, or
        ``("", "", "")`` when ready. ``fix`` is a command or a place to go."""
        if not self.sdk_version:
            return ("no_sdk", "The Cursor SDK (Python package) isn't installed.", SDK_INSTALL_CMD)
        if not self.has_key:
            return ("no_key", f"No Cursor API key. Create one in the Cursor dashboard, then set {KEY_ENV} "
                    f"or add it in /key.", KEY_URL)
        if self.rejected:
            return ("bad_key", "Cursor didn't accept this API key. Check that you copied all of it, "
                    "or create a new one.", KEY_URL)
        return ("", "", "")

    def account_line(self) -> str:
        """``you@x.com`` / ``key “jarvis”`` — what Cursor says the key belongs to."""
        if not self.has_key:
            return ""
        bits = [b for b in (self.email, f"key “{self.name}”" if self.name and not self.email else "") if b]
        return " · ".join(bits) or "Cursor API key"

    def public(self) -> dict[str, Any]:
        code, msg, fix = self.problem()
        return {
            "sdk_version": self.sdk_version,
            "has_key": self.has_key,
            "key_source": self.key_source,
            "account": self.account_line(),
            "ready": self.ready,
            "problem": code,
            "message": msg,
            "fix": fix,
            "sdk_install_cmd": SDK_INSTALL_CMD,
            "key_url": KEY_URL,
            "docs": DOCS_URL,
            "checked_at": self.checked_at,
            "limits": limits_public(),
        }

    def to_json(self) -> dict[str, Any]:
        return {k: getattr(self, k) for k in ("rejected", "key_fp", "email", "name", "checked_at", "error")}

    @classmethod
    def from_json(cls, d: dict) -> "Status":
        s = cls()
        for k in s.to_json():
            if k in d:
                setattr(s, k, d[k])
        return s


def _live(s: Status) -> Status:
    """The facts that can change under a cached status: SDK and key."""
    s.sdk_version = sdk_version() or ""
    s.key_source = key_source()
    s.has_key = bool(s.key_source)
    if s.key_fp and s.key_fp != _fingerprint(api_key()):
        # A different key since Cursor last judged one: that verdict is stale.
        s.rejected, s.key_fp, s.email, s.name, s.error, s.checked_at = False, "", "", "", "", 0.0
    return s


def status() -> Status:
    """The last known status — memory, then the disk cache; never talks to
    Cursor. SDK and key are looked at fresh (cheap file checks)."""
    with _lock:
        s = _memo.get("status")
    if s is None:
        payload, _fresh = catalog_cache.read(STATUS_CACHE)
        s = Status.from_json(payload) if isinstance(payload, dict) else Status()
        with _lock:
            _memo["status"] = s
    return _live(s)


def reset() -> None:
    """Forget the in-memory status and models (tests, a key that changed)."""
    with _lock:
        _memo.clear()


def is_ready() -> bool:
    """Cached: SDK installed, a key present, and Cursor hasn't refused it."""
    try:
        return status().ready
    except Exception:
        return False


def is_usable() -> bool:
    return is_ready()


def status_is_stale(max_age: float = 600.0) -> bool:
    return time.time() - (status().checked_at or 0) > max_age


# ─── Talking to Cursor (worker threads only) ──────────────────────────────


def _bridge_client(workspace: str | None = None):
    from cursor_sdk import CursorClient

    return CursorClient.launch_bridge(workspace=workspace or os.getcwd(), timeout=PROBE_TIMEOUT)


def _auth_failure(exc: BaseException) -> bool:
    """Did Cursor refuse the key (as opposed to the network being down)?"""
    # By class name (any SDK version, and the errors a bridge wraps), then by what Cursor says.
    if any(c.__name__ in ("AuthenticationError", "PermissionDeniedError") for c in type(exc).__mro__):
        return True
    low = str(exc).lower()
    return any(w in low for w in ("unauthenticated", "unauthorized", "invalid user api key", "invalid api key"))


def probe() -> Status:
    """Ask Cursor now: who is this key, and which models does it have. Starts
    the SDK's bridge once; no model request is made. Worker threads only."""
    importlib.invalidate_caches()  # an SDK installed while Jarvis runs
    s = _live(Status(checked_at=time.time()))
    if not s.ready:
        return s
    key = api_key()
    s.key_fp = _fingerprint(key)
    try:
        with _bridge_client() as client:
            try:
                me = client.me(api_key=key)
                s.email = str(getattr(me, "user_email", "") or "")
                s.name = str(getattr(me, "api_key_name", "") or "")
            except Exception as e:  # noqa: BLE001
                if _auth_failure(e):
                    s.rejected = True
                    s.error = str(e)[:300]
                    return s
                s.error = str(e)[:300]
            if not s.rejected:
                try:
                    store_models(client.models.list(api_key=key))
                except Exception as e:  # noqa: BLE001 — the old list stays
                    s.error = s.error or str(e)[:300]
    except Exception as e:  # noqa: BLE001 — no bridge / no network: status unknown
        if _auth_failure(e):
            s.rejected = True
        s.error = str(e)[:300]
    return s


def refresh() -> Status:
    """Probe and cache. Worker threads only."""
    s = probe()
    with _lock:
        _memo["status"] = s
    catalog_cache.write(STATUS_CACHE, s.to_json())
    return s


def verify_key(key: str) -> str:
    """``ok`` | ``rejected`` (Cursor refused it) | ``unknown`` (no SDK, no network).
    Asks Cursor who the key belongs to — starts the SDK's bridge, a few seconds.
    Worker threads only."""
    importlib.invalidate_caches()
    if not sdk_version():
        return "unknown"
    try:
        with _bridge_client() as client:
            client.me(api_key=key)
        return "ok"
    except Exception as e:  # noqa: BLE001
        return "rejected" if _auth_failure(e) else "unknown"


def refresh_in_background() -> None:
    threading.Thread(target=_quiet_refresh, daemon=True, name="jarvis-cursor-status").start()


def _quiet_refresh() -> None:
    try:
        refresh()
    except Exception:
        pass


def refresh_models() -> bool:
    """The model list for this key. Worker threads only; True when a list
    arrived."""
    if not is_ready():
        return False
    try:
        with _bridge_client() as client:
            return bool(store_models(client.models.list(api_key=api_key())))
    except Exception:
        return False


# ─── Models ───────────────────────────────────────────────────────────────

# Before Cursor has been asked: the ids its docs use everywhere.
SEED_MODELS: tuple[dict[str, Any], ...] = (
    {"id": "composer-2.5", "label": "Composer 2.5", "desc": "Cursor's own coding model"},
    {"id": "auto-smart", "label": "Auto (smart)", "desc": "Cursor Router picks the model"},
)
DEFAULT_MODEL = "composer-2.5"

# Cursor's per-model parameter that sets reasoning depth varies by model; match
# it by name. Cursor's spellings → Jarvis's effort names.
_EFFORT_PARAM_WORDS = ("effort", "reasoning", "thinking")
_EFFORT_ALIASES = {
    "extra-high": "xhigh", "extra_high": "xhigh", "extrahigh": "xhigh", "x-high": "xhigh",
    "minimum": "minimal", "min": "minimal", "med": "medium", "maximum": "max",
}
_EFFORT_OFF = ("none", "off", "disabled", "false")


def _effort_name(value: str) -> str:
    v = str(value or "").strip().lower()
    return _EFFORT_ALIASES.get(v, v)


def _get(obj: Any, *names: str, default: Any = "") -> Any:
    for n in names:
        v = obj.get(n) if isinstance(obj, dict) else getattr(obj, n, None)
        if v not in (None, ""):
            return v
    return default


def _clean_model(m: Any) -> dict[str, Any] | None:
    """One model row from an SDKModel (or the dict this module cached)."""
    mid = str(_get(m, "id")).strip()
    if not mid:
        return None
    params = []
    for p in _get(m, "parameters", "params", default=()) or ():  # SDK rows / the cached copy
        pid = str(_get(p, "id")).strip()
        # SDK rows carry value objects; the cached copy carries plain strings.
        values = [v if isinstance(v, str) else str(_get(v, "value"))
                  for v in (_get(p, "values", default=()) or ())]
        if pid:
            params.append({"id": pid, "label": str(_get(p, "label", "display_name", default=pid)),
                           "values": [v for v in values if v != ""]})
    efforts: dict[str, str] = {}
    effort_param = ""
    for p in params:
        if any(w in p["id"].lower() for w in _EFFORT_PARAM_WORDS):
            effort_param = p["id"]
            efforts = {_effort_name(v): v for v in p["values"] if _effort_name(v) not in _EFFORT_OFF}
            break
    return {
        "id": mid,
        "label": str(_get(m, "label", "display_name", default=mid)),
        "desc": str(_get(m, "desc", "description", default="")),
        "params": params,
        "effort_param": effort_param,
        "efforts": efforts,
        "thinking_off": any(_effort_name(v) in _EFFORT_OFF
                            for p in params if p["id"] == effort_param for v in p["values"]) if effort_param else None,
    }


def store_models(raw: list[Any]) -> list[dict[str, Any]]:
    rows = [r for r in (_clean_model(m) for m in raw or []) if r]
    seen: set[str] = set()
    rows = [r for r in rows if not (r["id"] in seen or seen.add(r["id"]))]
    if rows:
        catalog_cache.write(MODELS_CACHE, rows)
        with _lock:
            _memo.pop("models", None)
    return rows


def models() -> list[dict[str, Any]]:
    """Cursor's model list for this key (cached), else the seed."""
    with _lock:
        cached = _memo.get("models")
    if cached is not None:
        return cached
    payload, _fresh = catalog_cache.read(MODELS_CACHE)
    rows = [r for r in (_clean_model(m) for m in payload) if r] if isinstance(payload, list) else []
    rows = rows or [r for r in (_clean_model(m) for m in SEED_MODELS) if r]
    with _lock:
        _memo["models"] = rows
    return rows


def models_are_live() -> bool:
    payload, _fresh = catalog_cache.read(MODELS_CACHE)
    return isinstance(payload, list) and bool(payload)


def models_cache_is_fresh() -> bool:
    payload, fresh = catalog_cache.read(MODELS_CACHE)
    return bool(payload) and fresh


def get_model(model_id: str) -> dict[str, Any] | None:
    mid = (model_id or "").strip()
    for m in models():
        if m["id"] == mid:
            return m
    return None


def default_model() -> str:
    ids = [m["id"] for m in models()]
    if DEFAULT_MODEL in ids:
        return DEFAULT_MODEL
    return ids[0] if ids else DEFAULT_MODEL


# ─── Usage pools ──────────────────────────────────────────────────────────
#
# Cursor meters two pools: its own models (Grok, Composer — included in the
# plan) and third-party models (Claude, GPT, Gemini …, paid from credit). The
# "% used" Cursor's app shows comes from the dashboard behind the user's app
# login, which a Cursor API key can't read (and Jarvis never reads that login) —
# so Jarvis tells the pools apart and remembers what Cursor itself said when a
# pool ran out (a 429 and its reset date).

POOL_CURSOR = "cursor"
POOL_OTHER = "other"
POOL_LABELS = {POOL_CURSOR: "Cursor models", POOL_OTHER: "Other models"}
# Model ids the plan's own pool covers (Cursor's docs: Grok 4.x, Composer).
_CURSOR_POOL_PREFIXES = ("composer", "grok")
LIMITS_CACHE = "cursor_limits"
# A limit with no reset date in Cursor's message is believed this long.
LIMIT_FALLBACK_SECONDS = 6 * 3600.0
_RESET_DATE = re.compile(r"\b(\d{1,2})/(\d{1,2})/(\d{4})\b")


def pool_of(model: str) -> str:
    """``cursor`` for the plan's own models, ``other`` for third-party ones.
    ``auto*`` (Cursor's router) counts as Cursor's."""
    low = (model or "").strip().lower()
    if low.startswith(_CURSOR_POOL_PREFIXES) or low == "auto" or low.startswith("auto-"):
        return POOL_CURSOR
    return POOL_OTHER


def _reset_time(text: str, now: float) -> float:
    """When Cursor says the limit resets (``MM/DD/YYYY`` in its message), as a
    timestamp at the end of that day; else a fallback hour count from ``now``."""
    m = _RESET_DATE.search(text or "")
    if m:
        try:
            month, day, year = (int(g) for g in m.groups())
            return time.mktime((year, month, day, 23, 59, 59, 0, 0, -1))
        except (ValueError, OverflowError):
            pass
    return now + LIMIT_FALLBACK_SECONDS


def note_limit(model: str, message: str) -> None:
    """Cursor answered 429 for ``model``: remember its pool is out, and until when."""
    now = time.time()
    pool = pool_of(model)
    limits = _limits()
    limits[pool] = {"until": _reset_time(message, now), "seen": now, "model": model}
    catalog_cache.write(LIMITS_CACHE, limits)


def _limits() -> dict[str, dict[str, Any]]:
    payload, _fresh = catalog_cache.read(LIMITS_CACHE)
    now = time.time()
    rows = payload if isinstance(payload, dict) else {}
    return {p: r for p, r in rows.items()
            if p in POOL_LABELS and isinstance(r, dict) and float(r.get("until") or 0) > now}


def limit_for(model: str) -> dict[str, Any] | None:
    """The limit Cursor reported for ``model``'s pool, if still in force."""
    return _limits().get(pool_of(model))


def other_pool_label(model: str) -> str:
    """The label of the *other* pool, or '' when Cursor already said it is out too."""
    other = POOL_OTHER if pool_of(model) == POOL_CURSOR else POOL_CURSOR
    return "" if other in _limits() else POOL_LABELS[other]


def clear_limit(model: str) -> None:
    """A reply came back for ``model``: its pool isn't out (a top-up, a reset)."""
    limits = _limits()
    if limits.pop(pool_of(model), None) is not None:
        catalog_cache.write(LIMITS_CACHE, limits)


def clear_limits() -> None:
    catalog_cache.write(LIMITS_CACHE, {})


def _until_text(ts: float) -> str:
    t = time.localtime(ts)
    if ts - time.time() < 24 * 3600:
        return time.strftime("%H:%M", t)
    return time.strftime("%b %-d", t)


def limits_public() -> list[dict[str, Any]]:
    """Pools Cursor said are out, for the UIs: ``[{pool, label, until, until_text}]``."""
    return [{"pool": p, "label": POOL_LABELS[p], "until": r["until"], "until_text": _until_text(r["until"]),
             "model": r.get("model", "")} for p, r in _limits().items()]


def models_for_picker() -> list[tuple[str, str]]:
    """Picker rows, Cursor's own models first (they draw on the plan's included
    pool), each tagged with its pool and — when Cursor reported that pool out —
    the limit and when it resets."""
    limits = _limits()
    rows = []
    for m in models():
        pool = pool_of(m["id"])
        label = m["label"] if m["label"] != m["id"] else ""
        lim = limits.get(pool)
        tag = f"limit reached · resets {_until_text(lim['until'])}" if lim else POOL_LABELS[pool]
        desc = " · ".join(b for b in (label, m["desc"], tag) if b)
        rows.append((pool != POOL_CURSOR, m["id"], desc or m["id"]))
    rows.sort(key=lambda r: r[0])  # stable: Cursor's pool first, each in Cursor's own order
    return [(mid, desc) for _other, mid, desc in rows]


def display_name(model: str) -> str:
    m = get_model(model) or {}
    return m.get("label") or model or "Cursor"


# ─── Reasoning effort ─────────────────────────────────────────────────────


def effort_levels(model: str) -> tuple[str, ...]:
    """The effort names Cursor offers for ``model`` (lowest → highest as Cursor
    listed them); ``()`` when the model has no such parameter."""
    m = get_model(model) or {}
    return tuple((m.get("efforts") or {}).keys())


def model_selection(model: str, effort: str = "") -> Any:
    """What ``Agent.create(model=…)`` takes: the bare id, or a ``ModelSelection``
    carrying the reasoning-effort parameter when ``effort`` is one the model
    lists. Never invents a parameter Cursor didn't say the model has."""
    m = get_model(model) or {}
    raw = (m.get("efforts") or {}).get(_effort_name(effort)) if effort else None
    if not raw or not m.get("effort_param"):
        return model
    from cursor_sdk import ModelParameterValue, ModelSelection

    return ModelSelection(id=model, params=[ModelParameterValue(id=m["effort_param"], value=raw)])
