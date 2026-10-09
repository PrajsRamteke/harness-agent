"""Local models on the web — the twin of the terminal's ``/local-models``.

    GET  /api/local[?scan=1]       servers + models (scans when the last look is stale)
    POST /api/local {op: …}        scan · add · remove · detect · context · use

Probing and adding a server are network calls and run on the HTTP handler
thread; switching the session to a local model (``use``) — or off a removed
server that was in use — goes through ``bridge.request_action`` to the TUI
main thread (``run_local_action``). Every change emits a ``local`` event so
open pages redraw, plus ``providers`` because the model list changed. Keys
never come back: rows carry ``has_key`` only.
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Any, Callable

from .. import state

if TYPE_CHECKING:
    from .bridge import WebBridge


def _public() -> dict[str, Any]:
    from ..auth import local_models as lm

    return lm.public(state.provider or "", state.MODEL or "")


def get_local(*, force_scan: bool = False) -> dict[str, Any]:
    from ..auth import local_models as lm

    if lm.enabled() and (force_scan or not lm.scan_is_fresh()):
        lm.scan()
    return _public()


def _run(bridge: "WebBridge | None", action: str, data: dict[str, Any]) -> dict[str, Any]:
    if bridge is not None:
        return bridge.request_action(action, data)
    from ..console import console

    return run_local_action(action, data, console_print=console.print)


def post_local(data: dict[str, Any], bridge: "WebBridge | None" = None) -> dict[str, Any]:
    from ..auth import local_models as lm

    if not lm.enabled():
        return {"ok": False, "error": "Local models are turned off (HARNESS_LOCAL_MODELS=0)"}
    op = str(data.get("op") or "").strip()
    result: dict[str, Any]
    if op == "scan":
        lm.scan(only=str(data["id"]) if data.get("id") else None)
        result = {"ok": True}
    elif op == "add":
        res = lm.add_server(str(data.get("name") or ""), str(data.get("url") or ""),
                            key=str(data.get("key") or ""), force=bool(data.get("force")))
        result = {"ok": bool(res.get("ok")), **{k: v for k, v in res.items() if k != "ok"}}
        if res.get("ok"):
            n = res.get("models", 0)
            state_word = "" if res.get("status") == "online" else " · not answering yet"
            result["message"] = f"Added {res.get('name')} · {n} model{'s' if n != 1 else ''}{state_word}"
    elif op == "remove":
        pid = str(data.get("id") or "")
        was_active = state.provider == pid
        if not lm.remove_server(pid):
            return {"ok": False, "error": "Only servers you added can be removed"}
        result = {"ok": True, "message": "Server removed"}
        if was_active:
            moved = _run(bridge, "local_fallback", {})
            if moved.get("ok"):
                result["message"] = f"Server removed · switched to {moved.get('model') or 'the free tier'}"
    elif op == "detect":
        if not lm.set_detect(str(data.get("server") or ""), bool(data.get("on"))):
            return {"ok": False, "error": "Unknown runtime"}
        if data.get("on"):
            lm.scan(only=lm.provider_id(str(data.get("server"))))
        result = {"ok": True}
    elif op == "context":
        try:
            n = lm.set_context(int(data.get("tokens") or 0))
        except (TypeError, ValueError):
            return {"ok": False, "error": "Pick a context size"}
        result = {"ok": True, "message": f"Ollama models now get a {n // 1024}K window"}
    elif op == "use":
        result = _run(bridge, "local_use", {"id": str(data.get("id") or ""),
                                            "model": str(data.get("model") or "")})
    else:
        return {"ok": False, "error": f"unknown op: {op}"}
    if bridge is not None:
        bridge.emit("local", {})
        if op in ("add", "remove", "use", "detect"):
            bridge.emit("providers", {})
    return {**result, "local": _public()}


def run_local_action(action: str, data: dict[str, Any], *, console_print: Callable) -> dict[str, Any]:
    """``local_*`` web actions. TUI main thread."""
    from ..auth import local_models as lm

    if action == "local_use":
        pid, model = str(data.get("id") or ""), str(data.get("model") or "")
        srv = lm.get_server(pid)
        if srv is None:
            return {"ok": False, "error": "That server is gone"}
        model = model or lm.default_model(pid)
        if not model:
            return {"ok": False, "error": f"{srv.name} has no models Jarvis can see"}
        if state.provider == pid and state.MODEL == model:
            return {"ok": True, "message": f"Already using {model}", "model": model}
        from ..commands.control import _apply_model_selection

        _apply_model_selection(model, source=pid)
        if state.provider != pid or state.MODEL != model:
            return {"ok": False, "error": f"Couldn't switch to {model}. The terminal shows why."}
        return {"ok": True, "message": f"Now using {model} · {srv.name}", "model": model}
    if action == "local_fallback":
        from .providers_api import _fall_back_to_free

        _fall_back_to_free()
        return {"ok": True, "model": state.MODEL}
    return {"ok": False, "error": f"unknown action: {action}"}
