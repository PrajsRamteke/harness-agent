"""Live provider + model catalog from models.dev.

https://models.dev is an open database of AI providers and their models —
context window, price, tool support, vision, reasoning — kept up to date by
its community (MIT licence, (c) 2025 models.dev; https://github.com/sst/models.dev).
OpenCode builds its model list from it, and so does this module:

  * Every provider models.dev lists that speaks a protocol this harness has a
    client for (OpenAI chat-completions — most of them — or Anthropic
    messages) becomes a usable provider. A user adds its API key in ``/key``
    and its models appear in ``/model``. Provider ids are namespaced
    ``md:<models.dev id>`` so they can never collide with a built-in one.
  * Built-in providers (Anthropic API, OpenRouter, OpenCode Go / Zen) keep
    their own clients; models.dev only adds the models they serve that the
    static lists don't know yet.

There is no per-provider endpoint — ``/api.json`` is the whole database
(~5 MB, ~500 KB gzipped). It is fetched on a worker thread with
``If-None-Match`` (an unchanged catalog answers 304, no body), trimmed to the
fields used here and cached under ``~/.config/harness-agent/model_catalog/``.
UI code only ever reads that cache. ``HARNESS_MODELS_DEV=0`` turns all of it
off (nothing fetched, no catalog providers).
"""
from __future__ import annotations

import gzip
import json
import os
import re
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field

from . import catalog_cache

API_URL = "https://models.dev/api.json"
SITE_URL = "https://models.dev"
PREFIX = "md:"
DEFAULT_TIMEOUT = 15.0
# Bump when the trimmed cache layout or the support rules change: an older
# cache is then ignored (and refetched in full) instead of misread.
SCHEMA = 1

REQUEST_HEADERS = {
    "Accept": "application/json",
    "Accept-Encoding": "gzip",
    "User-Agent": "harness-agent/1.0 (+https://models.dev)",
}

# models.dev ids served by a built-in provider (own client, own key file).
# They are never offered twice; their entries only enrich the built-in lists.
NATIVE_IDS: dict[str, str] = {
    "anthropic": "anthropic",
    "openrouter": "openrouter",
    "opencode-go": "opencode",     # Jarvis's "opencode" is OpenCode Go
    "opencode": "opencode_zen",    # models.dev's "opencode" is OpenCode Zen
}

# Listed by models.dev but not reachable with a plain API key here:
# a built-in already (kimchi), or a token exchange / sign-in flow models.dev
# can't describe (GitHub Copilot, GitLab Duo).
SKIP_IDS = frozenset({"kimchi", "github-copilot", "gitlab"})

# Providers whose models.dev entry names an SDK package instead of a base URL.
# Each of these also serves the OpenAI chat-completions API at this address.
KNOWN_BASE_URLS: dict[str, str] = {
    "openai": "https://api.openai.com/v1",
    "groq": "https://api.groq.com/openai/v1",
    "cerebras": "https://api.cerebras.ai/v1",
    "deepinfra": "https://api.deepinfra.com/v1/openai",
    "togetherai": "https://api.together.xyz/v1",
    "xai": "https://api.x.ai/v1",
    "mistral": "https://api.mistral.ai/v1",
    "perplexity": "https://api.perplexity.ai",
    "google": "https://generativelanguage.googleapis.com/v1beta/openai",
    "cohere": "https://api.cohere.ai/compatibility/v1",
    "venice": "https://api.venice.ai/api/v1",
    "vercel": "https://ai-gateway.vercel.sh/v1",
    "aihubmix": "https://aihubmix.com/v1",
}

# Env vars too common to mean "use this provider" (set by gh, CI, …).
GENERIC_ENV = frozenset({"GITHUB_TOKEN", "GH_TOKEN"})

_OPENAI_COMPAT = "@ai-sdk/openai-compatible"
_OPENAI_RESPONSES = "@ai-sdk/openai"
_ANTHROPIC = "@ai-sdk/anthropic"

WIRE_OPENAI = "openai"        # OpenAI chat-completions (+ /responses per model)
WIRE_ANTHROPIC = "anthropic"  # Anthropic /v1/messages


def enabled() -> bool:
    return (os.getenv("HARNESS_MODELS_DEV", "1") or "").strip().lower() not in (
        "0", "false", "no", "off",
    )


# ── data ──────────────────────────────────────────────────────────────────────


def _fmt_ctx(n: int) -> str:
    if n >= 1_000_000:
        v = n / 1_000_000
        return f"{v:.0f}M" if v >= 10 else f"{v:.1f}".rstrip("0").rstrip(".") + "M"
    if n >= 1000:
        return f"{n // 1000}K"
    return str(n)


