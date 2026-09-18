"""Live discovery of every free model OpenRouter currently serves.

OpenRouter's public catalog (``GET /api/v1/models``, no auth, no key) lists
several hundred models with per-model pricing. Anything priced at $0 for both
prompt and completion is usable by any account that has merely added an API
key — which is the whole point of the free tier.

Hard-coding those ids rots fast: of the twelve OpenRouter models this repo
shipped as constants, ten had already been retired upstream. So the picker
reads the catalog instead, and new free models show up on their own.

Only public metadata is read here; the user's API key is never sent.
"""
from __future__ import annotations

from dataclasses import dataclass
import json
import os
import urllib.request

from . import catalog_cache

MODELS_URL = "https://openrouter.ai/api/v1/models"
CACHE_NAME = "openrouter_free"

REQUEST_HEADERS = {"Accept": "application/json", "User-Agent": "harness-agent/1.0"}

DEFAULT_TIMEOUT = 6.0


def _flag(name: str) -> bool:
    return (os.getenv(name, "") or "").strip().lower() in ("1", "true", "yes", "on")


# Every free model is listed by default — that is the point of the free tier.
# Two kinds are flagged in their label and sorted last rather than hidden,
# because a model silently missing from /model is far more confusing than one
# that is present and marked:
#   * no tool use  — the harness always sends tools, so these fail every turn.
#   * restricted   — OpenRouter refused this account for that model (see below).
# Set HARNESS_OPENROUTER_HIDE_NO_TOOLS=1 to drop the tool-less ones entirely.
HIDE_NO_TOOLS = _flag("HARNESS_OPENROUTER_HIDE_NO_TOOLS")


@dataclass(frozen=True)
class FreeModel:
    """One $0 OpenRouter model, as the picker and pricing tables need it."""
    id: str
    label: str
    context_length: int = 0
    supports_images: bool = False
    supports_tools: bool = True
    restricted: bool = False

    @property
    def usable(self) -> bool:
        """True when this model can actually serve a turn in this harness."""
        return self.supports_tools and not self.restricted


def _get_json(url: str, timeout: float):
    req = urllib.request.Request(url, headers=dict(REQUEST_HEADERS))
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.load(resp)


def _is_zero(value) -> bool:
    try:
        return float(value) == 0.0
    except (TypeError, ValueError):
        return False


def _is_free(entry: dict) -> bool:
    pricing = entry.get("pricing") or {}
    if "prompt" not in pricing or "completion" not in pricing:
        return False
    return _is_zero(pricing.get("prompt")) and _is_zero(pricing.get("completion"))


def _emits_text(entry: dict) -> bool:
    """Skip image/audio/video generators — this harness only consumes text."""
    arch = entry.get("architecture") or {}
    out = arch.get("output_modalities")
    if not out:
        return True
    return "text" in out


def _fmt_ctx(n: int) -> str:
    if n >= 1_000_000:
        return f"{n / 1_000_000:.0f}M"
    if n >= 1000:
        return f"{n // 1000}K"
    return str(n)


def _label(entry: dict, ctx: int, tools: bool) -> str:
    name = (entry.get("name") or entry.get("id") or "").replace("(free)", "").strip(" -—")
    bits = [name or entry.get("id", "")]
    if ctx:
        bits.append(f"{_fmt_ctx(ctx)} ctx")
    bits.append("free" if tools else "free, no tool use")
    return f"{bits[0]} — {', '.join(bits[1:])}"


def _to_free_model(entry: dict) -> FreeModel | None:
    mid = entry.get("id")
    if not mid or not _is_free(entry) or not _emits_text(entry):
        return None
    arch = entry.get("architecture") or {}
    try:
        ctx = int(entry.get("context_length") or 0)
    except (TypeError, ValueError):
        ctx = 0
    tools = "tools" in (entry.get("supported_parameters") or [])
    return FreeModel(
        id=mid,
        label=_label(entry, ctx, tools),
        context_length=ctx,
        supports_images="image" in (arch.get("input_modalities") or []),
        supports_tools=tools,
    )


def _sort_key(m: FreeModel):
    # Tool-capable first (the only ones that work end to end), then widest
    # context, then alphabetically for a stable order across refreshes.
    return (not m.supports_tools, -m.context_length, m.id)


def fetch_free_models(timeout: float = DEFAULT_TIMEOUT) -> list[FreeModel] | None:
    """Fetch the live catalog and return its free models. None on any failure."""
    try:
        payload = _get_json(MODELS_URL, timeout)
    except Exception:
        return None
    entries = payload.get("data") if isinstance(payload, dict) else None
    if not isinstance(entries, list):
        return None
    out = [m for m in (_to_free_model(e) for e in entries if isinstance(e, dict)) if m]
    out.sort(key=_sort_key)
    return out or None


