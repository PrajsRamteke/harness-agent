"""Which thinking efforts a model actually takes — discovered, not assumed.

Models differ a lot: one takes ``low … max``, another only ``high``, another is
an on/off switch, another always reasons and offers no control, many don't think
at all. Sending a level a model doesn't know is a 400, so every provider's own
catalog is read for it:

  * Anthropic   — ``capabilities`` on ``GET /v1/models`` (``anthropic_models``)
  * Codex       — ``supported_reasoning_levels`` (``codex_catalog``)
  * OpenRouter  — ``reasoning.supported_efforts`` (``openrouter_catalog``)
  * everyone else (OpenCode Go / Zen, the free tier, ~200 models.dev providers)
                — models.dev ``reasoning_options`` (``models_dev``)

Everything here reads the on-disk caches only — it is safe on a UI thread, never
raises, and **knows when it doesn't know**: a model nothing is recorded for
gets :data:`UNKNOWN`, which offers the legacy list and changes nothing, so the
behaviour before this module existed is the floor, never lower.

A level a provider refused at request time is remembered (``mark_refused``) and
taken out of what is offered, so a catalog that is wrong about one model heals
after one 400.
"""
from __future__ import annotations

from dataclasses import dataclass
import time

from . import catalog_cache
from ..constants.models import THINK_EFFORTS_LEGACY
from ..constants.providers import (
    PROVIDER_ANTHROPIC,
    PROVIDER_ANTIGRAVITY,
    PROVIDER_HARNESS_AGENT,
    PROVIDER_OPENAI_CODEX,
    PROVIDER_OPENCODE_ZEN,
    PROVIDER_OPENROUTER,
    claude_uses_adaptive_thinking,
)

# Lowest → highest. "none" is not a level: it is "thinking off" (``can_off``).
LEVEL_ORDER = ("minimal", "low", "medium", "high", "xhigh", "max", "ultra")
_RANK = {lvl: i for i, lvl in enumerate(LEVEL_ORDER)}

# Modes (``ThinkCaps.mode``)
MODE_UNKNOWN = "unknown"   # nothing recorded — everything legacy is offered
MODE_NONE = "none"         # the model doesn't think
MODE_FIXED = "fixed"       # always thinks, nothing to choose
MODE_TOGGLE = "toggle"     # on / off only
MODE_LEVELS = "levels"     # a set of effort levels

REFUSED_CACHE = "think_refused"
REFUSED_TTL = 7 * 24 * 3600
REFUSED_ALL = "*"          # the provider refused thinking itself, not one level

_DESCRIPTIONS = {
    "ultra": "maximum reasoning plus automatic delegation",
    "max": "deepest reasoning · slowest",
    "xhigh": "extra-high reasoning",
    "high": "strong reasoning",
    "medium": "balanced reasoning",
    "low": "lighter reasoning · faster",
    "minimal": "minimal reasoning",
    "none": "thinking off · fastest",
    "on": "thinking on (this model has no levels)",
}


@dataclass(frozen=True)
class ThinkCaps:
    """What one model takes for thinking."""

    known: bool = False
    source: str = ""                   # where it came from, for the UI
    reasoning: bool = True             # can the model think at all
    levels: tuple[str, ...] = ()       # selectable efforts, lowest → highest
    can_off: bool = True               # can thinking be switched off
    default: str = ""                  # the model's own default effort ('' = unknown)
    learned: bool = False              # narrowed by something a provider refused

    @property
    def mode(self) -> str:
        if not self.known:
            return MODE_UNKNOWN
        if not self.reasoning:
            return MODE_NONE
        if self.levels:
            return MODE_LEVELS
        return MODE_TOGGLE if self.can_off else MODE_FIXED

    @property
    def lowest(self) -> str:
        return self.levels[0] if self.levels else ""

    def takes(self, effort: str) -> bool:
        """Would a request at ``effort`` be accepted?"""
        if effort == "none":
            return self.can_off or not self.known or not self.reasoning
        if not self.known:
            return effort in THINK_EFFORTS_LEGACY
        return self.reasoning and effort in self.levels

    def summary(self) -> str:
        """One short line: what this model takes."""
        mode = self.mode
        if mode == MODE_UNKNOWN:
            return "not recorded for this model — all levels shown"
        if mode == MODE_NONE:
            return "this model doesn't think"
        if mode == MODE_FIXED:
            return "always thinks — no level to choose"
        if mode == MODE_TOGGLE:
            return "on / off only — no levels"
        return " · ".join(reversed(self.levels))


