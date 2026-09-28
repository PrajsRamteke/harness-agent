"""Live discovery of the models this ChatGPT account can use through Codex.

The Codex backend publishes its line-up at ``GET {CODEX_BASE_URL}/models`` —
the same list the Codex CLI reads. Unlike the free-tier catalogs it needs the
OAuth access token (the list depends on the plan), and it filters by
``client_version``: a model that needs a newer client is left out.

Hard-coding these ids rots quickly: gpt-5.4 / gpt-5.4-mini started answering
400 "not supported when using Codex with a ChatGPT account", and gpt-5.5 — still
listed, as "Legacy" — answers 404 ``model_not_found``. So the picker reads the
list, and a model that refuses a request is remembered and sorted last (the
same treatment as OpenRouter's restricted models) instead of staying the
default.

Only the user's own token is sent, and only to the backend it was issued for.
"""
from __future__ import annotations

from dataclasses import dataclass, replace
import json
import time
import urllib.request

from . import catalog_cache
from ..constants.codex_oauth import CODEX_OAUTH_ORIGINATOR
from ..constants.providers import CODEX_BASE_URL

MODELS_URL = f"{CODEX_BASE_URL}/models"
CACHE_NAME = "codex_models"

# The backend hides models whose ``minimal_client_version`` is above this. Any
# recent version returns the full line-up; too old a one returns only legacy
# models (0.130.0 → just gpt-5.5, which then 404s).
CLIENT_VERSION = "0.200.0"

DEFAULT_TIMEOUT = 6.0


@dataclass(frozen=True)
class CodexModel:
    """One Codex model, as the picker and pricing tables need it."""
    id: str
    label: str
    priority: int = 0
    supports_images: bool = False
    refused: bool = False

    @property
    def usable(self) -> bool:
        return not self.refused


def _get_json(url: str, access_token: str, timeout: float):
    req = urllib.request.Request(url, headers={
        "Accept": "application/json",
        "Authorization": f"Bearer {access_token}",
        "originator": CODEX_OAUTH_ORIGINATOR,
        "User-Agent": f"{CODEX_OAUTH_ORIGINATOR}/{CLIENT_VERSION}",
    })
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.load(resp)


def _label(entry: dict) -> str:
    name = entry.get("display_name") or entry.get("slug") or ""
    desc = (entry.get("description") or "").strip().rstrip(".")
    return f"{name} — {desc}" if desc else name


def _to_model(entry: dict) -> CodexModel | None:
    mid = entry.get("slug")
    # "hide" rows are internal (auto-review, reserve) — the Codex CLI's own
    # picker doesn't offer them either.
    if not mid or entry.get("visibility") != "list":
        return None
    try:
        priority = int(entry.get("priority") or 0)
    except (TypeError, ValueError):
        priority = 0
    return CodexModel(
        id=mid,
        label=_label(entry),
        priority=priority,
        supports_images="image" in (entry.get("input_modalities") or []),
    )


def fetch_models(access_token: str, timeout: float = DEFAULT_TIMEOUT) -> list[CodexModel] | None:
    """Fetch the account's Codex line-up, best first. None on any failure."""
    if not access_token:
        return None
    try:
        payload = _get_json(f"{MODELS_URL}?client_version={CLIENT_VERSION}", access_token, timeout)
    except Exception:
        return None
    entries = payload.get("models") if isinstance(payload, dict) else None
    if not isinstance(entries, list):
        return None
    out = [m for m in (_to_model(e) for e in entries if isinstance(e, dict)) if m]
    # Lower priority = recommended first, which is how the backend ranks them.
    out.sort(key=lambda m: (m.priority, m.id))
    return out or None


def _decode(payload) -> list[CodexModel]:
    if not isinstance(payload, list):
        return []
    out: list[CodexModel] = []
    for row in payload:
        if not isinstance(row, dict) or not row.get("id"):
            continue
        out.append(CodexModel(
            id=row["id"],
            label=row.get("label") or row["id"],
            priority=int(row.get("priority") or 0),
            supports_images=bool(row.get("supports_images")),
        ))
    return out


def _encode(models: list[CodexModel]) -> list[dict]:
    return [
        {
            "id": m.id,
            "label": m.label,
            "priority": m.priority,
            "supports_images": m.supports_images,
        }
        for m in models
    ]


# ── models the backend lists but refuses ──────────────────────────────────────
REFUSED_CACHE = "codex_refused"
REFUSED_TTL = 7 * 24 * 3600


def _refused_raw() -> dict:
    payload, _fresh = catalog_cache.read(REFUSED_CACHE)
    return payload if isinstance(payload, dict) else {}


def refused_ids() -> set[str]:
    """Models Codex refused recently. Entries age out after a week."""
    now = time.time()
    return {
        mid for mid, at in _refused_raw().items()
        if isinstance(at, (int, float)) and now - at < REFUSED_TTL
    }


def mark_unavailable(model_id: str) -> None:
    """Remember that ``model_id`` refused this account."""
    if not model_id:
        return
    entries = _refused_raw()
    entries[model_id] = time.time()
    catalog_cache.write(REFUSED_CACHE, entries)


def clear_unavailable() -> None:
    """Forget every refusal — used by an explicit /model refresh."""
    catalog_cache.write(REFUSED_CACHE, {})


def cached_models() -> list[CodexModel]:
    """Models from the on-disk cache. Never touches the network."""
    payload, _fresh = catalog_cache.read(CACHE_NAME)
    return _decode(payload)


def _signed_in() -> bool:
    from .codex_oauth_tokens import load_codex_oauth_tokens

    return load_codex_oauth_tokens() is not None


def cache_is_fresh() -> bool:
    """True when no refresh is needed — including when nobody is signed in."""
    if not _signed_in():
        return True
    _payload, fresh = catalog_cache.read(CACHE_NAME)
    return fresh


def refresh_models(
    timeout: float = DEFAULT_TIMEOUT, *, retry_refused: bool = False
) -> list[CodexModel] | None:
    """Fetch and persist. None when not signed in or the fetch failed.

    May refresh the OAuth token first, so call it off the UI thread.
    """
    if retry_refused:
        clear_unavailable()
    from .codex_oauth_tokens import get_fresh_codex_oauth_token

    tokens = get_fresh_codex_oauth_token()
    if not tokens:
        return None
    models = fetch_models(tokens.get("access_token") or "", timeout)
    if models:
        catalog_cache.write(CACHE_NAME, _encode(models))
    return models


def models_for_display(live: bool = False) -> list[CodexModel]:
    """Codex models for the picker: usable first, refused ones labelled last.

    ``live=False`` reads the cache only, so UI threads never block.
    """
    models = refresh_models() if live else None
    if not models:
        models = cached_models()
    refused = refused_ids()
    out = [
        replace(m, refused=True, label=f"{m.label}, unavailable") if m.id in refused else m
        for m in models
    ]
    out.sort(key=lambda m: (m.refused, m.priority, m.id))
    return out