def _fmt_price(v: float) -> str:
    return f"${round(v, 4):g}"


@dataclass(frozen=True)
class CatalogModel:
    """One model as the picker, pricing and the client need it."""

    id: str
    name: str = ""
    tools: bool = True
    images: bool = False
    reasoning: bool = False
    context: int = 0
    output: int = 0
    input_price: float | None = None   # USD per 1M tokens; None = unknown
    output_price: float | None = None
    release: str = ""                  # YYYY-MM-DD (or YYYY-MM)
    wire: str = "chat"                 # "chat" | "responses" (OpenAI wire only)
    reasoning_field: str = ""          # "reasoning_content" when it must be echoed back
    status: str = ""                   # "" | "beta"

    @property
    def free(self) -> bool:
        return self.input_price == 0 and self.output_price == 0

    @property
    def usable(self) -> bool:
        """Can serve a turn here (the harness always sends tools)."""
        return self.tools

    @property
    def price(self) -> tuple[float, float] | None:
        if self.input_price is None or self.output_price is None:
            return None
        return (self.input_price, self.output_price)

    @property
    def label(self) -> str:
        bits: list[str] = []
        if self.context:
            bits.append(f"{_fmt_ctx(self.context)} ctx")
        if self.free:
            bits.append("free")
        elif self.price is not None:
            bits.append(f"{_fmt_price(self.input_price)}/{_fmt_price(self.output_price)}")
        if self.status == "beta":
            bits.append("beta")
        if not self.tools:
            bits.append("no tool use")
        name = self.name or self.id
        return f"{name} — {', '.join(bits)}" if bits else name