UNKNOWN = ThinkCaps()


def _sorted_levels(values) -> tuple[str, ...]:
    """Valid levels only, deduplicated, lowest → highest. "none" is dropped."""
    seen = {v for v in values if isinstance(v, str) and v in _RANK}
    return tuple(sorted(seen, key=_RANK.__getitem__))


# ── learned refusals ──────────────────────────────────────────────────────────

def _key(provider: str, model: str) -> str:
    return f"{provider}|{model}"


def _refused_raw() -> dict:
    payload, _fresh = catalog_cache.read(REFUSED_CACHE)
    return payload if isinstance(payload, dict) else {}


def refused_efforts(provider: str, model: str) -> set[str]:
    """Efforts (or ``"*"``) this provider refused for this model recently."""
    now = time.time()
    rows = _refused_raw().get(_key(provider, model))
    if not isinstance(rows, dict):
        return set()
    return {
        eff for eff, at in rows.items()
        if isinstance(at, (int, float)) and now - at < REFUSED_TTL
    }


def mark_refused(provider: str, model: str, effort: str = REFUSED_ALL) -> None:
    """Remember that ``provider`` refused ``effort`` (or thinking at all, ``"*"``)
    for ``model``. Entries age out after a week, so a fixed model comes back."""
    if not model or not effort:
        return
    try:
        entries = _refused_raw()
        rows = entries.get(_key(provider, model))
        rows = dict(rows) if isinstance(rows, dict) else {}
        rows[effort] = time.time()
        entries[_key(provider, model)] = rows
        catalog_cache.write(REFUSED_CACHE, entries)
    except Exception:
        pass  # remembering is best effort; the retry itself already happened


def clear_refused() -> None:
    """Forget every refusal — an explicit ``/model refresh``."""
    try:
        catalog_cache.write(REFUSED_CACHE, {})
    except Exception:
        pass


def looks_like_thinking_refusal(err: BaseException | str) -> str:
    """For a 400 message: ``"effort"`` / ``"thinking"`` when it is about the
    thinking settings we sent, else ``""``. Deliberately narrow — an unrelated
    400 (bad tool schema, too long) must not make us drop thinking."""
    text = str(err or "").lower()
    if not text:
        return ""
    if "effort" in text and any(w in text for w in (
        "not support", "unsupported", "invalid", "not a valid", "must be one of",
        "unknown", "not allowed", "does not accept", "unrecognized",
    )):
        return "effort"
    if any(w in text for w in ("thinking", "reasoning", "budget_tokens")) and any(w in text for w in (
        "not support", "unsupported", "not allowed", "not available", "does not accept",
        "unknown parameter", "unrecognized", "extra inputs", "invalid",
    )):
        return "thinking"
    return ""


# ── per-source readers (each returns ThinkCaps or None = "no information") ────

def _from_anthropic(model: str) -> ThinkCaps | None:
    try:
        from .anthropic_models import cached_capabilities

        row = cached_capabilities(model)
    except Exception:
        row = None
    if isinstance(row, dict):
        levels = _sorted_levels(row.get("e") or ())
        thinks = bool(row.get("on"))
        adaptive = bool(row.get("a"))
        budget = bool(row.get("b"))
        # Adaptive-only models are always sent a level (the request path never
        # sends ``thinking: disabled`` to them), so they can't be "off".
        can_off = (not adaptive) or budget
        return ThinkCaps(
            known=True, source="Anthropic API", reasoning=thinks,
            levels=levels if thinks else (), can_off=can_off if thinks else True,
        )
    return None


# Used only until the Models API answered once (first run, offline): the adaptive
# line-up is documented, and adaptive models always carry a full effort ladder.
def _anthropic_builtin(model: str) -> ThinkCaps | None:
    if claude_uses_adaptive_thinking(model):
        return ThinkCaps(
            known=True, source="built-in", reasoning=True,
            levels=("low", "medium", "high", "xhigh", "max"), can_off=False,
        )
    return None


def _from_models_dev(model: str, provider: str) -> ThinkCaps | None:
    try:
        from . import models_dev

        if models_dev.is_catalog_provider(provider):
            m = models_dev.get_model(provider, model)
        else:
            src = PROVIDER_OPENCODE_ZEN if provider == PROVIDER_HARNESS_AGENT else provider
            m = models_dev.native_model(src, model)
    except Exception:
        m = None
    if m is None:
        return None
    if not m.reasoning:
        return ThinkCaps(known=True, source="models.dev", reasoning=False)
    if not m.reasoning_known:
        return None  # models.dev says it reasons but not how it is steered
    levels = _sorted_levels(m.efforts)
    return ThinkCaps(
        known=True, source="models.dev", reasoning=True, levels=levels,
        can_off=("none" in m.efforts) or m.toggle,
    )


