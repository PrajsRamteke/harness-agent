"""The Harness Agent free list's stand-in while models.dev is off.

Normally the free tier lists OpenCode Zen's models at $0 from the models.dev
catalog (``constants.providers.harness_agent_models``), and this module isn't
fetched. With ``HARNESS_MODELS_DEV=0`` it reads the same data from OpenCode's
own copy of the catalog instead — the one the real OpenCode CLI reads:

  * ``https://models.opencode.ai/api.json`` — carries a per-model ``cost`` (a
    model is free when both ``cost.input`` and ``cost.output`` are 0), a
    human display ``name`` and a ``status``.

Retired models are the ones OpenCode marks ``status: "deprecated"``; those are
dropped, exactly as OpenCode (and the models.dev path) does. The gateway's
served list (``https://opencode.ai/inference/v1/models``) is deliberately *not*
used as a gate: it lags behind for free models — it left out
``nemotron-3-ultra-free`` and ``longcat-2.5-preview-free`` while the gateway
served both — so intersecting with it hid working models.

This module only reads public metadata — it never sends the free-tier wire
headers, so it works even when the free tier itself is locked out.
"""
from __future__ import annotations

import json
import re
import urllib.request

from . import catalog_cache
from ._zen_wire import OPENCODE_USER_AGENT

CACHE_NAME = "zen_free"
CATALOG_URL = "https://models.opencode.ai/api.json"
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

# Per-request socket timeout.
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


def _neg_date(release: str) -> tuple:
    """Sort key for a release date — ascending puts the newest first."""
    digits = [int(x) for x in re.findall(r"\d+", release or "")][:3]
    while len(digits) < 3:
        digits.append(0)
    return tuple(-d for d in digits)


def fetch_free_models(timeout: float = DEFAULT_TIMEOUT) -> list[tuple[str, str]] | None:
    """Return ``[(model_id, label), ...]`` for the free models OpenCode serves.

    A model is free when OpenCode's own catalog prices it at $0 and doesn't
    mark it deprecated — the same set the OpenCode CLI shows. Newest first,
    so the free tier's default is the same model the models.dev path picks.

    Returns ``None`` when the network fails, the payload is unusable, or
    nothing is free — callers should fall back to a static list.
    """
    try:
        catalog = _get_json(CATALOG_URL, timeout)
    except Exception:
        return None

    catalog_models = ((catalog.get(CATALOG_PROVIDER) or {}).get("models")) or {}
    if not isinstance(catalog_models, dict):
        return None

    rows: list[tuple[str, str, str]] = []
    for mid, info in catalog_models.items():
        if not isinstance(mid, str) or mid in BROKEN_FREE_MODELS or not _is_free(info):
            continue
        rows.append((mid, (info.get("name") or mid), str(info.get("release_date") or "")))
    if not rows:
        return None
    rows.sort(key=lambda r: r[1].lower())
    rows.sort(key=lambda r: _neg_date(r[2]))
    return [(mid, label) for mid, label, _ in rows]


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
