"""Providers and login for the web remote — the web twin of ``/provider``.

The terminal walks through ``tui/provider_hub.py`` → ``key_modal.py`` /
``oauth_connect_modal.py``. The web shows every provider on one screen with
its status and the one thing to do next: sign in, paste a key, or use it.

Threads: checking a key and exchanging an OAuth code are network calls, so
they run on the HTTP handler thread (or the Codex callback thread). Anything
that changes the running session (client, model, which provider is live)
goes through ``bridge.request_action`` and runs on the TUI main thread, like
every other web mutation (``run_provider_action``, reached from
``actions_api.run_web_action``).

Keys never travel back to the browser: rows carry the last four characters.
"""
from __future__ import annotations

import os
import re
import threading
import time
import urllib.error
import urllib.request
import uuid
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Callable

from .. import state
from ..constants.api_keys import api_key_spec
from ..constants.oauth_providers import (
    OAUTH_ID_ANTHROPIC, OAUTH_ID_ANTIGRAVITY, OAUTH_ID_OPENAI_CODEX, oauth_provider,
)
from ..constants.providers import (
    AUTH_OAUTH,
    PROVIDER_ANTHROPIC,
    PROVIDER_ANTHROPIC_API,
    PROVIDER_ANTHROPIC_AUTH,
    PROVIDER_ANTIGRAVITY,
    PROVIDER_HARNESS_AGENT,
    PROVIDER_OPENAI_CODEX,
    PROVIDER_OPENAI_CODEX_AUTH,
    PROVIDER_OPENCODE,
    PROVIDER_OPENCODE_ZEN,
    PROVIDER_OPENROUTER,
)

if TYPE_CHECKING:
    from .bridge import WebBridge


@dataclass(frozen=True)
class Card:
    """One provider as the web dialog shows it."""

    id: str        # API-key spec id, OAuth provider id, or "harness_agent"
    kind: str      # "oauth" | "key" | "free"
    label: str
    blurb: str
    source: str    # model-picker source "Use" switches to
    mark: str      # short monogram for the row's tile
    link: str = ""  # where to get a key
    catalog: bool = False  # a provider from models.dev (id "md:<id>")


CARDS: tuple[Card, ...] = (
    Card("anthropic", "oauth", "Claude Pro / Max", "Sign in with your Claude subscription",
         PROVIDER_ANTHROPIC_AUTH, "C"),
    Card("openai_codex", "oauth", "ChatGPT Plus / Pro", "Codex models with your ChatGPT plan",
         PROVIDER_OPENAI_CODEX_AUTH, "G"),
    Card("antigravity", "oauth", "Google Antigravity", "Gemini and Claude with your Google account",
         PROVIDER_ANTIGRAVITY, "AG"),
    Card("anthropic_api", "key", "Anthropic API", "Claude models, pay as you go",
         PROVIDER_ANTHROPIC_API, "A", "https://console.anthropic.com/settings/keys"),
    Card("openrouter", "key", "OpenRouter", "Hundreds of models, many of them free",
         PROVIDER_OPENROUTER, "OR", "https://openrouter.ai/settings/keys"),
    Card("opencode", "key", "OpenCode Go", "GLM, Kimi, DeepSeek, MiMo, MiniMax, Qwen",
         PROVIDER_OPENCODE, "Go", "https://opencode.ai/auth"),
    Card("opencode_zen", "key", "OpenCode Zen", "Coding models, pay as you go",
         PROVIDER_OPENCODE_ZEN, "Zen", "https://opencode.ai/auth"),
    Card("harness_agent", "free", "Harness Agent", "Free models. No account, no key.",
         PROVIDER_HARNESS_AGENT, "H"),
)
CARD_BY_ID = {c.id: c for c in CARDS}

# Keys the server checks with the provider before saving (verify_key).
CHECKED_CARDS = frozenset({"anthropic_api", "openrouter"})


def _monogram(name: str) -> str:
    words = re.findall(r"[A-Za-z0-9]+", name)
    if len(words) >= 2:
        return (words[0][0] + words[1][0]).upper()
    return (words[0][:2] if words else "?").title()


def _catalog_card(pid: str) -> Card | None:
    from ..auth import models_dev

    prov = models_dev.get_provider(pid)
    if prov is None:
        return None
    n = len(prov.models)
    return Card(pid, "key", prov.name, f"{n} model{'s' if n != 1 else ''} · models.dev",
                pid, _monogram(prov.name), prov.doc, catalog=True)


