"""The web remote's chosen look (theme, mode, accent, glass, layout), kept on this computer.

The page used to keep these only in the browser's ``localStorage`` — which is
per origin. Every project runs its own Jarvis on its own port (and the same
one is reached as ``127.0.0.1``, ``localhost``, a LAN address or a tunnel), so
each of those origins had its own theme and a choice made in one never
reached the others. This file is the one source of truth they all share:

``GET /api/appearance`` → ``{ok, appearance}`` (``appearance`` is ``null`` until
a theme has been chosen once); ``POST /api/appearance {appearance}`` stores it
and every open page gets an ``appearance`` event.

The shape mirrors ``prefs`` in ``static/js/theme.js``; ``clean`` keeps only
values that script would accept, so a bad write can never break the page.
"""
from __future__ import annotations

import json
import os
import re
import threading
from typing import Any

from ..constants import CONFIG_DIR

APPEARANCE_FILE = CONFIG_DIR / "web_appearance.json"

MODES = ("system", "light", "dark")
LAYOUT_OPTIONS: dict[str, tuple[str, ...]] = {
    "text": ("s", "m", "l"),
    "width": ("narrow", "default", "wide"),
    "radius": ("sharp", "default", "round"),
    "font": ("geist", "system"),
    "pattern": ("none", "dots", "grid", "lines"),
}
LAYOUT_DEFAULTS = {"text": "m", "width": "default", "radius": "default", "font": "geist", "pattern": "none"}
SOFT_AMT_RANGE = (10, 100)
GLASS_OP_RANGE = (20, 100)
_NAME_RE = re.compile(r"^[a-z0-9_-]{1,24}$")   # palette / accent ids
_HEX_RE = re.compile(r"^#[0-9a-f]{6}$")

_lock = threading.Lock()


def _bool(value: Any) -> bool:
    return value is True or value == 1 or str(value).lower() in ("1", "true")


def _int_in(value: Any, low: int, high: int) -> int | None:
    try:
        n = int(value)
    except (TypeError, ValueError):
        return None
    return max(low, min(high, n))


def clean(raw: Any) -> dict[str, Any] | None:
    """The sanitized appearance, or ``None`` when ``raw`` isn't one."""
    if not isinstance(raw, dict):
        return None
    out: dict[str, Any] = {}
    palette = str(raw.get("palette") or "").strip().lower()
    out["palette"] = palette if _NAME_RE.match(palette) else ""
    mode = str(raw.get("mode") or "").strip().lower()
    out["mode"] = mode if mode in MODES else "dark"
    out["soft"] = _bool(raw.get("soft"))
    out["softAmt"] = _int_in(raw.get("softAmt"), *SOFT_AMT_RANGE) or SOFT_AMT_RANGE[1]
    out["glass"] = _bool(raw.get("glass"))
    out["glassOp"] = _int_in(raw.get("glassOp"), *GLASS_OP_RANGE)
    layout = raw.get("layout") if isinstance(raw.get("layout"), dict) else {}
    out["layout"] = {
        name: layout.get(name) if layout.get(name) in opts else LAYOUT_DEFAULTS[name]
        for name, opts in LAYOUT_OPTIONS.items()
    }
    accent = str(raw.get("accent") or "").strip().lower()
    out["accent"] = accent if _NAME_RE.match(accent) else "violet"
    custom = str(raw.get("custom") or "").strip().lower()
    out["custom"] = custom if _HEX_RE.match(custom) else ""
    return out


def load() -> dict[str, Any] | None:
    """What was last saved; ``None`` if nothing was, or the file is unreadable."""
    try:
        return clean(json.loads(APPEARANCE_FILE.read_text(encoding="utf-8")))
    except (OSError, ValueError):
        return None


def save(raw: Any) -> dict[str, Any] | None:
    """Store ``raw`` (sanitized) for every page; returns what was stored, or ``None`` if invalid."""
    value = clean(raw)
    if value is None:
        return None
    with _lock:
        APPEARANCE_FILE.parent.mkdir(parents=True, exist_ok=True)
        tmp = APPEARANCE_FILE.with_suffix(".tmp")
        tmp.write_text(json.dumps(value, indent=2), encoding="utf-8")
        os.replace(tmp, APPEARANCE_FILE)  # a reader never sees half a file
    return value