def _from_openrouter(model: str) -> ThinkCaps | None:
    try:
        from .openrouter_catalog import cached_reasoning

        row = cached_reasoning(model)
    except Exception:
        row = None
    if not isinstance(row, dict):
        return None
    if not row.get("on"):
        return ThinkCaps(known=True, source="OpenRouter", reasoning=False)
    raw = row.get("e") or ()
    levels = _sorted_levels(raw)
    mandatory = bool(row.get("m"))
    default = row.get("d") if isinstance(row.get("d"), str) else ""
    return ThinkCaps(
        known=True, source="OpenRouter", reasoning=True, levels=levels,
        can_off=not mandatory, default=default if default in _RANK else "",
    )


def _from_codex(model: str) -> ThinkCaps | None:
    try:
        from .codex_catalog import cached_models

        match = next((m for m in cached_models() if m.id == model), None)
    except Exception:
        match = None
    if match is None or match.efforts is None:
        return None
    levels = _sorted_levels(match.efforts)
    return ThinkCaps(
        known=True, source="Codex", reasoning=bool(match.efforts), levels=levels,
        can_off="none" in match.efforts,
        default=match.default_effort if match.default_effort in _RANK else "",
    )


def _from_antigravity(model: str) -> ThinkCaps | None:
    """Antigravity's model list doesn't describe thinking; the form follows
    the model family (``antigravity_catalog.think_facts``)."""
    from .antigravity_catalog import think_facts

    kind, levels, can_off = think_facts(model)
    if kind == "none":
        return ThinkCaps(known=True, source="Antigravity", reasoning=False)
    return ThinkCaps(known=True, source="Antigravity", reasoning=True,
                     levels=_sorted_levels(levels), can_off=can_off)


def _from_local(model: str, provider: str) -> ThinkCaps | None:
    """What the last scan of a local server said (Ollama's capabilities);
    None for the OpenAI-compatible servers, which don't say."""
    from . import local_models

    thinks, levels, can_off = local_models.think_facts(provider, model)
    if thinks is None:
        return None
    src = local_models.label(provider)
    if not thinks:
        return ThinkCaps(known=True, source=src, reasoning=False)
    return ThinkCaps(known=True, source=src, reasoning=True,
                     levels=_sorted_levels(levels), can_off=can_off)


def _lookup(model: str, provider: str) -> ThinkCaps:
    if provider.startswith("local:"):
        readers = (lambda: _from_local(model, provider),)
    elif provider == PROVIDER_OPENAI_CODEX:
        readers = (lambda: _from_codex(model),)
    elif provider == PROVIDER_ANTIGRAVITY:
        readers = (lambda: _from_antigravity(model),)
    elif provider == PROVIDER_ANTHROPIC:
        readers = (
            lambda: _from_anthropic(model),
            lambda: _from_models_dev(model, provider),
            lambda: _anthropic_builtin(model),
        )
    elif provider == PROVIDER_OPENROUTER:
        readers = (lambda: _from_openrouter(model), lambda: _from_models_dev(model, provider))
    else:
        readers = (lambda: _from_models_dev(model, provider),)
    for read in readers:
        try:
            caps = read()
        except Exception:
            caps = None
        if caps is not None:
            return caps
    return UNKNOWN


# The UIs ask on every footer paint and web poll, so answers are memoised — but
# only while the cache files they came from are untouched (mtime + size), so a
# refresh, a refusal or a test seeding a catalog is seen at once.
_memo: dict[tuple[str, str], tuple[tuple, ThinkCaps]] = {}
_CACHES = ("codex_models", "anthropic_caps", "openrouter_reasoning", REFUSED_CACHE)


def _stamp() -> tuple:
    import os

    paths = [catalog_cache.CACHE_DIR / f"{n}.json" for n in _CACHES]
    try:
        from . import models_dev

        paths.append(models_dev.CACHE_FILE)
    except Exception:
        pass
    try:
        from . import local_models

        paths.append(local_models.CACHE_FILE)
    except Exception:
        pass
    out = []
    for path in paths:
        try:
            st = os.stat(path)
            out.append((str(path), st.st_mtime_ns, st.st_size))
        except OSError:
            out.append((str(path), 0, 0))
    return tuple(out)