def card_for(card_id: str) -> Card | None:
    """A built-in card, or one for a models.dev provider (``md:<id>``)."""
    card = CARD_BY_ID.get(card_id)
    if card is None and card_id.startswith("md:"):
        card = _catalog_card(card_id)
    return card


def _catalog_key_status(pid: str, sources: dict | None = None) -> tuple[str, str]:
    from ..auth import catalog_keys

    source, value = (sources or {}).get(pid) or catalog_keys.key_source(pid)
    if source == "file" and catalog_keys.is_reference(catalog_keys.saved_value(pid)):
        return source, catalog_keys.saved_value(pid)  # "$VAR" is safe to show
    return source, _tail(value) if value else ""


def _ok(message: str = "", **extra: Any) -> dict[str, Any]:
    return {"ok": True, "message": message, **extra}


def _err(error: str, **extra: Any) -> dict[str, Any]:
    return {"ok": False, "error": error, **extra}


# ─── Status ───────────────────────────────────────────────────────────────


def _tail(secret: str) -> str:
    return f"…{secret[-4:]}" if len(secret) > 8 else ""


def key_status(spec: dict[str, Any]) -> tuple[str, str]:
    """``(source, hint)`` for an API-key spec: source is env | file | none."""
    env_val = os.getenv(spec["env_var"], "").strip()
    if env_val:
        return "env", _tail(env_val)
    try:
        raw = spec["file_path"].read_text().strip() if spec["file_path"].exists() else ""
    except OSError:
        raw = ""
    return ("file", _tail(raw)) if raw else ("none", "")


def _signed_in(card_id: str) -> bool:
    if card_id == OAUTH_ID_ANTHROPIC:
        from ..auth.oauth_tokens import load_oauth_tokens

        return load_oauth_tokens() is not None
    if card_id == OAUTH_ID_OPENAI_CODEX:
        from ..auth.codex_oauth_tokens import load_codex_oauth_tokens

        return load_codex_oauth_tokens() is not None
    if card_id == OAUTH_ID_ANTIGRAVITY:
        from ..auth.antigravity_oauth import load_antigravity_tokens

        return load_antigravity_tokens() is not None
    return False


def active_card_id() -> str:
    """Which card the running session is on."""
    provider = state.provider
    if provider == PROVIDER_OPENCODE_ZEN:
        return "harness_agent" if state.harness_agent_free else "opencode_zen"
    if provider == PROVIDER_ANTHROPIC:
        return "anthropic" if state.auth_mode == AUTH_OAUTH else "anthropic_api"
    if provider == PROVIDER_OPENAI_CODEX:
        return "openai_codex"
    if provider == PROVIDER_ANTIGRAVITY:
        return "antigravity"
    return provider or ""


def card_row(card: Card, active: str | None = None, *, sources: dict | None = None) -> dict[str, Any]:
    active = active_card_id() if active is None else active
    row: dict[str, Any] = {
        "id": card.id,
        "kind": card.kind,
        "label": card.label,
        "blurb": card.blurb,
        "mark": card.mark,
        "link": card.link,
        "active": card.id == active,
        "hint": "",
        "env_var": "",
        "key_prefix": "",
        "catalog": card.catalog,
        "checked": card.id in CHECKED_CARDS,
    }
    if card.kind == "free":
        row.update(connected=True, source="free")
    elif card.kind == "oauth":
        signed_in = _signed_in(card.id)
        row.update(connected=signed_in, source="oauth" if signed_in else "none")
    elif card.catalog:
        from ..auth import catalog_keys, models_dev

        prov = models_dev.get_provider(card.id)
        source, hint = _catalog_key_status(card.id, sources)
        missing = models_dev.missing_vars(prov) if prov else []
        env_var = catalog_keys.env_var_for(card.id) if source == "env" else (
            prov.key_vars[0] if prov and prov.key_vars else ""
        )
        row.update(
            connected=source != "none" and not missing,
            source=source,
            hint=hint,
            env_var=env_var,
            local=bool(prov and prov.local),
            needs=missing,
            checked=not (prov and prov.local),
        )
    else:
        spec = api_key_spec(card.id) or {}
        source, hint = key_status(spec) if spec else ("none", "")
        row.update(
            connected=source != "none",
            source=source,
            hint=hint,
            env_var=spec.get("env_var", ""),
            key_prefix=spec.get("key_prefix") or "",
        )
    return row


def _catalog_cards() -> list[Card]:
    try:
        from ..auth import models_dev

        pids = sorted(models_dev.providers(), key=lambda p: models_dev.providers()[p].name.lower())
    except Exception:
        return []
    return [c for c in (_catalog_card(pid) for pid in pids) if c is not None]


