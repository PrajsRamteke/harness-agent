"""Live Anthropic model discovery after a validated API connection."""
from __future__ import annotations

import threading
from typing import TYPE_CHECKING

from ..constants import ANTHROPIC_MODELS, ANTHROPIC_AUTH_MODEL_IDS

if TYPE_CHECKING:
    from anthropic import Anthropic


def fetch_anthropic_model_ids(client: "Anthropic") -> list[str]:
    """Return model ids from the Anthropic API, or [] if the call fails."""
    try:
        page = client.models.list(limit=100)
        return [m.id for m in page.data if getattr(m, "id", None)]
    except Exception:
        return []


CAPS_CACHE = "anthropic_caps"


def _capability_row(model) -> tuple[str, dict] | None:
    """``(id, row)`` from one Models API entry, or None when it carries no
    ``capabilities`` (an older API / proxy). ``row``: ``{"on", "e", "a", "b"}`` —
    thinks at all, effort levels, adaptive thinking, budget thinking."""
    try:
        d = model.model_dump() if hasattr(model, "model_dump") else dict(model)
    except Exception:
        return None
    mid = d.get("id")
    caps = d.get("capabilities")
    if not isinstance(mid, str) or not isinstance(caps, dict):
        return None

    def ok(node) -> bool:
        return isinstance(node, dict) and bool(node.get("supported"))

    thinking = caps.get("thinking") if isinstance(caps.get("thinking"), dict) else {}
    types = thinking.get("types") if isinstance(thinking.get("types"), dict) else {}
    effort = caps.get("effort") if isinstance(caps.get("effort"), dict) else {}
    levels = [k for k, v in effort.items() if k != "supported" and ok(v)] if ok(effort) else []
    return mid, {
        "on": 1 if (ok(thinking) or ok(types.get("adaptive")) or ok(types.get("enabled"))) else 0,
        "e": levels,
        "a": 1 if ok(types.get("adaptive")) else 0,
        "b": 1 if ok(types.get("enabled")) else 0,
    }


def fetch_anthropic_capabilities(client: "Anthropic") -> dict[str, dict]:
    """Per-model thinking capabilities from the Models API; {} on any failure."""
    try:
        page = client.models.list(limit=100)
        rows = [_capability_row(m) for m in getattr(page, "data", None) or []]
    except Exception:
        return {}
    return {mid: row for mid, row in (r for r in rows if r)}


def refresh_anthropic_capabilities(client: "Anthropic") -> bool:
    """Fetch and persist the capabilities. True when something came back."""
    from . import catalog_cache

    caps = fetch_anthropic_capabilities(client)
    if caps:
        catalog_cache.write(CAPS_CACHE, caps)
    return bool(caps)


def cached_capabilities(model_id: str) -> dict | None:
    """The stored capability row for ``model_id`` (cache only, no network)."""
    from . import catalog_cache

    payload, _fresh = catalog_cache.read(CAPS_CACHE)
    row = payload.get(model_id) if isinstance(payload, dict) else None
    return row if isinstance(row, dict) else None


def sync_anthropic_model_ids(client: "Anthropic") -> list[str]:
    """Fetch live model ids, store on ``state.anthropic_model_ids``, return them."""
    from .. import state

    ids = fetch_anthropic_model_ids(client)
    if ids:
        state.anthropic_model_ids = ids
    try:
        refresh_anthropic_capabilities(client)
    except Exception:
        pass
    return ids


def defer_anthropic_model_sync(client: "Anthropic") -> None:
    """Background model discovery so startup is not blocked on a second API call."""

    def _run() -> None:
        try:
            sync_anthropic_model_ids(client)
        except Exception:
            pass

    threading.Thread(
        target=_run,
        daemon=True,
        name="anthropic-model-sync",
    ).start()


def anthropic_auth_models_for_picker() -> list[tuple[str, str]]:
    """OAuth / Pro-Max models — catalog plus any live ids from the subscription."""
    from .. import state

    static = {m: d for m, d in ANTHROPIC_MODELS}
    rows: list[tuple[str, str]] = []
    seen: set[str] = set()

    for mid in ANTHROPIC_AUTH_MODEL_IDS:
        if mid in static:
            rows.append((mid, static[mid]))
            seen.add(mid)

    for mid in state.anthropic_model_ids or []:
        if mid not in seen:
            rows.append((mid, static.get(mid, mid)))
            seen.add(mid)

    if rows:
        return rows
    return [(mid, static[mid]) for mid in ANTHROPIC_AUTH_MODEL_IDS if mid in static]


def format_anthropic_model_lines(model_ids: list[str]) -> list[str]:
    static = {m: d for m, d in ANTHROPIC_MODELS}
    lines: list[str] = []
    for mid in model_ids:
        desc = static.get(mid, "")
        lines.append(f"{mid} — {desc}" if desc else mid)
    return lines
