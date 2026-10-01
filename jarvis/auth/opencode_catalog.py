"""What OpenCode's gateways serve right now — OpenCode Zen and OpenCode Go.

Both publish their live model ids without a key (``GET …/v1/models``). That
list decides which models exist; models.dev (``auth/models_dev.py``, the
``opencode`` and ``opencode-go`` providers) supplies everything else — names,
prices, vision, tool support, wire. Nothing about either is hard-coded in
Jarvis: a model the gateway stops serving drops out of /model on the next
refresh, and a new one appears as soon as models.dev describes it.
"""
from __future__ import annotations

import json
import urllib.request

from . import catalog_cache
from ._zen_wire import OPENCODE_USER_AGENT

# Jarvis provider id → (served-models URL, cache name).
SERVED = {
    "opencode_zen": ("https://opencode.ai/zen/v1/models", "opencode_zen_served"),
    "opencode": ("https://opencode.ai/zen/go/v1/models", "opencode_go_served"),
}

# The gateway 403s urllib's default User-Agent; send the real CLI's.
REQUEST_HEADERS = {"Accept": "application/json", "User-Agent": OPENCODE_USER_AGENT}
DEFAULT_TIMEOUT = 5.0


def _fetch(url: str, timeout: float) -> list[str] | None:
    try:
        req = urllib.request.Request(url, headers=dict(REQUEST_HEADERS))
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            payload = json.load(resp)
    except Exception:
        return None
    rows = payload.get("data") if isinstance(payload, dict) else None
    if not isinstance(rows, list):
        return None
    ids = sorted({str(r["id"]) for r in rows if isinstance(r, dict) and r.get("id")})
    return ids or None


def refresh(timeout: float = DEFAULT_TIMEOUT) -> bool:
    """Fetch both gateways' lists into the cache. True when at least one came back."""
    ok = False
    for url, cache in SERVED.values():
        ids = _fetch(url, timeout)
        if ids:
            catalog_cache.write(cache, ids)
            ok = True
    return ok


def served_ids(provider: str) -> set[str]:
    """Model ids ``provider`` served at the last fetch; empty before the first.
    A stale cache still counts — it's newer than any copy in the code."""
    entry = SERVED.get(provider)
    if entry is None:
        return set()
    payload, _fresh = catalog_cache.read(entry[1])
    return {str(x) for x in payload} if isinstance(payload, list) else set()


def cache_is_fresh() -> bool:
    return all(catalog_cache.read(cache)[1] for _url, cache in SERVED.values())