def _decode(payload) -> list[FreeModel]:
    if not isinstance(payload, list):
        return []
    out: list[FreeModel] = []
    for row in payload:
        if not isinstance(row, dict) or not row.get("id"):
            continue
        out.append(FreeModel(
            id=row["id"],
            label=row.get("label") or row["id"],
            context_length=int(row.get("context_length") or 0),
            supports_images=bool(row.get("supports_images")),
            supports_tools=bool(row.get("supports_tools", True)),
        ))
    return out


def _encode(models: list[FreeModel]) -> list[dict]:
    return [
        {
            "id": m.id,
            "label": m.label,
            "context_length": m.context_length,
            "supports_images": m.supports_images,
            "supports_tools": m.supports_tools,
        }
        for m in models
    ]


# ── models the account cannot actually reach ──────────────────────────────────
# Some free models are gated to OpenRouter's allowlisted "agentic harness" apps
# and answer 403 permission_denied no matter how valid the key is. Nothing in
# the catalog marks them, so the only way to know is to be refused once — after
# which the id is remembered here and dropped from the picker.
BLOCKED_CACHE = "openrouter_blocked"
BLOCKED_TTL = 7 * 24 * 3600


def _blocked_raw() -> dict:
    payload, _fresh = catalog_cache.read(BLOCKED_CACHE)
    return payload if isinstance(payload, dict) else {}


def blocked_ids() -> set[str]:
    """Model ids refused by OpenRouter recently. Entries age out after a week,
    so a model that gets un-gated comes back on its own."""
    import time

    now = time.time()
    return {
        mid for mid, at in _blocked_raw().items()
        if isinstance(at, (int, float)) and now - at < BLOCKED_TTL
    }


def mark_unavailable(model_id: str) -> None:
    """Remember that ``model_id`` refused this account, so we stop offering it."""
    import time

    if not model_id:
        return
    entries = _blocked_raw()
    entries[model_id] = time.time()
    catalog_cache.write(BLOCKED_CACHE, entries)


def clear_unavailable() -> None:
    """Forget every refusal — used by an explicit /model refresh."""
    catalog_cache.write(BLOCKED_CACHE, {})


def cached_free_models() -> list[FreeModel]:
    """Free models from the on-disk cache. Never touches the network."""
    payload, _fresh = catalog_cache.read(CACHE_NAME)
    return _decode(payload)


def cache_is_fresh() -> bool:
    _payload, fresh = catalog_cache.read(CACHE_NAME)
    return fresh


def refresh_free_models(
    timeout: float = DEFAULT_TIMEOUT, *, retry_blocked: bool = False
) -> list[FreeModel] | None:
    """Fetch and persist. Returns the new list, or None when the fetch failed.

    ``retry_blocked=True`` also forgets earlier refusals, so an explicit
    ``/model refresh`` gives every free model another chance.
    """
    if retry_blocked:
        clear_unavailable()
    models = fetch_free_models(timeout)
    if models:
        catalog_cache.write(CACHE_NAME, _encode(models))
    return models


def free_models(live: bool = False) -> list[FreeModel]:
    """Free models for display.

    ``live=False`` (default) reads the cache only, so callers on a UI thread
    never block. ``live=True`` refreshes over the network first and falls back
    to the cache when that fails.
    """
    if live:
        fetched = refresh_free_models()
        if fetched:
            return _for_display(fetched)
    return _for_display(cached_free_models())


def _for_display(models: list[FreeModel]) -> list[FreeModel]:
    """Annotate and order the free tier for the picker — nothing is dropped
    unless the user asked for tool-less models to be hidden."""
    from dataclasses import replace

    blocked = blocked_ids()
    out: list[FreeModel] = []
    for m in models:
        if HIDE_NO_TOOLS and not m.supports_tools:
            continue
        if m.id in blocked:
            m = replace(m, restricted=True, label=f"{m.label}, restricted")
        out.append(m)
    # Usable models first, then tool-less, then refused — each group by widest
    # context. A caveated model stays reachable, just never the first thing you
    # land on.
    out.sort(key=lambda m: (m.restricted, not m.supports_tools, -m.context_length, m.id))
    return out


def usable_free_models() -> list[FreeModel]:
    """Free models that can actually serve a turn — what a fallback may pick."""
    return [m for m in _for_display(cached_free_models()) if m.usable]
