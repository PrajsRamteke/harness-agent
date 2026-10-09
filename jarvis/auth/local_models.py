"""Models that run on this computer (or on a box on the LAN).

Provider ids are ``local:<server>``. Two kinds of server:

  * **Detected runtimes** (``RUNTIMES``): Ollama, LM Studio, llama.cpp, vLLM,
    Jan, GPT4All, KoboldCpp, Docker Model Runner, text-generation-webui — each
    probed on its usual port. Nothing to set up: start the app and its models
    show up in ``/model``. A runtime can be left out of detection (``off``).
  * **Added servers**: any Ollama or OpenAI-compatible URL the user gives
    (another machine, a non-default port, an API key).

Discovery never runs on the UI thread: ``scan()`` probes every server at once
(short timeouts, ~1 s worst case) and writes ``model_catalog/local_models.json``;
everything else (``models``, ``get_model``, ``public``…) reads that cache.
A probe checks the *shape* of the answer, not just that a port is open —
macOS's AirPlay receiver answers on :5000.

Per model it records what the request path needs: the context window, whether
it can call tools (Jarvis always sends tools — a model without them is labelled
``no tool use`` and sorted last, as on every other provider), vision and
thinking. Ollama says all of that (``/api/show`` capabilities); the
OpenAI-compatible servers say less, and what they don't say stays unknown
(``None``) — never read as "unsupported".

Ollama is spoken to natively (``ollama_client.py``): its OpenAI endpoint can't
set ``num_ctx`` and Ollama defaults to a 4K window on most machines, which
silently cuts off Jarvis's own system prompt. The window Jarvis asks for is
the setting ``context`` (default 32K), capped at what the model supports.

Config: ``~/.config/harness-agent/local_models.json`` (600 — it may hold an
API key for an added server). ``HARNESS_LOCAL_MODELS=0`` turns all of it off.
"""
from __future__ import annotations

import http.client
import json
import os
import re
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from typing import Any

from ..constants.paths import CONFIG_DIR

LOCAL_PREFIX = "local:"

CONFIG_FILE = CONFIG_DIR / "local_models.json"
CACHE_FILE = CONFIG_DIR / "model_catalog" / "local_models.json"

# Context windows the UIs offer; the default suits a 16 GB laptop running an
# 8B model (an agent's system prompt + tools alone is ~10K tokens).
CONTEXT_CHOICES = (8_192, 16_384, 32_768, 65_536, 131_072)
DEFAULT_CONTEXT = 32_768
# Reply room asked for per request: the window minus this is what a prompt
# may use, so it stays small next to a local window.
MAX_REPLY_TOKENS = 8_192

PROBE_TIMEOUT = 0.8      # an idle local server answers in milliseconds
SHOW_TIMEOUT = 3.0       # /api/show can read a big model's metadata from disk
SCAN_TTL = 60.0          # seconds a scan counts as fresh for the pickers

STATUS_ONLINE = "online"
STATUS_OFFLINE = "offline"
STATUS_AUTH = "auth"       # answered, wants an API key
STATUS_ERROR = "error"     # answered, but not like a model server

KIND_OLLAMA = "ollama"
KIND_OPENAI = "openai"


def enabled() -> bool:
    return os.getenv("HARNESS_LOCAL_MODELS", "1").strip() != "0"


def is_local_provider(provider: str | None) -> bool:
    return bool(provider) and str(provider).startswith(LOCAL_PREFIX)


def provider_id(server_id: str) -> str:
    """``ollama`` → ``local:ollama`` (an id that already has the prefix is kept)."""
    return server_id if is_local_provider(server_id) else f"{LOCAL_PREFIX}{server_id}"


def server_id(provider: str) -> str:
    return provider[len(LOCAL_PREFIX):] if is_local_provider(provider) else provider


# ─── runtimes Jarvis looks for ────────────────────────────────────────────


@dataclass(frozen=True)
class Runtime:
    id: str
    name: str
    url: str            # root, no API path
    kind: str           # KIND_OLLAMA | KIND_OPENAI
    blurb: str
    start: str          # the command that starts its server ("" = app only)
    site: str           # where to get it
    mark: str
    api_path: str = "/v1"
    env: str = ""       # env var that moves it (OLLAMA_HOST)
    pull: str = ""      # how to get a model onto it
    how: str = ""       # a plain-words hint next to (or instead of) ``start``

    @property
    def start_hint(self) -> str:
        """``ollama serve (or just open the Ollama app)`` — one line for errors."""
        if self.start and self.how:
            return f"{self.start} ({self.how})"
        return self.start or self.how


