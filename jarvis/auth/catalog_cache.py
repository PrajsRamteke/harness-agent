"""Disk cache for public model catalogs (stale-while-revalidate).

Model catalogs live behind public HTTP endpoints. Hitting them while the
``/model`` picker is opening blocks the UI thread for as long as the request
takes, which is what made model selection feel sluggish. Every catalog reader
now goes through this cache instead:

  * :func:`read` returns whatever was persisted last — instantly, never a
    network call — plus whether it is still within the TTL.
  * :func:`write` persists a fresh fetch.

Callers render the cached rows immediately and refresh in a background worker,
so a cold cache costs one slow picker open and a warm one costs nothing.
"""
from __future__ import annotations

import json
import os
import time
from typing import Any

from ..constants.paths import CONFIG_DIR

CACHE_DIR = CONFIG_DIR / "model_catalog"

# How long a cached catalog is considered fresh. Free model line-ups change on
# the order of days, so hours of staleness is harmless — and a stale entry is
# still served (then refreshed in the background) rather than discarded.
DEFAULT_TTL = 6 * 3600


def _ttl() -> int:
    try:
        return max(0, int(os.getenv("HARNESS_MODEL_CATALOG_TTL", str(DEFAULT_TTL))))
    except (TypeError, ValueError):
        return DEFAULT_TTL


def _path(name: str):
    return CACHE_DIR / f"{name}.json"


def read(name: str) -> tuple[Any | None, bool]:
    """Return ``(payload, fresh)``. ``payload`` is None when nothing is cached.

    ``fresh`` is False for an expired entry — the payload is still returned so
    callers can show something while a refresh runs.
    """
    try:
        raw = json.loads(_path(name).read_text())
    except (OSError, ValueError):
        return None, False
    if not isinstance(raw, dict) or "payload" not in raw:
        return None, False
    try:
        age = time.time() - float(raw.get("fetched_at") or 0)
    except (TypeError, ValueError):
        age = float("inf")
    return raw["payload"], age < _ttl()


def write(name: str, payload: Any) -> None:
    """Persist ``payload`` under ``name``. Failures are silent (cache is advisory)."""
    try:
        CACHE_DIR.mkdir(parents=True, exist_ok=True)
        tmp = _path(name).with_suffix(".tmp")
        tmp.write_text(json.dumps({"fetched_at": time.time(), "payload": payload}))
        tmp.replace(_path(name))
    except (OSError, TypeError, ValueError):
        pass


def clear(name: str) -> None:
    try:
        _path(name).unlink()
    except OSError:
        pass
