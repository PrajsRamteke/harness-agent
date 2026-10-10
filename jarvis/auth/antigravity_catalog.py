"""Live discovery of the models a Google account can use through Antigravity.

``POST {cloudcode}/v1internal:fetchAvailableModels`` lists them per account
(with quota info) — the same call the Antigravity app makes. The list is
cached on disk like the Codex one; seeds are used only until the first fetch
and never appended to a live list. A model the backend refuses is remembered
for a week and sorted last.

Also cached here: the current Antigravity version, read from the app's own
updater — the backend refuses clients that report an outdated one.
"""
from __future__ import annotations

import re
import time
from dataclasses import dataclass, replace

import httpx

from . import catalog_cache
from ..constants.antigravity_oauth import (
    ANTIGRAVITY_ENDPOINT_PROD,
    ANTIGRAVITY_ENDPOINTS,
    ANTIGRAVITY_VERSION_FALLBACK,
    ANTIGRAVITY_VERSION_URL,
)

CACHE_NAME = "antigravity_models"
VERSION_CACHE = "antigravity_version"
REFUSED_CACHE = "antigravity_refused"
REFUSED_TTL = 7 * 24 * 3600
DEFAULT_TIMEOUT = 8.0

# Gemini 3 thinking levels (``thinkingConfig.thinkingLevel``) and Claude
# thinking budgets (``thinking_budget``) per Jarvis effort.
GEMINI_FLASH_LEVELS = ("minimal", "low", "medium", "high")
CLAUDE_BUDGETS = {"low": 8192, "medium": 16384, "high": 32768}

# Offline stand-ins, shown only until the account's own list has been read.
SEEDS: tuple[tuple[str, str], ...] = (
    ("gemini-3.1-pro-high", "Gemini 3.1 Pro (High)"),
    ("gemini-3.1-pro-low", "Gemini 3.1 Pro (Low)"),
    ("claude-opus-4-6-thinking", "Claude Opus 4.6 (Thinking)"),
    ("claude-sonnet-4-6", "Claude Sonnet 4.6"),
    ("gemini-3-flash", "Gemini 3 Flash"),
)

# Internal / non-chat rows fetchAvailableModels also returns.
_SKIP = re.compile(r"^(chat_|tab_|rev\d|autocomplete|embed)|image|imagen|embedding|tts|audio|lite-preview", re.I)
_ID_OK = re.compile(r"^[a-z0-9][a-z0-9.\-]*$")


@dataclass(frozen=True)
class AntigravityModel:
    id: str
    label: str
    supports_images: bool = True
    thinking: bool = False
    max_output: int = 0
    context: int = 0
    remaining: float | None = None  # quota left, 0..1 (None = not reported)
    reset_time: str = ""
    refused: bool = False

    @property
    def usable(self) -> bool:
        return not self.refused

    @property
    def is_claude(self) -> bool:
        return "claude" in self.id

    @property
    def is_gemini(self) -> bool:
        return self.id.startswith("gemini")


# ── what each model takes for thinking (by id — the API doesn't say) ─────────


def gemini3_fixed_level(model: str) -> str:
    """``gemini-3-pro-high`` → ``high``: the level is part of the id."""
    m = re.match(r"^gemini-3(?:\.\d+)?-pro.*-(low|medium|high)$", model)
    return m.group(1) if m else ""


def think_facts(model: str) -> tuple[str, tuple[str, ...], bool]:
    """``(kind, levels, can_off)``: kind is ``gemini3`` | ``claude`` | ``budget`` | ``none``."""
    mid = (model or "").lower()
    if mid.startswith("gemini-3"):
        if gemini3_fixed_level(mid):
            return "gemini3", (), False  # always thinks, at the level in its name
        if "flash" in mid:
            return "gemini3", GEMINI_FLASH_LEVELS, False
        return "gemini3", ("low", "high"), False
    if "claude" in mid:
        if "thinking" in mid:
            return "claude", tuple(CLAUDE_BUDGETS), True
        return "none", (), True
    if mid.startswith("gemini-2.5"):
        return "budget", ("low", "medium", "high"), "pro" not in mid
    return "none", (), True