def list_providers() -> dict[str, Any]:
    active = active_card_id()
    catalog = _catalog_cards()
    sources = None
    if catalog:
        from ..auth import catalog_keys

        sources = catalog_keys.all_sources()  # the keys file once, not once per row
    rows = [card_row(c, active, sources=sources) for c in (*CARDS, *catalog)]
    current = card_for(active) if active else None
    return {
        "providers": rows,
        "active": active,
        "active_label": current.label if current else (state.provider or ""),
        "model": state.MODEL,
        "connected": sum(1 for r in rows if r["connected"] and r["kind"] != "free"),
    }


# ─── API keys ─────────────────────────────────────────────────────────────

_ENV_ASSIGN = re.compile(r"^(?:export\s+)?[A-Za-z_][A-Za-z0-9_]*\s*=\s*")


def clean_key(raw: str) -> str:
    """The key out of whatever was pasted: ``export X=…``, quotes, ``Bearer …``."""
    s = (raw or "").strip()
    s = _ENV_ASSIGN.sub("", s, count=1).strip().strip("'\"`").strip()
    if s.lower().startswith("bearer "):
        s = s[7:].strip()
    return s


def detect_key(key: str) -> str:
    """Card id a key's prefix gives away, or ``""`` when it could be anyone's."""
    if key.startswith("sk-ant-oat"):
        return "anthropic"  # a Claude subscription token, not an API key
    if key.startswith("sk-ant-"):
        return "anthropic_api"
    if key.startswith("sk-or-"):
        return "openrouter"
    return ""


def check_key_format(card_id: str, key: str) -> dict[str, Any] | None:
    """An error response when ``key`` can't be a ``card_id`` key, else None."""
    card = card_for(card_id)
    if card is None:
        return _err("Unknown provider")
    if not key:
        return _err("Paste a key first.")
    if any(ch.isspace() for ch in key):
        return _err("That looks like more than one line. Paste just the key.")
    if card.catalog:
        from ..auth import catalog_keys, models_dev

        if catalog_keys.is_reference(key):
            return None  # "$VAR": use an env var on the Jarvis computer
        prov = models_dev.get_provider(card_id)
        if prov is not None and prov.local:
            return None  # local servers often take any value
    if len(key) < 16:
        return _err("That key looks too short. Copy the whole thing.")
    detected = detect_key(key)
    if detected == "anthropic":
        return _err(
            "That's a Claude sign-in token, not an API key. Use Claude Pro / Max to sign in.",
            suggest="anthropic",
        )
    if detected and detected != card_id:
        return _err(f"That looks like an {CARD_BY_ID[detected].label} key.", suggest=detected)
    prefix = (api_key_spec(card_id) or {}).get("key_prefix")
    if prefix and not key.startswith(prefix):
        return _err(f"{card.label} keys start with {prefix}")
    return None


def _check_request(card_id: str, key: str) -> tuple[str, dict[str, str]] | None:
    """A cheap authenticated GET that answers 401 for a bad key."""
    if card_id == "anthropic_api":
        return "https://api.anthropic.com/v1/models?limit=1", {
            "x-api-key": key, "anthropic-version": "2023-06-01",
        }
    if card_id == "openrouter":
        return "https://openrouter.ai/api/v1/key", {"Authorization": f"Bearer {key}"}
    if card_id.startswith("md:"):
        from ..auth import models_dev

        prov = models_dev.get_provider(card_id)
        base = models_dev.base_url(prov) if prov is not None else None
        if not base or prov.local:
            return None
        if prov.wire == models_dev.WIRE_ANTHROPIC:
            return f"{base.rstrip('/')}/v1/models", {"x-api-key": key, "anthropic-version": "2023-06-01"}
        # Most OpenAI-style APIs answer /models; one that doesn't (404) is
        # "unknown", never a refusal.
        return f"{base.rstrip('/')}/models", {"Authorization": f"Bearer {key}"}
    # OpenCode serves /models to anyone, so there's no cheap check.
    return None


def verify_key(card_id: str, key: str, *, timeout: float = 8.0) -> str:
    """``ok`` | ``rejected`` (the provider said 401/403) | ``unknown``."""
    check = _check_request(card_id, key)
    if check is None:
        return "unknown"
    url, headers = check
    req = urllib.request.Request(url, headers={"Accept": "application/json", **headers})
    req.headers.setdefault("User-agent", "harness-jarvis")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return "ok" if resp.status == 200 else "unknown"
    except urllib.error.HTTPError as exc:
        return "rejected" if exc.code in (401, 403) else "unknown"
    except (urllib.error.URLError, OSError, ValueError):
        return "unknown"