RUNTIMES: tuple[Runtime, ...] = (
    Runtime("ollama", "Ollama", "http://127.0.0.1:11434", KIND_OLLAMA,
            "The easiest way to run open models", "ollama serve",
            "https://ollama.com/download", "Ol", env="OLLAMA_HOST",
            pull="ollama pull qwen3", how="or just open the Ollama app"),
    Runtime("lmstudio", "LM Studio", "http://127.0.0.1:1234", KIND_OPENAI,
            "Desktop app for GGUF and MLX models", "lms server start",
            "https://lmstudio.ai", "LM", pull="lms get qwen3-8b",
            how="or Developer → Start Server in the app"),
    Runtime("llamacpp", "llama.cpp", "http://127.0.0.1:8080", KIND_OPENAI,
            "llama-server, the reference GGUF server",
            "llama-server -m model.gguf --jinja -c 32768",
            "https://github.com/ggml-org/llama.cpp", "ll",
            how="--jinja turns on tool calls"),
    Runtime("vllm", "vLLM", "http://127.0.0.1:8000", KIND_OPENAI,
            "High-throughput GPU serving",
            "vllm serve <model> --enable-auto-tool-choice --tool-call-parser hermes",
            "https://docs.vllm.ai", "vL", how="the tool flags let it call tools"),
    Runtime("jan", "Jan", "http://127.0.0.1:1337", KIND_OPENAI,
            "Open-source ChatGPT alternative", "",
            "https://jan.ai", "Ja", how="Settings → Local API Server → Start Server"),
    Runtime("gpt4all", "GPT4All", "http://127.0.0.1:4891", KIND_OPENAI,
            "Private desktop chat by Nomic", "",
            "https://gpt4all.io", "G4", how="Settings → Enable Local API Server"),
    Runtime("koboldcpp", "KoboldCpp", "http://127.0.0.1:5001", KIND_OPENAI,
            "One-file GGUF runner", "koboldcpp --model model.gguf --contextsize 32768",
            "https://github.com/LostRuins/koboldcpp", "Ko"),
    Runtime("docker", "Docker Model Runner", "http://127.0.0.1:12434", KIND_OPENAI,
            "Models from Docker Desktop", "docker desktop enable model-runner --tcp 12434",
            "https://docs.docker.com/ai/model-runner/", "Dk", api_path="/engines/v1",
            pull="docker model pull ai/qwen3", how="or Settings → AI → host-side TCP support"),
    Runtime("textgen", "text-generation-webui", "http://127.0.0.1:5000", KIND_OPENAI,
            "oobabooga's web UI", "python server.py --api",
            "https://github.com/oobabooga/text-generation-webui", "TG"),
)
RUNTIME_BY_ID = {r.id: r for r in RUNTIMES}


def _ollama_root_from_env() -> str:
    """``OLLAMA_HOST`` as a URL: ``0.0.0.0:11434``, ``host``, ``http://h:p``."""
    raw = os.getenv("OLLAMA_HOST", "").strip()
    if not raw:
        return ""
    if "://" not in raw:
        raw = f"http://{raw}"
    try:
        u = urllib.parse.urlsplit(raw)
    except ValueError:
        return ""
    host = u.hostname or "127.0.0.1"
    if host in ("0.0.0.0", "::", ""):
        host = "127.0.0.1"
    if ":" in host:
        host = f"[{host}]"
    return f"{u.scheme or 'http'}://{host}:{u.port or 11434}"


# ─── servers (detected + added) ───────────────────────────────────────────


@dataclass(frozen=True)
class LocalServer:
    id: str
    name: str
    url: str            # Ollama: root · OpenAI kind: the API base (…/v1)
    kind: str
    key: str = ""
    custom: bool = False
    runtime: Runtime | None = None

    @property
    def provider(self) -> str:
        return provider_id(self.id)

    @property
    def root(self) -> str:
        """Host part of the URL, for runtime-specific endpoints (/props, /api/v0)."""
        if self.kind == KIND_OLLAMA:
            return self.url
        if self.runtime is not None:
            return self.runtime.url if not self.custom else _strip_api(self.url)
        return _strip_api(self.url)

    @property
    def api_base(self) -> str:
        """OpenAI-compatible base URL (``…/v1``)."""
        return f"{self.url}/v1" if self.kind == KIND_OLLAMA else self.url

    @property
    def host(self) -> str:
        try:
            u = urllib.parse.urlsplit(self.url)
            return u.netloc or self.url
        except ValueError:
            return self.url

    @property
    def remote(self) -> bool:
        """Runs on another machine (an added server on the LAN)."""
        try:
            host = (urllib.parse.urlsplit(self.url).hostname or "").lower()
        except ValueError:
            return False
        return host not in ("127.0.0.1", "localhost", "::1", "0.0.0.0", "")


def _strip_api(url: str) -> str:
    return re.sub(r"/(engines/)?v1/?$", "", url.rstrip("/"))


_lock = threading.RLock()


def _read_config() -> dict[str, Any]:
    try:
        raw = json.loads(CONFIG_FILE.read_text())
    except (OSError, ValueError):
        return {}
    return raw if isinstance(raw, dict) else {}


def _write_config(cfg: dict[str, Any]) -> None:
    CONFIG_FILE.parent.mkdir(parents=True, exist_ok=True)
    tmp = CONFIG_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(cfg, indent=2))
    try:
        os.chmod(tmp, 0o600)
    except OSError:
        pass
    tmp.replace(CONFIG_FILE)


def detection_off() -> set[str]:
    """Runtimes the user took out of detection."""
    return {str(x) for x in _read_config().get("off") or () if isinstance(x, str)}