# ── fetching ──────────────────────────────────────────────────────────────────


def _rank(mid: str) -> tuple:
    """Best first: Gemini Pro (newest), Claude Opus, Claude Sonnet, Gemini Flash, rest."""
    ver = re.search(r"(\d+(?:\.\d+)?)", mid)
    v = -float(ver.group(1)) if ver else 0.0
    if mid.startswith("gemini") and "pro" in mid:
        fam = 0
    elif "opus" in mid:
        fam = 1
    elif "sonnet" in mid:
        fam = 2
    elif mid.startswith("gemini"):
        fam = 3
    else:
        fam = 4
    tier = 0 if mid.endswith("-high") else 1
    return fam, v, tier, mid


def _int(v) -> int:
    try:
        return int(v or 0)
    except (TypeError, ValueError):
        return 0


def _to_model(mid: str, entry: dict) -> AntigravityModel | None:
    if not isinstance(entry, dict) or not _ID_OK.match(mid) or _SKIP.search(mid):
        return None
    if entry.get("isInternal") or entry.get("disabled"):
        return None
    label = str(entry.get("displayName") or entry.get("modelName") or mid)
    quota = entry.get("quotaInfo") if isinstance(entry.get("quotaInfo"), dict) else {}
    remaining = quota.get("remainingFraction")
    images = entry.get("supportsImages")
    kind, _levels, _off = think_facts(mid)
    return AntigravityModel(
        id=mid,
        label=label,
        supports_images=True if images is None else bool(images),
        thinking=bool(entry.get("supportsThinking")) or kind != "none",
        max_output=_int(entry.get("maxOutputTokens")),
        context=_int(entry.get("maxTokens") or entry.get("inputTokenLimit")),
        remaining=float(remaining) if isinstance(remaining, (int, float)) else None,
        reset_time=str(quota.get("resetTime") or ""),
    )


def parse_models(payload: object) -> list[AntigravityModel]:
    models = payload.get("models") if isinstance(payload, dict) else None
    if isinstance(models, list):  # tolerate a list of {name/id, …}
        models = {str(m.get("name") or m.get("id") or ""): m for m in models if isinstance(m, dict)}
    if not isinstance(models, dict):
        return []
    out = [m for m in (_to_model(str(k), v) for k, v in models.items()) if m]
    out.sort(key=lambda m: _rank(m.id))
    return out


def fetch_models(access_token: str, project_id: str,
                 timeout: float = DEFAULT_TIMEOUT) -> list[AntigravityModel] | None:
    """The account's line-up, best first. None on any failure."""
    if not access_token:
        return None
    from .antigravity_client import request_headers

    body = {"project": project_id} if project_id else {}
    for base in dict.fromkeys((ANTIGRAVITY_ENDPOINT_PROD, *ANTIGRAVITY_ENDPOINTS)):
        try:
            resp = httpx.post(f"{base}/v1internal:fetchAvailableModels", json=body,
                              headers=request_headers(access_token), timeout=timeout)
        except httpx.HTTPError:
            continue
        if resp.status_code != 200:
            continue
        try:
            out = parse_models(resp.json())
        except ValueError:
            continue
        if out:
            return out
    return None


def _encode(models: list[AntigravityModel]) -> list[dict]:
    return [m.__dict__ | {"refused": False} for m in models]


def _decode(payload) -> list[AntigravityModel]:
    if not isinstance(payload, list):
        return []
    out = []
    fields = set(AntigravityModel.__dataclass_fields__)
    for row in payload:
        if isinstance(row, dict) and row.get("id"):
            try:
                out.append(AntigravityModel(**{k: v for k, v in row.items() if k in fields}))
            except TypeError:
                continue
    return out


# ── refusals ──────────────────────────────────────────────────────────────────


def _refused_raw() -> dict:
    payload, _fresh = catalog_cache.read(REFUSED_CACHE)
    return payload if isinstance(payload, dict) else {}