def save_key(
    card_id: str,
    raw: str,
    *,
    use: bool,
    run_action: Callable[[str, dict[str, Any]], dict[str, Any]],
) -> dict[str, Any]:
    """Check, verify and save a pasted key; then apply it (main thread).

    HTTP handler thread. A key the provider refuses is never written.
    """
    card = card_for(card_id)
    if card is not None and card.catalog:
        return _save_catalog_key(card, raw, use=use, run_action=run_action)
    spec = api_key_spec(card_id)
    if card is None or card.kind != "key" or spec is None:
        return _err("Unknown provider")
    key = clean_key(raw)
    bad = check_key_format(card_id, key)
    if bad:
        return bad
    if os.getenv(spec["env_var"], "").strip():
        return _err(
            f"{spec['env_var']} is set on your computer, so Jarvis uses that key. "
            "Change it there, or unset it to save one here."
        )
    verdict = verify_key(card_id, key)
    if verdict == "rejected":
        return _err(f"{card.label} didn't accept this key. Check that you copied all of it.", rejected=True)
    from ..utils.io import _secure_write

    replaced = key_status(spec)[0] == "file"
    try:
        _secure_write(spec["file_path"], key)
    except OSError as exc:
        return _err(f"Couldn't save the key: {exc}")
    result = run_action("provider_key_saved", {"id": card_id, "use": bool(use), "replaced": replaced})
    if result.get("ok"):
        result["verified"] = verdict == "ok"
    return result


def _save_catalog_key(card: Card, raw: str, *, use: bool, run_action: Callable) -> dict[str, Any]:
    """``save_key`` for a models.dev provider: same checks, its own key store."""
    from ..auth import catalog_keys

    key = clean_key(raw)
    bad = check_key_format(card.id, key)
    if bad:
        return bad
    source, _hint = catalog_keys.key_source(card.id)
    if source == "env":
        var = catalog_keys.env_var_for(card.id)
        return _err(
            f"{var} is set on your computer, so Jarvis uses that key. "
            "Change it there, or unset it to save one here."
        )
    if catalog_keys.is_reference(key):
        if not catalog_keys._expand(key):
            return _err(f"{key} isn't set on the computer running Jarvis.")
        verdict = "unknown"
    else:
        verdict = verify_key(card.id, key)
    if verdict == "rejected":
        return _err(f"{card.label} didn't accept this key. Check that you copied all of it.", rejected=True)
    replaced = source == "file"
    try:
        catalog_keys.save(card.id, key)
    except (OSError, ValueError) as exc:
        return _err(f"Couldn't save the key: {exc}")
    result = run_action("provider_key_saved", {"id": card.id, "use": bool(use), "replaced": replaced})
    if result.get("ok"):
        result["verified"] = verdict == "ok"
    return result


# ─── Main-thread mutations (via bridge.request_action) ────────────────────


def _default_model(source: str) -> str:
    from ..constants import providers as p

    if p.is_catalog_provider(source):
        return p.normalize_model_for_provider("", source)
    if source == PROVIDER_OPENROUTER:
        return p.openrouter_default_model()
    if source == PROVIDER_OPENAI_CODEX_AUTH:
        return p.codex_default_model()
    if source == PROVIDER_ANTIGRAVITY:
        return p.antigravity_default_model()
    if source == PROVIDER_OPENCODE:  # live (models.dev + the gateway's list)
        return p.opencode_go_default_model() or state.MODEL
    if source == PROVIDER_OPENCODE_ZEN:
        return p.opencode_zen_default_model() or state.MODEL
    return {
        PROVIDER_HARNESS_AGENT: p.harness_agent_default_model(),
        PROVIDER_ANTHROPIC_API: p.ANTHROPIC_DEFAULT_MODEL,
        PROVIDER_ANTHROPIC_AUTH: p.ANTHROPIC_DEFAULT_MODEL,
    }.get(source, state.MODEL)


def model_for_source(source: str) -> str:
    """Keep the current model when the source serves it, else its default."""
    from ..constants.providers import models_for_source

    try:
        ids = [mid for mid, _desc in models_for_source(source, cached=True)]
    except Exception:
        ids = []
    if state.MODEL in ids:
        return state.MODEL
    default = _default_model(source)
    if not ids or default in ids:
        return default
    return ids[0]