def context_setting() -> int:
    """The window Jarvis asks a local model for (Ollama ``num_ctx``)."""
    try:
        n = int(_read_config().get("context") or DEFAULT_CONTEXT)
    except (TypeError, ValueError):
        n = DEFAULT_CONTEXT
    return max(2_048, min(n, 1_048_576))


def set_context(tokens: int) -> int:
    with _lock:
        cfg = _read_config()
        cfg["context"] = max(2_048, min(int(tokens), 1_048_576))
        _write_config(cfg)
    _bump()
    return cfg["context"]


def set_detect(runtime_id: str, on: bool) -> bool:
    if runtime_id not in RUNTIME_BY_ID:
        return False
    with _lock:
        cfg = _read_config()
        off = [x for x in cfg.get("off") or () if x != runtime_id]
        if not on:
            off.append(runtime_id)
        cfg["off"] = sorted(set(off))
        _write_config(cfg)
    _bump()
    return True


def _builtin_servers() -> list[LocalServer]:
    off = detection_off()
    out: list[LocalServer] = []
    for rt in RUNTIMES:
        if rt.id in off:
            continue
        url = rt.url
        if rt.env == "OLLAMA_HOST":
            url = _ollama_root_from_env() or url
        if rt.kind == KIND_OPENAI:
            url = f"{url}{rt.api_path}"
        out.append(LocalServer(rt.id, rt.name, url, rt.kind, runtime=rt))
    return out


def _custom_servers() -> list[LocalServer]:
    out: list[LocalServer] = []
    for row in _read_config().get("servers") or ():
        if not isinstance(row, dict):
            continue
        sid, url = str(row.get("id") or ""), str(row.get("url") or "")
        if not sid or not url:
            continue
        kind = KIND_OLLAMA if row.get("kind") == KIND_OLLAMA else KIND_OPENAI
        out.append(LocalServer(sid, str(row.get("name") or sid), url.rstrip("/"), kind,
                               key=str(row.get("key") or ""), custom=True))
    return out


def servers(*, include_off: bool = False) -> list[LocalServer]:
    """Every server Jarvis knows: detected runtimes, then added ones."""
    if not enabled():
        return []
    builtin = _builtin_servers()
    if include_off:
        have = {s.id for s in builtin}
        for rt in RUNTIMES:
            if rt.id not in have:
                url = rt.url + (rt.api_path if rt.kind == KIND_OPENAI else "")
                builtin.append(LocalServer(rt.id, rt.name, url, rt.kind, runtime=rt))
    return builtin + _custom_servers()


def get_server(provider: str) -> LocalServer | None:
    sid = server_id(provider)
    for s in servers(include_off=True):
        if s.id == sid:
            return s
    return None


