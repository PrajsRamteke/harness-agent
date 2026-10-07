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
# Every model id OpenRouter serves (free or paid), from the same fetch: what's
# missing from it has been retired upstream, whatever another catalog says.
IDS_CACHE = "openrouter_ids"

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


def _fetch_entries(timeout: float) -> list[dict] | None:
    try:
        payload = _get_json(MODELS_URL, timeout)
    except Exception:
        return None
    entries = payload.get("data") if isinstance(payload, dict) else None
    if not isinstance(entries, list):
        return None
    return [e for e in entries if isinstance(e, dict)]


def _free_from(entries: list[dict]) -> list[FreeModel]:
    out = [m for m in (_to_free_model(e) for e in entries) if m]
    out.sort(key=_sort_key)
    return out


def fetch_free_models(timeout: float = DEFAULT_TIMEOUT) -> list[FreeModel] | None:
    """Fetch the live catalog and return its free models. None on any failure."""
    entries = _fetch_entries(timeout)
    if entries is None:
        return None
    return _free_from(entries) or None


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
    entries = _fetch_entries(timeout)
    if entries is None:
        return None
    ids = sorted({str(e["id"]) for e in entries if e.get("id")})
    if ids:
        catalog_cache.write(IDS_CACHE, ids)
    try:
        rmap = reasoning_map(entries)
        if rmap:
            catalog_cache.write(REASONING_CACHE, rmap)
    except Exception:
        pass  # thinking info is a nicety — never fail the catalog over it
    models = _free_from(entries) or None
    if models:
        catalog_cache.write(CACHE_NAME, _encode(models))
    return models


# ── which models think, and at which efforts ──────────────────────────────────
# ``reasoning`` on each /models entry: {mandatory, supported_efforts,
# default_effort, default_enabled}. Stored for every served model (not just the
# free ones) so the thinking picker can say what the chosen model takes.
REASONING_CACHE = "openrouter_reasoning"


def reasoning_map(entries: list[dict]) -> dict[str, dict]:
    """``{model id: {"on": thinks?, "m": mandatory, "e": [efforts], "d": default}}``."""
    out: dict[str, dict] = {}
    for e in entries:
        mid = e.get("id")
        if not isinstance(mid, str) or not mid:
            continue
        r = e.get("reasoning")
        params = e.get("supported_parameters") or []
        row: dict = {}
        if isinstance(r, dict):
            row["on"] = 1
            if r.get("mandatory"):
                row["m"] = 1
            efforts = [x for x in (r.get("supported_efforts") or []) if isinstance(x, str) and x]
            if efforts:
                row["e"] = efforts
            if isinstance(r.get("default_effort"), str) and r["default_effort"]:
                row["d"] = r["default_effort"]
        elif "reasoning" in params or "include_reasoning" in params or "reasoning_effort" in params:
            row["on"] = 1
        else:
            row["on"] = 0  # lists no reasoning support at all
        out[mid] = row
    return out


def cached_reasoning(model_id: str) -> dict | None:
    """The stored reasoning row for ``model_id`` — None when never fetched or
    the model isn't listed. Cache only; never touches the network."""
    payload, _fresh = catalog_cache.read(REASONING_CACHE)
    row = payload.get(model_id) if isinstance(payload, dict) else None
    return row if isinstance(row, dict) else None


def served_ids() -> set[str]:
    """Every model id OpenRouter listed at the last fetch (empty before the first).
    A stale cache still counts: it's newer than any id hard-coded or mirrored."""
    payload, _fresh = catalog_cache.read(IDS_CACHE)
    return {str(x) for x in payload} if isinstance(payload, list) else set()


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