def refused_ids() -> set[str]:
    now = time.time()
    return {mid for mid, at in _refused_raw().items()
            if isinstance(at, (int, float)) and now - at < REFUSED_TTL}


def mark_unavailable(model_id: str) -> None:
    if not model_id:
        return
    entries = _refused_raw()
    entries[model_id] = time.time()
    catalog_cache.write(REFUSED_CACHE, entries)


def clear_unavailable() -> None:
    catalog_cache.write(REFUSED_CACHE, {})


# ── cache ─────────────────────────────────────────────────────────────────────


def cached_models() -> list[AntigravityModel]:
    payload, _fresh = catalog_cache.read(CACHE_NAME)
    return _decode(payload)


def _signed_in() -> bool:
    from .antigravity_oauth import load_antigravity_tokens

    return load_antigravity_tokens() is not None


def cache_is_fresh() -> bool:
    if not _signed_in():
        return True
    _payload, fresh = catalog_cache.read(CACHE_NAME)
    return fresh


def refresh_models(timeout: float = DEFAULT_TIMEOUT, *,
                   retry_refused: bool = False) -> list[AntigravityModel] | None:
    """Fetch and persist (worker thread — may refresh the token first)."""
    if retry_refused:
        clear_unavailable()
    from .antigravity_oauth import get_fresh_antigravity_token

    tokens = get_fresh_antigravity_token()
    if not tokens:
        return None  # signed out: no network at all
    refresh_version()
    models = fetch_models(str(tokens.get("access_token") or ""),
                          str(tokens.get("project_id") or ""), timeout)
    if models:
        catalog_cache.write(CACHE_NAME, _encode(models))
    return models


def seed_models() -> list[AntigravityModel]:
    return [AntigravityModel(id=mid, label=label, thinking=think_facts(mid)[0] != "none")
            for mid, label in SEEDS]


def models_for_display(live: bool = False) -> list[AntigravityModel]:
    """Picker rows: usable first, refused ones labelled last. Cache only
    unless ``live``; the seeds while nothing has been fetched yet."""
    models = refresh_models() if live else None
    if not models:
        models = cached_models() or seed_models()
    refused = refused_ids()
    out = [replace(m, refused=True, label=f"{m.label}, unavailable") if m.id in refused else m
           for m in models]
    out.sort(key=lambda m: (m.refused, _rank(m.id)))
    return out


def get_model(model_id: str) -> AntigravityModel | None:
    return next((m for m in (cached_models() or seed_models()) if m.id == model_id), None)


def default_model() -> str:
    usable = [m for m in models_for_display() if m.usable]
    return usable[0].id if usable else SEEDS[0][0]


# ── client version ────────────────────────────────────────────────────────────

_VERSION_RE = re.compile(r"\d+\.\d+\.\d+")


def version() -> str:
    payload, _fresh = catalog_cache.read(VERSION_CACHE)
    if isinstance(payload, str) and _VERSION_RE.fullmatch(payload):
        return payload
    return ANTIGRAVITY_VERSION_FALLBACK


def refresh_version(timeout: float = 5.0) -> str:
    """Read the current release from Antigravity's updater (once a day)."""
    payload, fresh = catalog_cache.read(VERSION_CACHE)
    if fresh and isinstance(payload, str):
        return payload
    try:
        resp = httpx.get(ANTIGRAVITY_VERSION_URL, timeout=timeout)
        m = _VERSION_RE.search(resp.text[:2000]) if resp.status_code == 200 else None
    except httpx.HTTPError:
        m = None
    if m:
        catalog_cache.write(VERSION_CACHE, m.group(0))
        return m.group(0)
    return version()


def quota_note(m: AntigravityModel) -> str:
    """``42% left`` for a picker row ('' when unknown)."""
    if m.remaining is None:
        return ""
    return f"{round(m.remaining * 100)}% left"


__all__ = [
    "AntigravityModel", "cached_models", "models_for_display", "refresh_models", "get_model",
    "default_model", "refused_ids", "mark_unavailable", "think_facts", "version",
]
