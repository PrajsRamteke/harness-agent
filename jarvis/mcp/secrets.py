"""Credentials for MCP servers — kept out of the config files.

A server entry refers to a secret as ``${NAME}`` (in ``env``, ``headers``,
``args``, ``url``); the value is looked up in the process environment first,
then in ``~/.config/harness-agent/mcp_secrets.json`` (mode 600). That keeps a
project ``.mcp.json`` safe to commit: it only ever names the variable.

``expand`` is applied to a server's config right before it connects.
"""
from __future__ import annotations

import json
import os
import pathlib
import re
import threading
from typing import Any

SECRETS_FILE = pathlib.Path.home() / ".config" / "harness-agent" / "mcp_secrets.json"

_REF_RE = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)(?::-([^}]*))?\}")
_lock = threading.Lock()

# Values a README puts where the reader's own key goes.
_PLACEHOLDER_RE = re.compile(
    r"^(?:<[^>]*>|\[[^\]]*\]|\{[^}]*\}|your[-_ ].*|.*[-_ ]here|x{3,}|\*{3,}|\.{3}|changeme|todo|"
    r"(?:api|secret|access)?[-_ ]?(?:key|token)|"
    r"(?:sk|pk|ghp|xox[bp]|glpat)[-_]?(?:x{3,}|\.{3}|your.*)|"
    r"(?:api|auth|bearer)?[-_ ]?(?:key|token)[-_ ]?(?:value|goes[-_ ]here))$",
    re.I,
)
# Names that hold a credential (so their value is stored, not written to a config).
_SECRET_NAME_RE = re.compile(r"(token|secret|password|passwd|api[-_]?key|apikey|auth|bearer|credential|private[-_]?key)", re.I)


def _read() -> dict[str, str]:
    try:
        raw = json.loads(SECRETS_FILE.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return {str(k): str(v) for k, v in raw.items()} if isinstance(raw, dict) else {}


def _write(data: dict[str, str]) -> None:
    SECRETS_FILE.parent.mkdir(parents=True, exist_ok=True)
    tmp = SECRETS_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    try:
        os.chmod(tmp, 0o600)
    except OSError:
        pass
    os.replace(tmp, SECRETS_FILE)


def set_secret(name: str, value: str) -> None:
    if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", name or ""):
        raise ValueError(f"'{name}' is not a valid variable name")
    with _lock:
        data = _read()
        data[name] = value
        _write(data)


def delete_secret(name: str) -> bool:
    with _lock:
        data = _read()
        if name not in data:
            return False
        del data[name]
        _write(data)
        return True


def get_secret(name: str) -> str | None:
    """Environment wins (a value exported in the shell is deliberate)."""
    val = os.environ.get(name)
    if val:
        return val
    return _read().get(name) or None


def stored_names() -> set[str]:
    return set(_read())


def expand(value: Any) -> Any:
    """Replace ``${VAR}`` / ``${VAR:-default}`` in strings, recursively.

    An unknown variable without a default is left as written, so the server's
    own error (or ``missing()``) points at it.
    """
    if isinstance(value, str):
        stored = _read() if "${" in value else {}

        def sub(m: re.Match[str]) -> str:
            name, default = m.group(1), m.group(2)
            got = os.environ.get(name) or stored.get(name)
            if got:
                return got
            return default if default is not None else m.group(0)

        return _REF_RE.sub(sub, value)
    if isinstance(value, list):
        return [expand(v) for v in value]
    if isinstance(value, dict):
        return {k: expand(v) for k, v in value.items()}
    return value


def refs(value: Any) -> list[str]:
    """Variable names referenced (without a default) anywhere in ``value``, in order."""
    found: list[str] = []

    def walk(v: Any) -> None:
        if isinstance(v, str):
            for m in _REF_RE.finditer(v):
                if m.group(2) is None and m.group(1) not in found:
                    found.append(m.group(1))
        elif isinstance(v, (list, tuple)):
            for x in v:
                walk(x)
        elif isinstance(v, dict):
            for x in v.values():
                walk(x)

    walk(value)
    return found


def missing(value: Any) -> list[str]:
    """Referenced variables that have no value yet."""
    return [n for n in refs(value) if not get_secret(n)]


def is_placeholder(value: str) -> bool:
    v = (value or "").strip()
    return not v or bool(_PLACEHOLDER_RE.match(v))


def looks_secret(name: str) -> bool:
    return bool(_SECRET_NAME_RE.search(name or ""))


def var_name(server: str, key: str) -> str:
    """A stable variable name for a server's header / env key: ``MCP_LINEAR_API_KEY``."""
    base = re.sub(r"[^A-Za-z0-9]+", "_", f"MCP_{server}_{key}").strip("_").upper()
    return base if not base[0].isdigit() else f"_{base}"


_AUTH_SCHEME_RE = re.compile(r"^(Bearer|Basic|Token)\s+(.+)$", re.I)


def protect_headers(server: str, headers: dict[str, str]) -> tuple[dict[str, str], dict[str, str]]:
    """Move credentials in ``headers`` to the store. Returns ``(headers_for_config, stored)``.

    ``Authorization: Bearer abc`` becomes ``Bearer ${MCP_<SERVER>_AUTHORIZATION}``.
    A value that is already a ``${…}`` reference, or a placeholder, is left as a
    reference for the user to fill in (``stored`` then holds nothing for it).
    """
    out: dict[str, str] = {}
    stored: dict[str, str] = {}
    for key, raw in (headers or {}).items():
        val = str(raw)
        if "${" in val or not looks_secret(key):
            out[key] = val
            continue
        scheme = ""
        secret = val
        m = _AUTH_SCHEME_RE.match(val)
        if m:
            scheme, secret = m.group(1) + " ", m.group(2)
        var = var_name(server, key)
        out[key] = f"{scheme}${{{var}}}"
        if not is_placeholder(secret):
            stored[var] = secret
    return out, stored


def protect_env(server: str, env: dict[str, str]) -> tuple[dict[str, str], dict[str, str]]:
    """Same for a stdio server's ``env``: ``KEY=abc`` becomes ``KEY=${KEY}`` + a stored value."""
    out: dict[str, str] = {}
    stored: dict[str, str] = {}
    for key, raw in (env or {}).items():
        val = str(raw)
        if "${" in val:
            out[key] = val
            continue
        if looks_secret(key) or is_placeholder(val):
            out[key] = f"${{{key}}}"
            if not is_placeholder(val):
                stored[key] = val
        else:
            out[key] = val
    return out, stored