@dataclass(frozen=True)
class CatalogProvider:
    """One models.dev provider this harness can talk to."""

    mid: str                      # models.dev id ("deepseek")
    name: str
    env: tuple[str, ...] = ()
    api: str = ""                 # base URL; may hold ${VAR} placeholders
    wire: str = WIRE_OPENAI
    doc: str = ""
    models: tuple[CatalogModel, ...] = field(default=(), compare=False, repr=False)

    @property
    def id(self) -> str:
        """Jarvis provider id (``md:deepseek``)."""
        return PREFIX + self.mid

    @property
    def template_vars(self) -> tuple[str, ...]:
        return tuple(re.findall(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}", self.api))

    @property
    def key_vars(self) -> tuple[str, ...]:
        """Env vars that hold the API key (not the ones filling the URL)."""
        tpl = set(self.template_vars)
        keys = [e for e in self.env if e not in tpl]
        likely = [e for e in keys if re.search(r"KEY|TOKEN|PAT|SECRET", e)]
        return tuple(likely or keys)

    @property
    def local(self) -> bool:
        host = urllib.parse.urlparse(self.api.replace("${", "").replace("}", "")).hostname or ""
        return host in ("localhost", "127.0.0.1", "::1", "0.0.0.0")

    def model(self, model_id: str) -> CatalogModel | None:
        for m in self.models:
            if m.id == model_id:
                return m
        return None


# ── trimming the raw catalog ──────────────────────────────────────────────────


def _price(cost: dict | None, key: str) -> float | None:
    if not isinstance(cost, dict) or key not in cost:
        return None
    try:
        return float(cost[key])
    except (TypeError, ValueError):
        return None


def _model_wire(provider_wire: str, provider_npm: str, mid: str, raw: dict) -> str | None:
    """How this model is reached on its provider's client; None = it can't be."""
    over = raw.get("provider") if isinstance(raw.get("provider"), dict) else {}
    if over.get("api"):
        return None  # served from a different base URL than the provider's
    npm = over.get("npm") or provider_npm
    if provider_wire == WIRE_ANTHROPIC:
        return "chat" if npm == _ANTHROPIC else None
    if npm == _OPENAI_RESPONSES:
        if provider_npm == _OPENAI_RESPONSES:
            # OpenAI itself: chat-completions serves everything except the
            # Responses-only line (pro / codex / deep-research models).
            low = mid.lower()
            if "codex" in low or low.endswith("-pro") or "deep-research" in low:
                return "responses"
            return "chat"
        return "responses"  # a gateway that serves this model on /responses only
    if npm in (_OPENAI_COMPAT, "@openrouter/ai-sdk-provider") or npm == provider_npm:
        return "chat"
    return None


def _trim_model(provider_wire: str, provider_npm: str, raw: dict) -> dict | None:
    mid = raw.get("id")
    if not isinstance(mid, str) or not mid:
        return None
    if raw.get("status") == "deprecated":
        return None
    modalities = raw.get("modalities") or {}
    out = modalities.get("output") or ["text"]
    if "text" not in out:
        return None  # image / audio / embedding models
    wire = _model_wire(provider_wire, provider_npm, mid, raw)
    if wire is None:
        return None
    limit = raw.get("limit") or {}
    cost = raw.get("cost")
    inter = raw.get("interleaved")
    row: dict = {"id": mid}
    name = (raw.get("name") or "").strip()
    if name and name != mid:
        row["n"] = name
    if not raw.get("tool_call", True):
        row["t"] = 0
    if "image" in (modalities.get("input") or []):
        row["i"] = 1
    if raw.get("reasoning"):
        row["r"] = 1
    if isinstance(inter, dict) and inter.get("field") == "reasoning_content":
        row["rf"] = "reasoning_content"
    for src, dst in (("context", "c"), ("output", "o")):
        try:
            v = int(limit.get(src) or 0)
        except (TypeError, ValueError):
            v = 0
        if v:
            row[dst] = v
    pin, pout = _price(cost, "input"), _price(cost, "output")
    if pin is not None and pout is not None:
        row["p"] = [pin, pout]
    rel = raw.get("release_date") or raw.get("last_updated") or ""
    if isinstance(rel, str) and rel:
        row["d"] = rel
    if wire != "chat":
        row["w"] = wire
    if raw.get("status") == "beta":
        row["s"] = "beta"
    return row


def _provider_wire(mid: str, raw: dict) -> tuple[str, str] | None:
    """``(wire, base_url)`` for a provider, or None when no client fits."""
    npm = raw.get("npm") or ""
    api = (raw.get("api") or "").strip()
    if npm == _ANTHROPIC:
        # The Anthropic SDK appends /v1/messages itself.
        if api.rstrip("/").endswith("/v1"):
            return WIRE_ANTHROPIC, api.rstrip("/")[: -len("/v1")]
        return None
    if api:
        return WIRE_OPENAI, api.rstrip("/")
    known = KNOWN_BASE_URLS.get(mid)
    if known:
        return WIRE_OPENAI, known
    return None


def trim(raw: dict) -> dict:
    """The raw models.dev payload, cut down to what this harness uses."""
    out: dict[str, dict] = {}
    if not isinstance(raw, dict):
        return out
    for mid, prov in raw.items():
        if not isinstance(prov, dict) or not isinstance(mid, str) or mid in SKIP_IDS:
            continue
        native = mid in NATIVE_IDS
        if native:
            wire, api = WIRE_OPENAI, ""
        else:
            found = _provider_wire(mid, prov)
            if found is None:
                continue
            wire, api = found
        npm = prov.get("npm") or ""
        if native and mid == "anthropic":
            wire_for_models, npm_for_models = WIRE_ANTHROPIC, _ANTHROPIC
        else:
            wire_for_models, npm_for_models = wire, npm
        models = []
        for m in (prov.get("models") or {}).values():
            if isinstance(m, dict):
                row = _trim_model(wire_for_models, npm_for_models, m)
                if row:
                    models.append(row)
        if not models:
            continue
        entry: dict = {
            "name": prov.get("name") or mid,
            "env": [e for e in (prov.get("env") or []) if isinstance(e, str)],
            "models": models,
        }
        if native:
            entry["native"] = NATIVE_IDS[mid]
        else:
            entry["api"] = api
            entry["wire"] = wire
        if prov.get("doc"):
            entry["doc"] = prov["doc"]
        out[mid] = entry
    return out


def _model_from_row(row: dict) -> CatalogModel:
    price = row.get("p") or [None, None]
    return CatalogModel(
        id=row["id"],
        name=row.get("n") or row["id"],
        tools=bool(row.get("t", 1)),
        images=bool(row.get("i")),
        reasoning=bool(row.get("r")),
        context=int(row.get("c") or 0),
        output=int(row.get("o") or 0),
        input_price=price[0],
        output_price=price[1],
        release=row.get("d") or "",
        wire=row.get("w") or "chat",
        reasoning_field=row.get("rf") or "",
        status=row.get("s") or "",
    )


def _sort_key(m: CatalogModel):
    # Models that can run here first, newest first, then by name.
    return (not m.usable, _neg_date(m.release), (m.name or m.id).lower())


def _neg_date(d: str) -> tuple:
    digits = [int(x) for x in re.findall(r"\d+", d or "")][:3]
    while len(digits) < 3:
        digits.append(0)
    return tuple(-x for x in digits)


# ── cache (disk + memo) ───────────────────────────────────────────────────────

CACHE_FILE = catalog_cache.CACHE_DIR / "models_dev.json"

_lock = threading.Lock()
_memo: dict = {"mtime": None, "path": None, "providers": {}, "native": {}, "meta": {}}


def _read_cache_file() -> dict | None:
    try:
        raw = json.loads(CACHE_FILE.read_text())
    except (OSError, ValueError):
        return None
    if not isinstance(raw, dict) or raw.get("schema") != SCHEMA:
        return None
    if not isinstance(raw.get("providers"), dict):
        return None
    return raw


def _write_cache_file(providers: dict, etag: str) -> None:
    payload = {"schema": SCHEMA, "fetched_at": time.time(), "etag": etag, "providers": providers}
    try:
        CACHE_FILE.parent.mkdir(parents=True, exist_ok=True)
        tmp = CACHE_FILE.with_suffix(".tmp")
        tmp.write_text(json.dumps(payload, separators=(",", ":")))
        tmp.replace(CACHE_FILE)
    except (OSError, TypeError, ValueError):
        pass


def _touch_cache_file() -> None:
    """A 304: the catalog is unchanged — just restart its freshness clock."""
    raw = _read_cache_file()
    if raw is None:
        return
    _write_cache_file(raw["providers"], raw.get("etag") or "")


def _load() -> tuple[dict[str, CatalogProvider], dict[str, list[CatalogModel]]]:
    """Parsed cache, memoised on the file's mtime (cheap to call often)."""
    if not enabled():
        return {}, {}
    try:
        mtime = CACHE_FILE.stat().st_mtime_ns
    except OSError:
        mtime = None
    with _lock:
        if _memo["mtime"] == mtime and _memo["path"] == str(CACHE_FILE):
            return _memo["providers"], _memo["native"]
    raw = _read_cache_file() if mtime is not None else None
    providers: dict[str, CatalogProvider] = {}
    native: dict[str, list[CatalogModel]] = {}
    for mid, entry in ((raw or {}).get("providers") or {}).items():
        if not isinstance(entry, dict):
            continue
        try:
            models = sorted(
                (_model_from_row(r) for r in entry.get("models") or [] if isinstance(r, dict) and r.get("id")),
                key=_sort_key,
            )
        except (TypeError, ValueError, KeyError):
            continue
        if entry.get("native"):
            native[str(entry["native"])] = models
            continue
        providers[PREFIX + mid] = CatalogProvider(
            mid=mid,
            name=str(entry.get("name") or mid),
            env=tuple(entry.get("env") or ()),
            api=str(entry.get("api") or ""),
            wire=str(entry.get("wire") or WIRE_OPENAI),
            doc=str(entry.get("doc") or ""),
            models=tuple(models),
        )
    with _lock:
        _memo.update(mtime=mtime, path=str(CACHE_FILE), providers=providers, native=native,
                     meta={"fetched_at": (raw or {}).get("fetched_at") or 0,
                           "etag": (raw or {}).get("etag") or ""})
    _register_labels(providers)
    return providers, native


def _register_labels(providers: dict[str, CatalogProvider]) -> None:
    """Make catalog providers print by name wherever labels are looked up."""
    try:
        from ..constants import providers as p
    except Exception:
        return
    for pid, prov in providers.items():
        p.PROVIDER_LABELS.setdefault(pid, prov.name)
        p.MODEL_SOURCE_LABELS.setdefault(pid, prov.name)


def store(trimmed: dict, etag: str = "") -> None:
    """Persist an already-trimmed catalog (also what tests use to seed one)."""
    _write_cache_file(trimmed, etag)


def cache_is_fresh() -> bool:
    if not enabled():
        return True  # nothing to refresh
    raw = _read_cache_file()
    if raw is None:
        return False
    try:
        age = time.time() - float(raw.get("fetched_at") or 0)
    except (TypeError, ValueError):
        return False
    return age < catalog_cache._ttl()


def fetched_at() -> float:
    _load()
    return float(_memo["meta"].get("fetched_at") or 0)


# ── network ───────────────────────────────────────────────────────────────────


def _http_get(url: str, headers: dict[str, str], timeout: float) -> tuple[int, bytes, dict[str, str]]:
    """``(status, body, headers)``; a 304 comes back as status 304, empty body."""
    req = urllib.request.Request(url, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            body = resp.read()
            hdrs = {k.lower(): v for k, v in resp.headers.items()}
            return resp.status, body, hdrs
    except urllib.error.HTTPError as exc:
        if exc.code == 304:
            return 304, b"", {}
        raise


def refresh(timeout: float = DEFAULT_TIMEOUT, *, force: bool = False) -> bool:
    """Fetch models.dev into the cache. True when the cache is good afterwards.

    Network — call it from a worker thread, never the UI thread. An unchanged
    catalog costs one 304; ``force=True`` skips the ETag and refetches.
    """
    if not enabled():
        return False
    raw_cache = _read_cache_file()
    headers = dict(REQUEST_HEADERS)
    etag = (raw_cache or {}).get("etag") or ""
    if etag and not force:
        headers["If-None-Match"] = etag
    try:
        status, body, hdrs = _http_get(API_URL, headers, timeout)
    except Exception:
        return raw_cache is not None
    if status == 304:
        _touch_cache_file()
        _load()  # parse here, on the worker, not on the UI thread's next read
        return True
    if status != 200 or not body:
        return raw_cache is not None
    try:
        if hdrs.get("content-encoding", "").lower() == "gzip" or body[:2] == b"\x1f\x8b":
            body = gzip.decompress(body)
        payload = json.loads(body)
    except (OSError, ValueError, EOFError):
        return raw_cache is not None
    trimmed = trim(payload)
    if not trimmed:
        return raw_cache is not None
    _write_cache_file(trimmed, hdrs.get("etag") or "")
    _load()
    return True


# ── lookups (cache only — safe on the UI thread) ─────────────────────────────


def is_catalog_provider(provider: str | None) -> bool:
    return bool(provider) and str(provider).startswith(PREFIX)


def provider_id(name: str) -> str:
    """``deepseek`` / ``md:deepseek`` → ``md:deepseek`` ("" when unknown)."""
    n = (name or "").strip()
    if not n:
        return ""
    pid = n if n.startswith(PREFIX) else PREFIX + n
    return pid if pid in providers() else ""


def providers() -> dict[str, CatalogProvider]:
    """Every catalog provider this harness can use, keyed ``md:<id>``."""
    return _load()[0]


def get_provider(provider: str) -> CatalogProvider | None:
    if not is_catalog_provider(provider):
        return None
    return providers().get(provider)


def models(provider: str) -> list[CatalogModel]:
    p = get_provider(provider)
    return list(p.models) if p else []


def get_model(provider: str, model_id: str) -> CatalogModel | None:
    p = get_provider(provider)
    return p.model(model_id) if p else None


def native_models(provider: str) -> list[CatalogModel]:
    """models.dev's view of a built-in provider (``openrouter``, ``opencode``, …)."""
    return list(_load()[1].get(provider) or [])


def native_model(provider: str, model_id: str) -> CatalogModel | None:
    for m in native_models(provider):
        if m.id == model_id:
            return m
    return None


def default_model(provider: str) -> str:
    """A sensible first model: the newest that can call tools, skipping
    preview / pro tiers when there is anything else."""
    ms = [m for m in models(provider) if m.usable]
    if not ms:
        ms = models(provider)
    if not ms:
        return ""
    plain = [m for m in ms if not re.search(r"(preview|-pro\b|exp|beta)", m.id.lower()) and m.status != "beta"]
    return (plain or ms)[0].id


def base_url(provider: CatalogProvider) -> str | None:
    """The provider's base URL with ``${VAR}`` filled from the environment."""
    url = provider.api
    for var in provider.template_vars:
        val = os.getenv(var, "").strip()
        if not val:
            return None
        url = url.replace("${" + var + "}", val.rstrip("/"))
    return url


def missing_vars(provider: CatalogProvider) -> list[str]:
    return [v for v in provider.template_vars if not os.getenv(v, "").strip()]


def shared_env_vars() -> frozenset[str]:
    """Key env vars more than one catalog provider reads (MINIMAX_API_KEY …)."""
    provs = providers()
    with _lock:
        cached = _memo.get("shared")
        if cached is not None and cached[0] is provs:
            return cached[1]
    seen: dict[str, int] = {}
    for p in provs.values():
        for var in p.key_vars:
            seen[var] = seen.get(var, 0) + 1
    shared = frozenset(v for v, n in seen.items() if n > 1)
    with _lock:
        _memo["shared"] = (provs, shared)
    return shared


def find_model(model_id: str, provider: str = "") -> CatalogModel | None:
    """The catalog entry for ``model_id`` on ``provider`` (catalog or built-in)."""
    if is_catalog_provider(provider):
        return get_model(provider, model_id)
    if provider:
        return native_model(provider, model_id)
    return None