def _slug(name: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")
    return s[:32] or "server"


def normalize_url(raw: str) -> str:
    """``192.168.1.4:11434`` → ``http://192.168.1.4:11434``; trailing / dropped."""
    s = (raw or "").strip().strip("'\"")
    if not s:
        return ""
    if "://" not in s:
        s = f"http://{s}"
    try:
        u = urllib.parse.urlsplit(s)
    except ValueError:
        return ""
    if u.scheme not in ("http", "https") or not u.hostname or re.search(r"\s", u.netloc):
        return ""
    try:
        u.port  # a bad port ("host:abc") raises here
    except ValueError:
        return ""
    path = re.sub(r"/(chat/completions|models|api/tags|api/chat)/?$", "", u.path.rstrip("/"))
    return urllib.parse.urlunsplit((u.scheme, u.netloc, path, "", "")).rstrip("/")


# ─── HTTP ────────────────────────────────────────────────────────────────


class ProbeError(Exception):
    def __init__(self, status: str, message: str):
        super().__init__(message)
        self.status = status


def _http_json(url: str, *, data: dict | None = None, key: str = "",
               timeout: float | None = None) -> Any:
    """GET (or POST ``data``) a JSON endpoint. Raises :class:`ProbeError`."""
    body = json.dumps(data).encode() if data is not None else None
    req = urllib.request.Request(url, data=body, method="POST" if body else "GET")
    req.add_header("Accept", "application/json")
    if body:
        req.add_header("Content-Type", "application/json")
    if key:
        req.add_header("Authorization", f"Bearer {key}")
    try:
        with urllib.request.urlopen(req, timeout=timeout or PROBE_TIMEOUT) as resp:
            raw = resp.read(4_000_000)
    except urllib.error.HTTPError as e:
        try:
            text = e.read(2000).decode("utf-8", "replace").lower()
        except Exception:
            text = ""
        # 401, or a 403 that talks about keys — macOS AirPlay answers a bare
        # 403 on :5000, which is not a model server asking for a key.
        if e.code == 401 or (e.code == 403 and any(w in text for w in ("key", "auth", "token"))):
            raise ProbeError(STATUS_AUTH, "asks for an API key") from None
        raise ProbeError(STATUS_ERROR, f"HTTP {e.code}") from None
    except (urllib.error.URLError, TimeoutError, OSError, ValueError, http.client.HTTPException) as e:
        reason = getattr(e, "reason", e)
        raise ProbeError(STATUS_OFFLINE, _offline_words(reason)) from None
    try:
        return json.loads(raw.decode("utf-8", "replace"))
    except ValueError:
        raise ProbeError(STATUS_ERROR, "didn't answer like a model server") from None


def _offline_words(reason: object) -> str:
    text = str(reason).lower()
    if "refused" in text or "61" in text or "111" in text:
        return "not running"
    if "timed out" in text or "timeout" in text:
        return "didn't answer in time"
    if "name" in text and ("resolve" in text or "known" in text):
        return "host not found"
    return "not reachable"


# ─── probing ─────────────────────────────────────────────────────────────


def _fmt_ctx(n: int | None) -> str:
    if not n:
        return ""
    return f"{n // 1024}K" if n >= 1024 else str(n)


def _fmt_size(n: int | None) -> str:
    if not n:
        return ""
    gb = n / 1e9
    return f"{gb:.1f} GB" if gb >= 1 else f"{n / 1e6:.0f} MB"


_NOT_CHAT = re.compile(r"(embed|embedding|rerank|whisper|tts|bge-|e5-|minilm|clip\b)", re.I)


def _ctx_from_info(info: dict) -> int | None:
    if not isinstance(info, dict):
        return None
    for k, v in info.items():
        if k.endswith(".context_length"):
            try:
                return int(v)
            except (TypeError, ValueError):
                return None
    return None


def _gpt_oss(model_id: str, family: str = "") -> bool:
    return "gpt-oss" in model_id.lower() or family.lower() == "gptoss"


def _probe_ollama(s: LocalServer, show_cache: dict, t: float) -> dict:
    ver = _http_json(f"{s.url}/api/version", key=s.key, timeout=t)
    if not isinstance(ver, dict) or "version" not in ver:
        raise ProbeError(STATUS_ERROR, "didn't answer like Ollama")
    tags = _http_json(f"{s.url}/api/tags", key=s.key, timeout=max(t, 2.0))
    rows = tags.get("models") if isinstance(tags, dict) else None
    if not isinstance(rows, list):
        raise ProbeError(STATUS_ERROR, "didn't answer like Ollama")
    loaded: dict[str, int | None] = {}
    try:
        ps = _http_json(f"{s.url}/api/ps", key=s.key, timeout=t)
        for r in (ps or {}).get("models") or ():
            if isinstance(r, dict) and r.get("name"):
                loaded[r["name"]] = r.get("context_length")
    except ProbeError:
        pass

    def show(row: dict) -> dict:
        name, digest = row.get("name") or row.get("model") or "", row.get("digest") or ""
        hit = show_cache.get(digest) if digest else None
        if isinstance(hit, dict):
            return hit
        try:
            info = _http_json(f"{s.url}/api/show", data={"model": name}, key=s.key,
                              timeout=SHOW_TIMEOUT)
        except ProbeError:
            return {}
        out = {
            "caps": list(info.get("capabilities") or []) if isinstance(info, dict) else [],
            "ctx": _ctx_from_info(info.get("model_info") or {}) if isinstance(info, dict) else None,
        }
        if digest and out["caps"]:
            show_cache[digest] = out
        return out

    rows = [r for r in rows if isinstance(r, dict)]
    with ThreadPoolExecutor(max_workers=8) as pool:
        shows = list(pool.map(show, rows))
    models: list[dict] = []
    for row, info in zip(rows, shows):
        mid = row.get("name") or row.get("model") or ""
        details = row.get("details") or {}
        caps = info.get("caps") or []
        known = bool(caps)
        if known and "completion" not in caps:
            continue  # embedding-only
        if not known and _NOT_CHAT.search(mid):
            continue
        family = str(details.get("family") or "")
        think = ("thinking" in caps) if known else None
        models.append({
            "id": mid,
            "family": family,
            "params": str(details.get("parameter_size") or ""),
            "quant": str(details.get("quantization_level") or ""),
            "size": int(row.get("size") or 0),
            "ctx": info.get("ctx"),
            "tools": ("tools" in caps) if known else None,
            "vision": ("vision" in caps) if known else None,
            "think": think,
            "levels": ["low", "medium", "high"] if think and _gpt_oss(mid, family) else [],
            "loaded": mid in loaded,
            "cloud": bool(row.get("remote_host")) or mid.endswith("-cloud") or ":cloud" in mid,
        })
    return {"version": str(ver.get("version") or ""), "models": models}


def _probe_openai(s: LocalServer, t: float) -> dict:
    data = _http_json(f"{s.url}/models", key=s.key, timeout=max(t, 1.5) if s.custom else t)
    rows = data.get("data") if isinstance(data, dict) else None
    if not isinstance(rows, list):
        raise ProbeError(STATUS_ERROR, "didn't answer like an OpenAI-compatible server")
    rows = [r for r in rows if isinstance(r, dict) and r.get("id")]
    rid = s.runtime.id if s.runtime else ""
    owners = {str(r.get("owned_by") or "").lower() for r in rows}

    extra: dict[str, dict] = {}
    # LM Studio's own listing knows type (llm / vlm / embeddings), state,
    # context and quantisation.
    if rid == "lmstudio" or "organization_owner" in owners or s.custom:
        try:
            v0 = _http_json(f"{s.root}/api/v0/models", key=s.key, timeout=t)
            for r in (v0 or {}).get("data") or ():
                if isinstance(r, dict) and r.get("id"):
                    extra[r["id"]] = r
        except ProbeError:
            pass
    props: dict = {}
    if rid == "llamacpp" or "llamacpp" in owners:
        try:
            got = _http_json(f"{s.root}/props", key=s.key, timeout=t)
            props = got if isinstance(got, dict) else {}
        except ProbeError:
            props = {}

    models: list[dict] = []
    for r in rows:
        mid = str(r["id"])
        lm = extra.get(mid) or {}
        typ = str(lm.get("type") or "").lower()
        if typ == "embeddings" or (not typ and _NOT_CHAT.search(mid)):
            continue
        meta = r.get("meta") if isinstance(r.get("meta"), dict) else {}
        ctx = (lm.get("loaded_context_length") or lm.get("max_context_length")
               or r.get("max_model_len") or r.get("context_length"))
        if props:
            gen = props.get("default_generation_settings") or {}
            ctx = gen.get("n_ctx") or props.get("n_ctx") or meta.get("n_ctx_train") or ctx
        try:
            ctx = int(ctx) if ctx else None
        except (TypeError, ValueError):
            ctx = None
        caps = lm.get("capabilities") if isinstance(lm.get("capabilities"), list) else None
        vision: bool | None = None
        if typ:
            vision = typ == "vlm"
        elif props.get("modalities"):
            vision = bool((props.get("modalities") or {}).get("vision"))
        params = ""
        if meta.get("n_params"):
            params = f"{meta['n_params'] / 1e9:.1f}B"
        models.append({
            "id": mid,
            "family": str(lm.get("arch") or ""),
            "params": params,
            "quant": str(lm.get("quantization") or ""),
            "size": int(meta.get("size") or 0),
            "ctx": ctx,
            "tools": ("tool_use" in caps) if caps is not None else None,
            "vision": vision,
            "think": None,
            "levels": [],
            "loaded": (lm.get("state") == "loaded") if lm else None,
            "cloud": False,
        })
    return {"version": "", "models": models}


def probe(s: LocalServer, show_cache: dict | None = None, timeout: float | None = None) -> dict:
    """One server's status + models. Never raises."""
    t0 = time.monotonic()
    out: dict[str, Any] = {"id": s.id, "status": STATUS_OFFLINE, "error": "",
                           "version": "", "models": [], "checked": time.time(), "ms": 0}
    try:
        t = timeout or PROBE_TIMEOUT
        got = _probe_ollama(s, show_cache if show_cache is not None else {}, t) \
            if s.kind == KIND_OLLAMA else _probe_openai(s, t)
        out.update(got)
        out["status"] = STATUS_ONLINE
    except ProbeError as e:
        out["status"], out["error"] = e.status, str(e)
    except Exception as e:  # a server answering nonsense must not break a scan
        out["status"], out["error"] = STATUS_ERROR, f"{type(e).__name__}: {e}"[:200]
    if not s.custom and out["status"] == STATUS_ERROR:
        # Something else owns a runtime's usual port: not that runtime.
        out["status"], out["error"] = STATUS_OFFLINE, "not running"
    out["ms"] = int((time.monotonic() - t0) * 1000)
    return out


def detect_kind(url: str, key: str = "") -> tuple[str, str]:
    """(kind, api url) for a URL the user typed — Ollama or OpenAI-compatible.

    Tries Ollama's /api/version, then ``<url>/models`` and ``<url>/v1/models``.
    Raises :class:`ProbeError` with the best reason when nothing answers.
    """
    url = url.rstrip("/")
    last = ProbeError(STATUS_OFFLINE, "not reachable")
    root = _strip_api(url)
    try:
        ver = _http_json(f"{root}/api/version", key=key, timeout=2.0)
        if isinstance(ver, dict) and "version" in ver:
            return KIND_OLLAMA, root
    except ProbeError as e:
        last = e
        if e.status == STATUS_OFFLINE:
            raise
    for base in dict.fromkeys((url, f"{root}/v1")):
        try:
            data = _http_json(f"{base}/models", key=key, timeout=2.0)
        except ProbeError as e:
            if e.status == STATUS_AUTH:
                raise
            last = e
            continue
        if isinstance(data, dict) and isinstance(data.get("data"), list):
            return KIND_OPENAI, base
    if last.status == STATUS_ERROR:
        raise ProbeError(STATUS_ERROR, "answered, but not like Ollama or an OpenAI-compatible server")
    raise last


# ─── cache ───────────────────────────────────────────────────────────────

_memo: dict[str, Any] = {"stamp": None, "data": None}
_gen = 0


def _bump() -> None:
    global _gen
    _gen += 1
    _memo["stamp"] = None


def _read_cache() -> dict:
    try:
        st = os.stat(CACHE_FILE)
        stamp = (str(CACHE_FILE), st.st_mtime_ns, st.st_size, _gen)
    except OSError:
        return {}
    if _memo["stamp"] == stamp and isinstance(_memo["data"], dict):
        return _memo["data"]
    try:
        raw = json.loads(CACHE_FILE.read_text())
    except (OSError, ValueError):
        raw = {}
    data = raw if isinstance(raw, dict) else {}
    _memo.update(stamp=stamp, data=data)
    return data


def _write_cache(data: dict) -> None:
    try:
        CACHE_FILE.parent.mkdir(parents=True, exist_ok=True)
        tmp = CACHE_FILE.with_suffix(".tmp")
        tmp.write_text(json.dumps(data))
        tmp.replace(CACHE_FILE)
    except OSError:
        pass
    _memo["stamp"] = None


def cache_file_stamp() -> tuple:
    try:
        st = os.stat(CACHE_FILE)
        return (st.st_mtime_ns, st.st_size, _gen)
    except OSError:
        return (0, 0, _gen)


_scan_lock = threading.Lock()


def scan(*, only: str | None = None, timeout: float | None = None) -> dict[str, dict]:
    """Probe every server (or ``only`` that one), save and return the results.

    Network: call it from a worker thread. Concurrent calls share one scan.
    """
    if not enabled():
        return {}
    targets = [s for s in servers() if only is None or s.id == server_id(only)]
    if only is not None and not targets:
        s = get_server(only)
        targets = [s] if s is not None else []
    with _scan_lock:
        data = dict(_read_cache())
        show_cache = dict(data.get("show") or {})
        with ThreadPoolExecutor(max_workers=max(1, len(targets))) as pool:
            results = list(pool.map(lambda s: probe(s, show_cache, timeout), targets))
        found = dict(data.get("servers") or {}) if only else {}
        for r in results:
            prev = found.get(r["id"]) or (data.get("servers") or {}).get(r["id"]) or {}
            if r["status"] != STATUS_ONLINE and prev.get("models"):
                # Keep the last list a stopped server had, so a saved model
                # still shows (as offline) instead of vanishing.
                r["last_models"] = prev.get("models") or prev.get("last_models") or []
                r["last_seen"] = prev.get("checked") if prev.get("status") == STATUS_ONLINE \
                    else prev.get("last_seen")
            elif r["status"] != STATUS_ONLINE and prev.get("last_models"):
                r["last_models"] = prev["last_models"]
                r["last_seen"] = prev.get("last_seen")
            found[r["id"]] = r
        known = {s.id for s in servers(include_off=True)}
        data = {
            "scanned_at": time.time(),
            "servers": {k: v for k, v in found.items() if k in known},
            "show": dict(list(show_cache.items())[-400:]),
        }
        _write_cache(data)
    return data["servers"]


def scan_is_fresh() -> bool:
    try:
        return time.time() - float(_read_cache().get("scanned_at") or 0) < SCAN_TTL
    except (TypeError, ValueError):
        return False


def cached() -> dict[str, dict]:
    """Last scan results by server id (cache only, never the network)."""
    if not enabled():
        return {}
    data = _read_cache().get("servers")
    return data if isinstance(data, dict) else {}


def status(provider: str) -> str:
    row = cached().get(server_id(provider)) or {}
    return str(row.get("status") or "")


# ─── models (cache reads) ────────────────────────────────────────────────


@dataclass(frozen=True)
class LocalModel:
    id: str
    family: str = ""
    params: str = ""
    quant: str = ""
    size: int = 0
    ctx: int | None = None
    tools: bool | None = None
    vision: bool | None = None
    think: bool | None = None
    levels: tuple[str, ...] = ()
    loaded: bool | None = None
    cloud: bool = False
    offline: bool = False

    @property
    def usable(self) -> bool:
        return self.tools is not False

    def label(self, ctx: int | None = None) -> str:
        """``8.2B · Q4_K_M · 32K ctx · 5.2 GB`` — what the picker shows.
        ``ctx``: the window a request really gets, when it differs."""
        parts = [self.params, self.quant]
        if ctx or self.ctx:
            parts.append(f"{_fmt_ctx(ctx or self.ctx)} ctx")
        parts.append("cloud" if self.cloud else _fmt_size(self.size))
        if self.loaded:
            parts.append("loaded")
        if self.offline:
            parts.append("offline")
        if self.tools is False:
            parts.append("no tool use")
        return " · ".join(p for p in parts if p) or "local model"


def _as_model(row: dict, offline: bool = False) -> LocalModel | None:
    if not isinstance(row, dict) or not row.get("id"):
        return None
    try:
        ctx = int(row["ctx"]) if row.get("ctx") else None
    except (TypeError, ValueError):
        ctx = None
    return LocalModel(
        id=str(row["id"]), family=str(row.get("family") or ""), params=str(row.get("params") or ""),
        quant=str(row.get("quant") or ""), size=int(row.get("size") or 0), ctx=ctx,
        tools=row.get("tools"), vision=row.get("vision"), think=row.get("think"),
        levels=tuple(row.get("levels") or ()), loaded=row.get("loaded"),
        cloud=bool(row.get("cloud")), offline=offline,
    )


def models(provider: str, *, include_offline: bool = True) -> list[LocalModel]:
    """``provider``'s models, usable first (loaded first among those)."""
    row = cached().get(server_id(provider)) or {}
    offline = row.get("status") != STATUS_ONLINE
    raw = row.get("models") if not offline else (row.get("last_models") if include_offline else None)
    out = [m for m in (_as_model(r, offline) for r in raw or ()) if m is not None]
    out.sort(key=lambda m: (not m.usable, not m.loaded, m.id.lower()))
    return out


def get_model(provider: str, model: str) -> LocalModel | None:
    for m in models(provider):
        if m.id == model:
            return m
    return None


def picker_rows(provider: str) -> list[tuple[str, str]]:
    return [(m.id, m.label(served_context(provider, m.id))) for m in models(provider)]


def online_providers() -> list[str]:
    """Servers that answered the last scan with at least one chat model."""
    out = []
    by_id = cached()
    for s in servers():
        row = by_id.get(s.id) or {}
        if row.get("status") == STATUS_ONLINE and row.get("models"):
            out.append(s.provider)
    return out


def picker_providers(active: str = "") -> list[str]:
    """Local sources /model lists: online ones, plus the one in use (shown as
    offline when its server stopped) so the current model never vanishes."""
    out = online_providers()
    if is_local_provider(active) and active not in out and get_server(active) is not None:
        if models(active):
            out.append(active)
    return out


def default_model(provider: str) -> str:
    ms = models(provider)
    for m in ms:
        if m.usable and not m.offline:
            return m.id
    return ms[0].id if ms else ""


def label(provider: str) -> str:
    s = get_server(provider)
    return s.name if s is not None else server_id(provider)


# ─── request facts ───────────────────────────────────────────────────────


def served_context(provider: str, model: str) -> int | None:
    """The window a request really gets.

    Ollama: what Jarvis asks for (``context_setting``) capped at the model's
    own maximum. Other servers decide the window themselves when they load the
    model — llama.cpp / vLLM / LM Studio report it, so that's the number.
    """
    s = get_server(provider)
    m = get_model(provider, model)
    if s is not None and s.kind == KIND_OLLAMA:
        want = context_setting()
        if m is not None and m.cloud:
            return m.ctx or None
        return min(want, m.ctx) if m is not None and m.ctx else want
    return m.ctx if m is not None else None


def reply_tokens(provider: str, model: str) -> int:
    ctx = served_context(provider, model)
    if not ctx:
        return MAX_REPLY_TOKENS
    return max(1_024, min(MAX_REPLY_TOKENS, ctx // 4))


def think_facts(provider: str, model: str) -> tuple[bool | None, tuple[str, ...], bool]:
    """(thinks?, levels, can be switched off) — ``None`` when unknown."""
    m = get_model(provider, model)
    if m is None or m.think is None:
        return None, (), True
    return m.think, m.levels, not m.levels  # gpt-oss on Ollama always thinks


def local_timeout() -> float:
    try:
        return max(30.0, float(os.getenv("HARNESS_LOCAL_TIMEOUT", "900")))
    except ValueError:
        return 900.0


def build_client(provider: str):
    """A client for ``provider``: native Ollama, or the OpenAI-style one."""
    s = get_server(provider)
    if s is None:
        raise RuntimeError(f"unknown local server: {server_id(provider)}")
    if s.kind == KIND_OLLAMA:
        from .ollama_client import OllamaClient

        return OllamaClient(
            s.url, key=s.key,
            num_ctx=lambda model: served_context(provider, model),
            num_predict=lambda model: reply_tokens(provider, model),
            think=lambda model: think_facts(provider, model),
            read_timeout=local_timeout(),
            label=s.name,
        )
    import httpx

    from .opencode_client import OpenCodeClient

    def hint(model: str) -> dict:
        thinks, _levels, _off = think_facts(provider, model)
        return {
            "reasoning": bool(thinks),
            "reasoning_field": None,
            "max_output": reply_tokens(provider, model),
            "wire": "chat",
        }

    read = local_timeout()
    return OpenCodeClient(
        api_key=s.key or "local",
        base_url=f"{s.url}/",
        default_headers={"User-Agent": "harness-agent/1.0"},
        model_hints=hint,
        timeout=httpx.Timeout(connect=5.0, read=read, write=30.0, pool=5.0),
        read_timeout=read,
        # A local 5xx is almost always "doesn't fit in memory", and a refused
        # connection means it isn't running: retrying only repeats the wait.
        max_retries=0,
    )


# ─── adding / removing servers ───────────────────────────────────────────


def add_server(name: str, url: str, *, key: str = "", force: bool = False) -> dict[str, Any]:
    """Check ``url`` answers like a model server and save it.

    Returns ``{"ok", "id", "kind", "models", "error", "status"}``. ``force``
    saves a server that doesn't answer right now (it shows as offline).
    """
    clean = normalize_url(url)
    if not clean:
        return {"ok": False, "error": "That doesn't look like a URL — try http://192.168.1.20:11434"}
    key = (key or "").strip()
    try:
        kind, api = detect_kind(clean, key)
    except ProbeError as e:
        if not force:
            words = {
                STATUS_OFFLINE: f"Nothing answered at {clean} ({e})",
                STATUS_AUTH: f"{clean} wants an API key — add one and try again",
            }.get(e.status, f"{clean} {e}")
            return {"ok": False, "status": e.status, "error": words}
        kind, api = KIND_OPENAI, clean if re.search(r"/v1$", clean) else f"{clean}/v1"
    host = urllib.parse.urlsplit(clean).hostname or "server"
    nice = (name or "").strip() or (f"Ollama on {host}" if kind == KIND_OLLAMA else f"Server on {host}")
    with _lock:
        cfg = _read_config()
        rows = [r for r in cfg.get("servers") or () if isinstance(r, dict)]
        for r in rows:
            if normalize_url(str(r.get("url") or "")) == api:
                return {"ok": False, "error": f"{api} is already added as {r.get('name') or r.get('id')}"}
        taken = {str(r.get("id")) for r in rows} | set(RUNTIME_BY_ID)
        sid = base = _slug(nice)
        n = 2
        while sid in taken:
            sid, n = f"{base}-{n}", n + 1
        rows.append({"id": sid, "name": nice, "url": api, "kind": kind, "key": key})
        cfg["servers"] = rows
        _write_config(cfg)
    _bump()
    result = scan(only=provider_id(sid), timeout=3.0).get(sid) or {}
    return {"ok": True, "id": provider_id(sid), "name": nice, "kind": kind,
            "status": result.get("status", ""), "models": len(result.get("models") or ())}


def remove_server(provider: str) -> bool:
    sid = server_id(provider)
    with _lock:
        cfg = _read_config()
        rows = [r for r in cfg.get("servers") or () if isinstance(r, dict)]
        keep = [r for r in rows if r.get("id") != sid]
        if len(keep) == len(rows):
            return False
        cfg["servers"] = keep
        _write_config(cfg)
    _bump()
    return True


# ─── what the UIs show ───────────────────────────────────────────────────


def public(active_provider: str = "", active_model: str = "") -> dict[str, Any]:
    """Every server with its status and models — the TUI dialog and the web
    page draw from this. No keys, ever (``has_key`` only)."""
    by_id = cached()
    off = detection_off()
    rows: list[dict[str, Any]] = []
    for s in servers(include_off=True):
        r = by_id.get(s.id) or {}
        rt = s.runtime
        st = r.get("status") or ("off" if s.id in off else "unknown")
        if s.id in off:
            st = "off"
        ms = models(s.provider) if st != "off" else []
        rows.append({
            "id": s.provider,
            "server": s.id,
            "name": s.name,
            "kind": s.kind,
            "url": s.url,
            "host": s.host,
            "custom": s.custom,
            "remote": s.remote,
            "has_key": bool(s.key),
            "status": st,
            "error": r.get("error") or "",
            "version": r.get("version") or "",
            "ms": r.get("ms") or 0,
            "checked": r.get("checked") or 0,
            "last_seen": r.get("last_seen") or 0,
            "blurb": rt.blurb if rt else ("Ollama server" if s.kind == KIND_OLLAMA else "OpenAI-compatible server"),
            "start": rt.start if rt else "",
            "how": rt.how if rt else "",
            "site": rt.site if rt else "",
            "pull": rt.pull if rt else "",
            "mark": rt.mark if rt else (s.name[:2].title() or "Lo"),
            "active": active_provider == s.provider,
            "models": [
                {
                    "id": m.id, "label": m.label(served_context(s.provider, m.id)),
                    "params": m.params, "quant": m.quant,
                    "size": _fmt_size(m.size), "ctx": m.ctx, "ctx_label": _fmt_ctx(m.ctx),
                    "served_ctx": served_context(s.provider, m.id),
                    "tools": m.tools, "vision": m.vision, "think": m.think,
                    "loaded": m.loaded, "cloud": m.cloud, "offline": m.offline,
                    "usable": m.usable, "family": m.family,
                    "active": active_provider == s.provider and active_model == m.id,
                }
                for m in ms
            ],
        })
    # Running first, then added servers, then the rest in RUNTIMES order.
    order = {STATUS_ONLINE: 0, STATUS_AUTH: 1, STATUS_ERROR: 2}
    rows.sort(key=lambda r: (order.get(r["status"], 3 if r["custom"] else 4)))
    data = _read_cache()
    return {
        "enabled": enabled(),
        "servers": rows,
        "context": context_setting(),
        "context_choices": list(CONTEXT_CHOICES),
        "scanned_at": data.get("scanned_at") or 0,
        "online": sum(1 for r in rows if r["status"] == STATUS_ONLINE),
        "model_count": sum(len(r["models"]) for r in rows if r["status"] == STATUS_ONLINE),
    }