def use_card(card_id: str) -> dict[str, Any]:
    card = card_for(card_id)
    if card is None:
        return _err("Unknown provider")
    row = card_row(card)
    if not row["connected"]:
        verb = "Sign in to" if card.kind == "oauth" else "Add a key for"
        return _err(f"{verb} {card.label} first")
    if row["active"]:
        return _ok(f"Already using {card.label}", model=state.MODEL)
    from ..commands.control import _apply_model_selection

    _apply_model_selection(model_for_source(card.source), source=card.source)
    if active_card_id() != card_id:
        return _err(f"Couldn't switch to {card.label}. The terminal shows why.")
    return _ok(f"Now using {card.label} · {state.MODEL}", model=state.MODEL)


def _fall_back_to_free() -> None:
    from ..auth.client import _fallback_harness_agent_client
    from ..constants.providers import harness_agent_default_model, is_harness_agent_model
    from ..storage.prefs import save_last_model

    keep = state.MODEL if is_harness_agent_model(state.MODEL) else harness_agent_default_model()
    state.client = _fallback_harness_agent_client(preferred_model=keep)
    save_last_model()


def apply_saved_key(card_id: str, use: bool, replaced: bool = False) -> dict[str, Any]:
    card = card_for(card_id)
    if card is None:
        return _err("Unknown provider")
    provider = card.id if card.catalog else (api_key_spec(card_id) or {}).get("provider", "")
    from ..commands.control import apply_key_change

    was_active = active_card_id() == card_id
    apply_key_change(provider)  # rebuilds the client if it's the live one
    done = f"Saved the new {card.label} key" if replaced else f"{card.label} connected"
    if use and not was_active:
        res = use_card(card_id)
        if res.get("ok"):
            return _ok(f"{done} · now using {state.MODEL}", model=state.MODEL)
        return _ok(f"{done}. {res.get('error', '')}".strip())
    if was_active or replaced:
        return _ok(done)
    return _ok(f"{done}. Its models are in the model list.")


def remove_key(card_id: str) -> dict[str, Any]:
    card = card_for(card_id)
    if card is not None and card.catalog:
        from ..auth import catalog_keys

        source, _value = catalog_keys.key_source(card.id)
        if source == "env":
            var = catalog_keys.env_var_for(card.id)
            return _err(f"This key comes from {var} on your computer. Remove it there.")
        if not catalog_keys.delete(card.id):
            return _ok(f"No {card.label} key saved")
        from ..commands.control import apply_key_change

        note = apply_key_change(card.id, removed=True)
        fallback = " · switched to Harness Agent (free)" if note.startswith("switched") else ""
        return _ok(f"Removed the {card.label} key{fallback}")
    spec = api_key_spec(card_id)
    if card is None or spec is None:
        return _err("Unknown provider")
    source, _hint = key_status(spec)
    if source == "env":
        return _err(f"This key comes from {spec['env_var']} on your computer. Remove it there.")
    if source == "none":
        return _ok(f"No {card.label} key saved")
    try:
        spec["file_path"].unlink(missing_ok=True)
    except OSError as exc:
        return _err(f"Couldn't remove the key: {exc}")
    from ..commands.control import apply_key_change

    note = apply_key_change(spec["provider"], removed=True)
    fallback = " · switched to Harness Agent (free)" if note.startswith("switched") else ""
    return _ok(f"Removed the {card.label} key{fallback}")


def activate_signed_in(card_id: str) -> dict[str, Any]:
    card = CARD_BY_ID.get(card_id)
    spec = oauth_provider(card_id)
    if card is None or spec is None:
        return _err("Unknown provider")
    from ..auth.connect.oauth_actions import activate_oauth

    ok, msg, _model_ids = activate_oauth(spec)
    if not ok:
        return _err(msg[:1].upper() + msg[1:] if msg else f"Couldn't switch to {card.label}")
    return _ok(f"Signed in to {card.label} · now using {state.MODEL}", model=state.MODEL)


def sign_out(card_id: str) -> dict[str, Any]:
    card = CARD_BY_ID.get(card_id)
    spec = oauth_provider(card_id)
    if card is None or spec is None:
        return _err("Unknown provider")
    from ..auth.connect.oauth_actions import disconnect_oauth

    was_active = active_card_id() == card_id
    ok, msg, _ = disconnect_oauth(spec)
    if not ok:
        return _ok(f"Not signed in to {card.label}") if msg == "not signed in" else _err(msg)
    # Signing out of the live account leaves no client (or a Codex id with no
    # tokens): land on the free tier instead of a session that can't answer.
    if was_active and (state.client is None or active_card_id() == card_id):
        _fall_back_to_free()
        return _ok(f"Signed out of {card.label} · switched to Harness Agent (free)")
    return _ok(f"Signed out of {card.label}")


