"""API keys for providers discovered from models.dev (``md:<id>``).

Built-in providers keep one key file each; catalog providers share
``~/.config/harness-agent/provider_keys.json`` (mode 600), keyed by provider
id. A key can also come from the env var models.dev names for the provider
(``DEEPSEEK_API_KEY``, ``GEMINI_API_KEY`` …) — that one wins and is shown as
read-only, like the built-in providers' env keys.

Two env cases are deliberately *not* picked up on their own:
  * a var several catalog providers read (``MINIMAX_API_KEY`` serves four
    MiniMax endpoints) — guessing would list endpoints the key doesn't fit;
  * a var too common to mean intent (``GITHUB_TOKEN``).
For those the user saves the provider in ``/key``; typing ``$MINIMAX_API_KEY``
there stores a reference to the variable instead of a copy of the key.
"""
from __future__ import annotations

import json
import os
import re

from ..constants import paths
from . import models_dev

_REF = re.compile(r"^\$\{?([A-Za-z_][A-Za-z0-9_]*)\}?$")


def _file():
    return paths.PROVIDER_KEYS_FILE


def _read() -> dict[str, str]:
    try:
        raw = json.loads(_file().read_text())
    except (OSError, ValueError):
        return {}
    if not isinstance(raw, dict):
        return {}
    return {k: v for k, v in raw.items() if isinstance(k, str) and isinstance(v, str) and v.strip()}


def _write(data: dict[str, str]) -> None:
    path = _file()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    # Created 600 from the start — never world-readable, not even briefly.
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as fh:
        json.dump(data, fh, indent=2, sort_keys=True)
    os.replace(tmp, path)
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass


def _expand(value: str) -> str:
    m = _REF.match(value.strip())
    if m:
        return os.getenv(m.group(1), "").strip()
    return value.strip()


def is_reference(value: str) -> bool:
    return bool(_REF.match((value or "").strip()))


def env_var_for(provider: str, *, _shared: frozenset[str] | None = None) -> str:
    """The env var this provider's key is read from automatically ("" = none)."""
    p = models_dev.get_provider(provider)
    if p is None:
        return ""
    shared = models_dev.shared_env_vars() if _shared is None else _shared
    for var in p.key_vars:
        if var in shared or var in models_dev.GENERIC_ENV:
            continue
        if os.getenv(var, "").strip():
            return var
    return ""


def key_source(
    provider: str,
    *,
    _saved: dict[str, str] | None = None,
    _shared: frozenset[str] | None = None,
) -> tuple[str, str]:
    """``(source, value)`` — source is ``env`` | ``file`` | ``none``."""
    var = env_var_for(provider, _shared=_shared)
    if var:
        return "env", os.environ[var].strip()
    saved = (_read() if _saved is None else _saved).get(provider, "")
    if saved:
        return "file", _expand(saved)
    return "none", ""


def all_sources() -> dict[str, tuple[str, str]]:
    """``key_source`` for every catalog provider, reading the file once."""
    saved, shared = _read(), models_dev.shared_env_vars()
    return {pid: key_source(pid, _saved=saved, _shared=shared) for pid in models_dev.providers()}


def saved_value(provider: str) -> str:
    """What was saved, unexpanded (``$VAR`` references stay references)."""
    return _read().get(provider, "")


def get_key(provider: str) -> str:
    return key_source(provider)[1]


def has_key(provider: str) -> bool:
    return bool(get_key(provider))


def save(provider: str, key: str) -> None:
    key = (key or "").strip()
    if not key:
        raise ValueError("key is empty")
    data = _read()
    data[provider] = key
    _write(data)


def delete(provider: str) -> bool:
    data = _read()
    if provider not in data:
        return False
    del data[provider]
    _write(data)
    return True


def connected() -> list[str]:
    """Catalog providers that can be used right now (key + resolvable URL),
    in display order (by name)."""
    provs = models_dev.providers()
    if not provs:
        return []
    out = [
        pid for pid, (src, value) in all_sources().items()
        if src != "none" and value and models_dev.base_url(provs[pid]) is not None
    ]
    out.sort(key=lambda pid: provs[pid].name.lower())
    return out