def thinking_caps(model: str, provider: str | None = None) -> ThinkCaps:
    """What ``model`` takes for thinking under ``provider`` (default: the active
    one). Cache reads only; never raises; :data:`UNKNOWN` when nothing is known."""
    try:
        if provider is None:
            from .. import state

            provider = getattr(state, "provider", "") or ""
        model = (model or "").strip()
        if not model:
            return UNKNOWN
        stamp = _stamp()
        hit = _memo.get((provider, model))
        if hit is not None and hit[0] == stamp:
            return hit[1]
        caps = _resolve(model, provider)
        if len(_memo) > 256:
            _memo.clear()
        _memo[(provider, model)] = (stamp, caps)
        return caps
    except Exception:
        return UNKNOWN


def _resolve(model: str, provider: str) -> ThinkCaps:
    try:
        caps = _lookup(model, provider)
        refused = refused_efforts(provider, model)
        if not refused:
            return caps
        if REFUSED_ALL in refused:
            return ThinkCaps(known=True, source=caps.source or "learned", reasoning=False, learned=True)
        if not caps.known:
            # Nothing recorded, but a level was refused: offer the legacy list
            # minus what failed.
            left = _sorted_levels(THINK_EFFORTS_LEGACY)
            left = tuple(x for x in left if x not in refused)
            return ThinkCaps(known=True, source="learned", reasoning=True, levels=left, learned=True)
        left = tuple(x for x in caps.levels if x not in refused)
        if left == caps.levels:
            return caps
        return ThinkCaps(
            known=True, source=caps.source, reasoning=caps.reasoning, levels=left,
            can_off=caps.can_off, default=caps.default if caps.default in left else "", learned=True,
        )
    except Exception:
        return UNKNOWN


# ── choosing a level ──────────────────────────────────────────────────────────

def nearest_level(effort: str, levels: tuple[str, ...]) -> str:
    """The supported level closest to ``effort``: the nearest one at or below it,
    else the lowest above. '' when there are no levels."""
    if not levels:
        return ""
    if effort in levels:
        return effort
    rank = _RANK.get(effort)
    if rank is None:
        return levels[-1] if "high" not in levels else "high"
    below = [x for x in levels if _RANK[x] <= rank]
    return below[-1] if below else levels[0]


def choice_rows(caps: ThinkCaps, current: str = "") -> list[dict]:
    """The rows both pickers show, highest first, ending with "none".

    Each row: ``{value, detail, available, why}``. ``value`` is what to set
    (a level, or "none"; ``"on"`` for a model that is just a switch — callers
    turn it into their remembered effort). Levels the model lacks are listed as
    unavailable instead of hidden, so it's clear *why* the list is short; only
    the extreme levels (max / ultra) appear when the model takes them.
    """
    rows: list[dict] = []

    def row(value: str, available: bool, why: str = "", detail: str = "") -> None:
        rows.append({
            "value": value,
            "detail": detail or _DESCRIPTIONS.get(value, ""),
            "available": available,
            "why": why,
        })

    mode = caps.mode
    if mode == MODE_UNKNOWN:
        for lvl in reversed(LEVEL_ORDER):
            if lvl in THINK_EFFORTS_LEGACY:
                row(lvl, True)
        row("none", True)
        return rows
    if mode == MODE_NONE:
        row("none", True, detail="this model doesn't think")
        return rows
    if mode == MODE_FIXED:
        row("on", True, detail="always thinks — nothing to choose")
        return rows
    if mode == MODE_TOGGLE:
        row("on", True)
        row("none", True)
        return rows
    for lvl in reversed(LEVEL_ORDER):
        if lvl in caps.levels:
            row(lvl, True, detail=_DESCRIPTIONS[lvl] + (" · model default" if lvl == caps.default else ""))
        elif lvl in THINK_EFFORTS_LEGACY:
            row(lvl, False, why="not supported by this model")
    row("none", caps.can_off, why="" if caps.can_off else "this model always thinks")
    return rows


def refresh_all(*, retry_refused: bool = False) -> bool:
    """Refresh the one source ``refresh_model_catalogs`` doesn't cover: the
    Anthropic Models API (needs the signed-in client). True when it answered."""
    if retry_refused:
        clear_refused()
    try:
        from .. import state
        from .anthropic_models import refresh_anthropic_capabilities

        if getattr(state, "provider", "") == PROVIDER_ANTHROPIC and state.client is not None:
            return refresh_anthropic_capabilities(state.client)
    except Exception:
        pass
    return False