def run_provider_action(action: str, data: dict[str, Any], *, console_print: Callable) -> dict[str, Any]:
    """``provider_*`` web actions. TUI main thread."""
    card_id = str(data.get("id") or "").strip()
    if action == "provider_use":
        result = use_card(card_id)
    elif action == "provider_key_saved":
        result = apply_saved_key(card_id, bool(data.get("use")), bool(data.get("replaced")))
    elif action == "provider_key_remove":
        result = remove_key(card_id)
    elif action == "provider_signed_in":
        result = activate_signed_in(card_id)
    elif action == "provider_sign_out":
        result = sign_out(card_id)
    else:
        return _err(f"unknown action: {action}")
    if result.get("ok") and result.get("message"):
        from rich.markup import escape

        console_print(f"[dim]web · {escape(str(result['message']))}[/]")
    return result


# ─── OAuth sign-in ────────────────────────────────────────────────────────

FLOW_TTL = 900.0  # a sign-in link stays good this long


@dataclass
class OAuthFlow:
    id: str
    card_id: str
    verifier: str
    state: str
    url: str
    created: float = field(default_factory=time.monotonic)
    status: str = "waiting"   # waiting → working → done | error
    message: str = ""
    listening: bool = False   # Codex: the localhost callback is up
    stop: threading.Event = field(default_factory=threading.Event)
    thread: threading.Thread | None = None

    def public(self) -> dict[str, Any]:
        return {
            "ok": True,
            "flow": self.id,
            "id": self.card_id,
            "status": self.status,
            "message": self.message,
            "listening": self.listening,
        }


_flows: dict[str, OAuthFlow] = {}
_flows_lock = threading.Lock()


def _drop_flow(flow: OAuthFlow, *, join: bool = False) -> None:
    flow.stop.set()
    with _flows_lock:
        _flows.pop(flow.id, None)
    if join and flow.thread is not None and flow.thread is not threading.current_thread():
        flow.thread.join(timeout=2.0)


def _get_flow(flow_id: str) -> OAuthFlow | None:
    with _flows_lock:
        flow = _flows.get(flow_id)
    if flow is not None and time.monotonic() - flow.created > FLOW_TTL:
        _drop_flow(flow)
        return None
    return flow


def start_oauth(card_id: str, *, run_action: Callable[[str, dict[str, Any]], dict[str, Any]]) -> dict[str, Any]:
    """A fresh sign-in link. HTTP handler thread; never opens a browser here —
    the user may be on a phone, so the page opens the link on its own device."""
    card = CARD_BY_ID.get(card_id)
    if card is None or card.kind != "oauth":
        return _err("Unknown provider")
    # One sign-in per provider: a new link replaces the old one (and frees
    # the Codex callback port it was holding).
    with _flows_lock:
        old = [f for f in _flows.values() if f.card_id == card_id]
    for f in old:
        _drop_flow(f, join=True)

    from ..auth.pkce import _pkce_pair

    verifier, challenge, oauth_state = _pkce_pair()
    if card_id == OAUTH_ID_ANTHROPIC:
        from ..auth.oauth_tokens import anthropic_authorize_url

        url = anthropic_authorize_url(challenge, oauth_state)
    elif card_id == OAUTH_ID_ANTIGRAVITY:
        from ..auth.antigravity_oauth import build_antigravity_authorize_url

        try:
            url = build_antigravity_authorize_url(code_challenge=challenge, state=oauth_state)
        except RuntimeError as e:  # no OAuth client configured
            return _err(str(e))
    else:
        from ..auth.codex_oauth_tokens import build_codex_authorize_url
        from ..constants.codex_oauth import CODEX_OAUTH_CLIENT_ID, CODEX_OAUTH_REDIRECT_URI

        url = build_codex_authorize_url(
            client_id=CODEX_OAUTH_CLIENT_ID,
            redirect_uri=CODEX_OAUTH_REDIRECT_URI,
            code_challenge=challenge,
            state=oauth_state,
        )
    flow = OAuthFlow(uuid.uuid4().hex, card_id, verifier, oauth_state, url)
    with _flows_lock:
        _flows[flow.id] = flow
    if card_id in (OAUTH_ID_OPENAI_CODEX, OAUTH_ID_ANTIGRAVITY):
        _start_codex_listener(flow, run_action)
    out = flow.public()
    out["url"] = url
    return out


