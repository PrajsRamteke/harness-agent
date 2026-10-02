"""The Harness Agent free list's stand-in while models.dev is off.

Normally the free tier lists OpenCode Zen's models at $0 from the models.dev
catalog plus Zen's served list (``constants.providers.harness_agent_models``),
and this module isn't fetched. With ``HARNESS_MODELS_DEV=0`` it reads the same
data from OpenCode's own copy of the catalog instead.

Two public GET endpoints, neither requiring auth or a special User-Agent:

  * ``https://models.opencode.ai/api.json`` — the catalog the real OpenCode CLI
    itself reads. Carries a per-model ``cost`` (a model is free when both
    ``cost.input`` and ``cost.output`` are 0) and a human display ``name``.
  * ``https://opencode.ai/zen/v1/models`` — the ids actually served right now.

Neither endpoint is sufficient alone: the catalog still lists *retired* models
that answer ``401 Model not supported`` on use, and the served list carries no
price information. Intersecting the two yields the currently-usable free set.

This module only reads public metadata — it never sends the free-tier wire
headers, so it works even when the free tier itself is locked out.
"""
from __future__ import annotations

import json
import urllib.request

from . import catalog_cache
from ._zen_wire import OPENCODE_USER_AGENT

CACHE_NAME = "zen_free"
CATALOG_URL = "https://models.opencode.ai/api.json"
SERVED_URL = "https://opencode.ai/zen/v1/models"
CATALOG_PROVIDER = "opencode"

# Served by the gateway and marked free in the catalog, but verified broken on
# use — deepseek-v4-flash-free answers 400 "Model is unavailable" on both APIs.
# Deprecated models are dropped as well, as OpenCode does (and as the models.dev
# path does); this list holds ids confirmed dead that the catalog doesn't mark.
BROKEN_FREE_MODELS = frozenset({
    "deepseek-v4-flash-free",
})

# The gateway 403s urllib's default "Python-urllib/x.y" User-Agent, so send the
# same identity the real CLI does.
REQUEST_HEADERS = {"Accept": "application/json", "User-Agent": OPENCODE_USER_AGENT}

# Per-request socket timeout. The two calls are sequential, so the worst-case
# added latency for a live refresh is roughly twice this.
DEFAULT_TIMEOUT = 4.0


def _get_json(url: str, timeout: float):
    req = urllib.request.Request(url, headers=dict(REQUEST_HEADERS))
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.load(resp)


def _is_free(info) -> bool:
    if not isinstance(info, dict) or info.get("status") == "deprecated":
        return False
    cost = info.get("cost") or {}
    return cost.get("input") == 0 and cost.get("output") == 0


def fetch_free_models(timeout: float = DEFAULT_TIMEOUT) -> list[tuple[str, str]] | None:
    """Return ``[(model_id, label), ...]`` for served free models.

    Returns ``None`` when the network fails, the payload is unusable, or the
    intersection is empty — callers should fall back to a static list.
    """
    try:
        catalog = _get_json(CATALOG_URL, timeout)
        served = _get_json(SERVED_URL, timeout)
    except Exception:
        return None

    catalog_models = ((catalog.get(CATALOG_PROVIDER) or {}).get("models")) or {}
    if not isinstance(catalog_models, dict):
        return None

    out: list[tuple[str, str]] = []
    for entry in served.get("data") or []:
        if not isinstance(entry, dict):
            continue
        mid = entry.get("id")
        info = catalog_models.get(mid)
        if mid and mid not in BROKEN_FREE_MODELS and _is_free(info):
            out.append((mid, (info.get("name") or mid)))
    return out or None


def cached_free_models() -> list[tuple[str, str]]:
    """Free models from the on-disk cache. Never touches the network.

    Used on UI threads (the ``/model`` picker) so opening it is instant; a
    background refresh keeps the cache current.
    """
    payload, _fresh = catalog_cache.read(CACHE_NAME)
    if not isinstance(payload, list):
        return []
    return [
        (row[0], row[1])
        for row in payload
        if isinstance(row, (list, tuple)) and len(row) >= 2 and row[0]
    ]


def cache_is_fresh() -> bool:
    _payload, fresh = catalog_cache.read(CACHE_NAME)
    return fresh


def refresh_free_models(timeout: float = DEFAULT_TIMEOUT) -> list[tuple[str, str]] | None:
    """Fetch over the network and persist. None when the fetch failed."""
    models = fetch_free_models(timeout)
    if models:
        catalog_cache.write(CACHE_NAME, [list(m) for m in models])
    return models
