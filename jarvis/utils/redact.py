"""Hide credentials in tool output before the model (or the transcript) sees it.

A model asked to debug an MCP token went looking and printed
``mcp_secrets.json`` — every key in it ended up in the conversation, in the
session database and at the provider. ``redact`` runs on every tool result:

* well-known token shapes (GitHub ``ghp_`` / ``github_pat_``, Figma ``figd_``,
  Anthropic / OpenAI / OpenRouter ``sk-…``, Slack ``xox?-``, GitLab, AWS,
  Google, Stripe, npm, Hugging Face) and PEM private-key bodies;
* the exact values Jarvis itself stores — MCP secrets, provider API keys —
  and secret-looking environment variables, whatever their shape.

Each hit keeps its first characters (so "which token is this" still reads)
and ends in ``MARKER``. Tools that write files or run commands refuse input
containing the marker (``has_marker``): writing it back would replace the
real secret with the placeholder.
"""
from __future__ import annotations

import json
import os
import re
import threading
from typing import Any

MARKER = "[hidden secret]"

_TOKEN_RE = re.compile(
    r"(?<![A-Za-z0-9_])(?:"
    r"gh[pousr]_[A-Za-z0-9]{30,}"
    r"|github_pat_[A-Za-z0-9_]{40,}"
    r"|figd_[A-Za-z0-9_\-]{20,}"
    r"|sk-ant-[A-Za-z0-9_\-]{20,}"
    r"|sk-or-v1-[A-Za-z0-9]{32,}"
    r"|sk-(?:proj|svcacct|admin)-[A-Za-z0-9_\-]{20,}"
    r"|sk-[A-Za-z0-9]{40,}"
    r"|xox[abeoprs]-[A-Za-z0-9\-]{10,}"
    r"|glpat-[A-Za-z0-9_\-]{20,}"
    r"|AKIA[0-9A-Z]{16}"
    r"|AIza[0-9A-Za-z_\-]{35}"
    r"|(?:sk|rk)_live_[A-Za-z0-9]{20,}"
    r"|npm_[A-Za-z0-9]{36}"
    r"|hf_[A-Za-z0-9]{30,}"
    r")"
)
_PEM_RE = re.compile(
    r"(-----BEGIN [A-Z ]*PRIVATE KEY-----)(.*?)(-----END [A-Z ]*PRIVATE KEY-----)", re.S
)
_PEM_BODY_RE = re.compile(r"[A-Za-z0-9+/=]{16,}")
_SECRET_ENV_RE = re.compile(r"(TOKEN|SECRET|PASSWORD|PASSWD|API_?KEY|PRIVATE_?KEY|CREDENTIAL)", re.I)
_MIN_KNOWN = 12  # shorter stored values are too likely to match ordinary text

_lock = threading.Lock()
_known_key: tuple = ()
_known: list[str] = []


def _hide(value: str) -> str:
    return value[:4] + "…" + MARKER


_TEXT_FILES = ("KEY_FILE", "OPENROUTER_KEY_FILE", "OPENCODE_KEY_FILE", "OPENCODE_ZEN_KEY_FILE")
_JSON_FILES = ("PROVIDER_KEYS_FILE", "OAUTH_FILE", "CODEX_OAUTH_FILE", "ANTIGRAVITY_OAUTH_FILE")


def _stored_files() -> list[tuple[Any, str]]:
    """``(path, kind)`` for each file where Jarvis keeps a credential."""
    out: list[tuple[Any, str]] = []
    try:
        from ..mcp.secrets import SECRETS_FILE
        out.append((SECRETS_FILE, "json"))
    except Exception:
        pass
    try:
        from ..constants import paths
    except Exception:
        return out
    for names, kind in ((_TEXT_FILES, "text"), (_JSON_FILES, "json")):
        for name in names:
            f = getattr(paths, name, None)
            if f is not None:
                out.append((f, kind))
    return out


def _json_secrets(data: Any, secret_key: bool = True) -> list[str]:
    """String values in a credentials file: every top-level value of a flat
    ``{name: key}`` map, and anything under a secret-looking key deeper down."""
    if isinstance(data, str):
        return [data] if secret_key else []
    if isinstance(data, dict):
        found: list[str] = []
        for k, v in data.items():
            found += _json_secrets(v, bool(_SECRET_ENV_RE.search(str(k))) or (secret_key and isinstance(v, str)))
        return found
    if isinstance(data, list):
        return [x for v in data for x in _json_secrets(v, secret_key)]
    return []


def _known_values() -> list[str]:
    """Stored secrets + secret-looking env values, longest first; memoised on
    the files' mtimes and the environment."""
    global _known_key, _known
    files = _stored_files()
    stamps = []
    for f, _kind in files:
        try:
            st = f.stat()
            stamps.append((str(f), st.st_mtime_ns, st.st_size))
        except OSError:
            stamps.append((str(f), None, None))
    env = tuple(sorted((k, v) for k, v in os.environ.items() if _SECRET_ENV_RE.search(k)))
    key = (tuple(stamps), env)
    with _lock:
        if key == _known_key:
            return _known
    found: set[str] = {v for _k, v in env}
    for f, kind in files:
        try:
            text = f.read_text(encoding="utf-8")
        except OSError:
            continue
        if kind == "text":
            found.add(text.strip())
            continue
        try:
            found.update(_json_secrets(json.loads(text), secret_key=f.name in ("mcp_secrets.json", "provider_keys.json")))
        except ValueError:
            continue
    values = sorted(
        (v.strip() for v in found if len(v.strip()) >= _MIN_KNOWN and "${" not in v and "\n" not in v.strip()),
        key=len, reverse=True,
    )
    with _lock:
        _known_key, _known = key, values
    return values


def redact(text: str) -> str:
    """``text`` with every credential replaced by its first characters + ``MARKER``."""
    if not text or not isinstance(text, str):
        return text
    for value in _known_values():
        if value in text:
            text = text.replace(value, _hide(value))
    text = _TOKEN_RE.sub(lambda m: _hide(m.group(0)), text)
    if "PRIVATE KEY-----" in text:
        text = _PEM_RE.sub(
            lambda m: m.group(1) + _PEM_BODY_RE.sub(MARKER, m.group(2)) + m.group(3), text
        )
    return text


def has_marker(value: Any) -> bool:
    """True if ``MARKER`` appears in any string inside ``value``."""
    if isinstance(value, str):
        return MARKER in value
    if isinstance(value, dict):
        return any(has_marker(v) for v in value.values())
    if isinstance(value, (list, tuple)):
        return any(has_marker(v) for v in value)
    return False