def _start_codex_listener(flow: OAuthFlow, run_action: Callable) -> None:
    """Sign-in on this computer lands on localhost (ChatGPT :1455, Antigravity
    :51121) and finishes by itself."""
    from ..auth.codex_oauth_callback import (
        CodexOAuthCallbackError, pick_codex_callback_port, wait_for_codex_oauth_callback,
    )

    if flow.card_id == OAUTH_ID_ANTIGRAVITY:
        from ..constants.antigravity_oauth import (
            ANTIGRAVITY_OAUTH_CALLBACK_PATH as path, ANTIGRAVITY_OAUTH_CALLBACK_PORT as port,
        )
    else:
        from ..constants.codex_oauth import CODEX_OAUTH_CALLBACK_PATH as path, CODEX_OAUTH_CALLBACK_PORT as port

    try:
        pick_codex_callback_port(port)
    except CodexOAuthCallbackError:
        return  # Codex CLI / Antigravity (or the terminal dialog) holds it — pasting still works

    def listen() -> None:
        try:
            code, _state = wait_for_codex_oauth_callback(
                expected_state=flow.state,
                port=port,
                timeout=FLOW_TTL,
                stop=flow.stop,
                path=path,
            )
        except (CodexOAuthCallbackError, OSError) as exc:
            flow.listening = False
            if not flow.stop.is_set() and flow.status == "waiting":
                flow.status, flow.message = "error", _codex_callback_error(str(exc))
            return
        flow.listening = False
        _complete(flow, code, run_action)

    flow.listening = True
    flow.thread = threading.Thread(target=listen, daemon=True, name=f"jarvis-web-{flow.card_id}-login")
    flow.thread.start()


def _codex_callback_error(msg: str) -> str:
    low = msg.lower()
    if "timed out" in low:
        return "The sign-in link expired. Start again."
    if "state mismatch" in low:
        return "That sign-in came from an older link. Start again."
    return f"Sign-in failed: {msg}"


def _anthropic_error(status: int, body: object) -> str:
    err_type, err_msg = "", ""
    if isinstance(body, dict):
        err = body.get("error")
        if isinstance(err, dict):
            err_type = str(err.get("type") or err.get("error") or "")
            err_msg = str(err.get("message") or err.get("error_description") or "")
        elif isinstance(err, str):
            err_type = err
        err_msg = err_msg or str(body.get("error_description") or body.get("message") or "")
    if status == 429 or err_type in ("rate_limit_error", "too_many_requests"):
        return "Too many tries. Wait a minute, then get a new link."
    if err_type in ("invalid_grant", "expired_token"):
        return "That code expired or was already used. Open the sign-in page again and paste the new code."
    if status == 0:
        return "Couldn't reach Anthropic. Check the computer's internet connection."
    if status in (401, 403) or err_type == "invalid_client":
        return "Anthropic refused the sign-in. Try again with the same Claude account."
    return f"Sign-in failed ({status}): {err_msg or err_type or 'unknown error'}"


def _codex_error(status: int, body: object) -> str:
    if status == 0:
        return "Couldn't reach OpenAI. Check the computer's internet connection."
    desc = ""
    if isinstance(body, dict):
        desc = str(body.get("error_description") or body.get("error") or body.get("message") or "")
    if "invalid_grant" in desc or "expired" in desc.lower():
        return "That sign-in expired or was already used. Start again."
    return f"Sign-in failed ({status}){': ' + desc if desc else ''}"


def _claim(flow: OAuthFlow) -> bool:
    """Only one finisher per flow (the Codex callback and a paste can race)."""
    with _flows_lock:
        if flow.status not in ("waiting", "error"):
            return False
        flow.status, flow.message = "working", ""
        return True


