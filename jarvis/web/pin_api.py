"""Pinned context for the web remote's Pin dialog (``static/js/pin.js``).

``GET /api/pin`` → ``storage/pin.public()``; ``POST /api/pin {op, …}`` runs on
the terminal's main thread (``bridge.request_action("pin", …)``), then every
open page gets a ``pin`` event. Same text and file as the terminal's /pin.

ops: ``add {text}`` · ``save {text}`` (the whole text) · ``update {line, expect, text}``
· ``remove {line, expect}`` · ``toggle {enabled}`` · ``clear``. ``expect`` is the
line as the page showed it — a line changed elsewhere meanwhile is refused
(``code: "changed"``) instead of overwriting the wrong one.
"""
from __future__ import annotations

from typing import Any

from ..storage import pin as pin_store

MAX_CHARS = 20_000


def apply(data: dict[str, Any]) -> dict[str, Any]:
    op = str(data.get("op") or "").strip()
    text = str(data.get("text") or "")
    changed = {"ok": False, "code": "changed",
               "error": "That line changed somewhere else. The list is up to date now — try again."}

    if op in ("add", "save") and len(text) > MAX_CHARS:
        return {"ok": False, "error": f"Keep pinned context under {MAX_CHARS:,} characters."}

    if op == "add":
        if not text.strip():
            return {"ok": False, "error": "Write something to pin."}
        pin_store.append_pin(text)
    elif op == "save":
        pin_store.set_pin_text(text)
    elif op in ("update", "remove"):
        try:
            line = int(data.get("line") or 0)
        except (TypeError, ValueError):
            line = 0
        expect = data.get("expect")
        expect = None if expect is None else str(expect)
        if op == "update" and len(pin_store.pin_text()) + len(text) > MAX_CHARS:
            return {"ok": False, "error": f"Keep pinned context under {MAX_CHARS:,} characters."}
        ok = (pin_store.update_line(line, text, expect=expect) if op == "update"
              else pin_store.remove_line(line, expect=expect))
        if not ok:
            return {**changed, "pin": pin_store.public()}
    elif op == "toggle":
        pin_store.set_enabled(bool(data.get("enabled")))
    elif op == "clear":
        pin_store.clear_pin()
    else:
        return {"ok": False, "error": f"unknown op: {op}"}
    return {"ok": True, "pin": pin_store.public()}