def _complete(flow: OAuthFlow, code: str, run_action: Callable) -> dict[str, Any]:
    """Exchange ``code``, save the tokens, switch to the account."""
    if not _claim(flow):
        return flow.public() if flow.status == "done" else _err("Already finishing. One moment.")

    def retry(msg: str) -> dict[str, Any]:
        flow.status, flow.message = "waiting", msg
        return _err(msg)

    try:
        if flow.card_id == OAUTH_ID_ANTHROPIC:
            from ..auth.oauth_tokens import exchange_oauth_code, save_oauth_tokens
            from ..constants import OAUTH_SCOPES
            from ..constants.models import OAUTH_DEFAULT_EXPIRY

            status, body = exchange_oauth_code(code, flow.verifier, flow.state)
            if status != 200 or not isinstance(body, dict) or "access_token" not in body:
                return retry(_anthropic_error(status, body))
            raw_scope = body.get("scope", OAUTH_SCOPES)
            save_oauth_tokens({
                "access_token": body["access_token"],
                "refresh_token": body.get("refresh_token", ""),
                "expires_at": int(time.time()) + int(body.get("expires_in") or OAUTH_DEFAULT_EXPIRY),
                "scopes": raw_scope.split() if isinstance(raw_scope, str) else (raw_scope or []),
            })
        elif flow.card_id == OAUTH_ID_ANTIGRAVITY:
            from ..auth.antigravity_oauth import complete_antigravity_login

            bundle, why = complete_antigravity_login(code, flow.verifier)
            if bundle is None:
                return retry(why)
            try:
                from ..auth.antigravity_catalog import refresh_models

                refresh_models()  # the account's own line-up before the model list opens
            except Exception:
                pass
        else:
            from ..auth.codex_oauth_tokens import (
                exchange_codex_api_key, exchange_codex_oauth_code, persist_codex_oauth_bundle,
            )
            from ..constants.codex_oauth import CODEX_OAUTH_REDIRECT_URI

            status, body = exchange_codex_oauth_code(code, flow.verifier, redirect_uri=CODEX_OAUTH_REDIRECT_URI)
            if status != 200 or not isinstance(body, dict) or "access_token" not in body:
                return retry(_codex_error(status, body))
            api_key = None
            if body.get("id_token"):
                k_status, k_body = exchange_codex_api_key(str(body["id_token"]))
                if k_status == 200 and isinstance(k_body, dict):
                    api_key = k_body.get("access_token")
            persist_codex_oauth_bundle(body, api_key=api_key)
    except Exception as exc:  # network / disk — keep the flow so the user can retry
        return retry(f"Sign-in failed: {exc}")

    flow.stop.set()  # a paste finished first: release the callback port
    result = run_action("provider_signed_in", {"id": flow.card_id})
    if result.get("ok"):
        flow.status, flow.message = "done", str(result.get("message") or "Signed in")
    else:
        flow.status = "error"
        flow.message = f"Signed in, but switching failed: {result.get('error') or 'unknown error'}"
        result = _err(flow.message)
    return result


def finish_oauth(flow_id: str, pasted: str, *, run_action: Callable) -> dict[str, Any]:
    """The pasted ``code#state`` (Claude) or callback address (ChatGPT, Antigravity)."""
    flow = _get_flow(flow_id)
    if flow is None:
        return _err("This sign-in link expired. Start again.", expired=True)
    if flow.status == "done":
        return _ok(flow.message)
    raw = (pasted or "").strip()
    if not raw:
        return _err("Paste the code first.")
    # Phones often copy the callback address without its scheme, or just
    # the query: read those as the URL they came from.
    if not raw.startswith(("http://", "https://")) and ("code=" in raw or "error=" in raw):
        raw = "http://localhost/?" + raw.split("?", 1)[-1]
    if raw.startswith(("http://", "https://")):
        import urllib.parse

        qs = urllib.parse.parse_qs(urllib.parse.urlparse(raw).query)
        if qs.get("error"):
            desc = (qs.get("error_description") or qs["error"])[0]
            return _err(f"Sign-in was cancelled or refused: {desc}")
    from ..auth.oauth_tokens import parse_oauth_paste

    code, pasted_state = parse_oauth_paste(raw, flow.state)
    if not code:
        return _err("Couldn't find a code in that. Copy it again.")
    if pasted_state != flow.state:
        return _err("That code is from an older sign-in link. Open the sign-in page again and paste the new one.")
    return _complete(flow, code, run_action)


def oauth_status(flow_id: str) -> dict[str, Any]:
    flow = _get_flow(flow_id)
    if flow is None:
        return _err("This sign-in link expired. Start again.", expired=True, status="expired")
    return flow.public()


def cancel_oauth(flow_id: str) -> dict[str, Any]:
    flow = _get_flow(flow_id)
    if flow is not None and flow.status != "working":
        _drop_flow(flow)
    return _ok()


def bridge_runner(bridge: "WebBridge | None") -> Callable[[str, dict[str, Any]], dict[str, Any]]:
    """``run_action`` for the functions above: main thread through the bridge.

    Every change tells open pages to reload their provider list — including a
    ChatGPT sign-in that finished on its own via the localhost callback.
    """
    if bridge is not None:
        def run_via_bridge(action: str, data: dict[str, Any]) -> dict[str, Any]:
            result = bridge.request_action(action, data)
            if isinstance(result, dict) and result.get("ok"):
                bridge.emit("providers", {})
            return result

        return run_via_bridge

    def run_here(action: str, data: dict[str, Any]) -> dict[str, Any]:
        from ..console import console

        return run_provider_action(action, data, console_print=console.print)

    return run_here
