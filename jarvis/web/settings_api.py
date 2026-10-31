"""Every setting for the web remote's Settings dialog (``static/js/settings.js``).

The web twin of the terminal's ``/settings``, but as switches, steppers and
choices instead of raw keys. ``GET /api/settings/all`` → ``public()``;
``POST /api/settings/all {op: set|reset, key, value?}`` runs on the terminal's
main thread (``bridge.request_action("settings_set", …)``) — the terminal then
applies what it shows itself (theme, pet, QR, sticky prompt; see
``tui/mixins/web_remote._apply_web_setting``) — and every open page gets a
``settings_all`` event.

Each key goes through the same path the terminal's own control uses (a global
scope switch invalidates its cache, MCP on/off connects / disconnects, thinking
is checked against the model …), so a change here is never half-applied.
Model, provider, agent and thinking effort live in the dialog too, but the page
builds those rows from the session state and opens the existing pickers.
"""
from __future__ import annotations

from typing import Any

from .. import state
from ..storage.settings import DEFAULTS, SETTINGS_FILE, get_settings

# key → (section, label, description, kind, extra). kinds: switch · number · choice.
# "session.*" keys live only in this run (never written to settings.json).
SECTIONS: list[tuple[str, str]] = [
    ("chat", "Chat"),
    ("sources", "Agents, skills & tools"),
    ("subagents", "Parallel agents"),
    ("terminal", "Terminal"),
    ("web", "Web remote"),
]

ITEMS: list[dict[str, Any]] = [
    {"key": "trace.on", "section": "chat", "label": "Show thinking",
     "desc": "The model's reasoning in the chat", "kind": "switch", "icon": "brain"},
    {"key": "session.auto_approve", "section": "chat", "label": "Auto-approve commands",
     "desc": "Run shell commands without asking (this run only)", "kind": "switch", "icon": "shield"},
    {"key": "pin.enabled", "section": "chat", "label": "Pinned context",
     "desc": "Send your pinned rules with every message", "kind": "switch", "icon": "pin",
     "link": {"picker": "pin", "label": "Edit"}},

    {"key": "agent.global", "section": "sources", "label": "Global agents",
     "desc": "Agents from ~/.harness, ~/.claude, ~/.cursor …", "kind": "switch", "icon": "sparkles"},
    {"key": "skills.global", "section": "sources", "label": "Global skills",
     "desc": "Skills from your home folder, not just this project", "kind": "switch", "icon": "book-open"},
    {"key": "commands.global", "section": "sources", "label": "Global commands",
     "desc": "Custom /commands from your home folder", "kind": "switch", "icon": "terminal"},
    {"key": "mcp.enabled", "section": "sources", "label": "MCP servers",
     "desc": "Off disconnects every server", "kind": "switch", "icon": "plug",
     "link": {"picker": "mcp", "label": "Manage"}},
    {"key": "mcp.global", "section": "sources", "label": "Global MCP servers",
     "desc": "Servers from Claude Code, Cursor, OpenCode … configs", "kind": "switch", "icon": "globe"},

    {"key": "subagents.enabled", "section": "subagents", "label": "Parallel agents",
     "desc": "Let Jarvis split big tasks across helpers", "kind": "switch", "icon": "users"},
    {"key": "subagents.max_parallel", "section": "subagents", "label": "At once",
     "desc": "Helpers running at the same time", "kind": "number", "min": 1, "max": 20},
    {"key": "subagents.max_steps", "section": "subagents", "label": "Steps per agent",
     "desc": "Model requests each helper may make", "kind": "number", "min": 4, "max": 200, "step": 4},
    {"key": "subagents.timeout_min", "section": "subagents", "label": "Time limit",
     "desc": "Minutes per helper", "kind": "number", "min": 1, "max": 120, "unit": "min"},

    {"key": "theme", "section": "terminal", "label": "Terminal theme",
     "desc": "Colours of the terminal app", "kind": "choice", "icon": "palette"},
    {"key": "ui.sticky_prompt", "section": "terminal", "label": "Sticky prompt",
     "desc": "Pin your prompt above its reply while you scroll", "kind": "switch", "icon": "pin"},
    {"key": "pet.enabled", "section": "terminal", "label": "Pet",
     "desc": "Your buddy in the sidebar", "kind": "switch", "icon": "cat"},
    {"key": "pet.nudges", "section": "terminal", "label": "Pet nudges",
     "desc": "Stretch breaks, full context, late nights", "kind": "switch", "icon": "message-circle"},
    {"key": "pet.notify", "section": "terminal", "label": "Desktop notifications",
     "desc": "When a long reply finishes while you're away", "kind": "switch", "icon": "bell"},

    {"key": "web.qr", "section": "web", "label": "QR in the terminal",
     "desc": "Keep the connect QR in the terminal's corner", "kind": "switch", "icon": "qr-code"},
    {"key": "web.tunnel", "section": "web", "label": "Anywhere link",
     "desc": "Service behind the public link", "kind": "choice", "icon": "globe",
     "options": [["auto", "Auto"], ["cloudflare", "Cloudflare"], ["ngrok", "ngrok"]]},
    {"key": "web.keep_awake", "section": "web", "label": "Keep the Mac awake",
     "desc": "While the Anywhere link is on", "kind": "switch", "icon": "coffee"},
]

_BY_KEY = {it["key"]: it for it in ITEMS}

# Keys the running session holds (a switch elsewhere changes them without
# touching settings.json first): read from ``state``, not the file.
_STATE_KEYS = {
    "trace.on": "show_internal",
    "session.auto_approve": "auto_approve",
    "agent.global": "global_agents",
    "skills.global": "global_skills",
    "commands.global": "global_commands",
    "mcp.global": "global_mcp",
}

_DEFAULTS_EXTRA = {"session.auto_approve": False, "commands.global": True}


def _default(key: str) -> Any:
    if key in _DEFAULTS_EXTRA:
        return _DEFAULTS_EXTRA[key]
    cur: Any = DEFAULTS
    for part in key.split("."):
        if not isinstance(cur, dict) or part not in cur:
            return None
        cur = cur[part]
    return cur


def _value(key: str) -> Any:
    if key in _STATE_KEYS:
        return bool(getattr(state, _STATE_KEYS[key], False))
    if key == "pin.enabled":
        from ..storage import pin as pin_store

        return pin_store.is_enabled()
    if key == "mcp.enabled":
        from ..mcp import toggle

        return toggle.mcp_enabled()
    val = get_settings().get(key)
    return _default(key) if val is None else val


def _theme_options() -> list[list[Any]]:
    """``[name, label, swatch]`` — the swatch colours the menu row (bg + accents)."""
    try:
        from ..tui.theme import PALETTES

        return [[n, n.replace("_", " ").capitalize(),
                 {"bg": p.get("bg_1", ""), "accent": p.get("accent", ""), "alt": p.get("accent_2", "")}]
                for n, p in PALETTES.items()]
    except Exception:
        names = list(getattr(state, "THEMES", {}) or [])
    return [[n, n.replace("_", " ").capitalize()] for n in names]


def public() -> dict[str, Any]:
    """Sections with their rows: ``{key, label, desc, kind, value, default, …}``."""
    sections = []
    for sid, title in SECTIONS:
        rows = []
        for it in ITEMS:
            if it["section"] != sid:
                continue
            row = {k: v for k, v in it.items() if k != "section"}
            if it["key"] == "theme":
                row["options"] = _theme_options()
            try:
                row["value"] = _value(it["key"])
            except Exception:
                row["value"] = _default(it["key"])
            row["default"] = _default(it["key"])
            rows.append(row)
        sections.append({"id": sid, "title": title, "items": rows})
    return {"sections": sections, "file": str(SETTINGS_FILE)}


def _coerce(item: dict[str, Any], value: Any) -> Any:
    kind = item["kind"]
    if kind == "switch":
        if isinstance(value, str):
            return value.strip().lower() in ("1", "true", "on", "yes")
        return bool(value)
    if kind == "number":
        try:
            n = int(float(value))
        except (TypeError, ValueError):
            raise ValueError(f"{item['label']} needs a number") from None
        lo, hi = item.get("min", n), item.get("max", n)
        if not lo <= n <= hi:
            raise ValueError(f"{item['label']}: pick {lo}–{hi}")
        return n
    allowed = [o[0] for o in (item.get("options") or _theme_options())]
    v = str(value or "").strip()
    if v not in allowed:
        raise ValueError(f"{item['label']}: unknown choice '{v}'")
    return v


def _set(key: str, value: Any) -> None:
    """Write ``key`` the way the terminal's own control for it does."""
    if key == "trace.on":
        state.show_internal = value
        state.save_trace_config()
    elif key == "session.auto_approve":
        state.auto_approve = value
    elif key in ("agent.global", "skills.global", "commands.global", "mcp.global"):
        from .actions_api import run_web_action

        action = {"agent.global": "agents_scope", "skills.global": "skills_scope",
                  "commands.global": "commands_scope", "mcp.global": "mcp_scope"}[key]
        field = {"agent.global": "global_agents"}.get(key, "global_" + key.split(".")[0])
        res = run_web_action(action, {field: value}, console_print=lambda *_a, **_k: None)
        if not res.get("ok"):
            raise ValueError(res.get("error") or "couldn't change that")
    elif key == "pin.enabled":
        from ..storage import pin as pin_store

        pin_store.set_enabled(value)
    elif key == "mcp.enabled":
        from ..mcp.install import set_mcp_enabled

        set_mcp_enabled(value)
    else:
        get_settings().set(key, value)
        if key == "theme":
            state._reload_saved_theme()


def apply(data: dict[str, Any]) -> dict[str, Any]:
    """``{op: set, key, value}`` or ``{op: reset, key}`` → ``{ok, key, error?, settings}``."""
    op = str(data.get("op") or "set").strip()
    key = str(data.get("key") or "").strip()
    item = _BY_KEY.get(key)
    if item is None:
        return {"ok": False, "error": f"unknown setting: {key or '(none)'}"}
    try:
        raw = _default(key) if op == "reset" else data.get("value")
        if op not in ("set", "reset"):
            return {"ok": False, "error": f"unknown op: {op}"}
        _set(key, _coerce(item, raw))
    except ValueError as exc:
        return {"ok": False, "key": key, "error": str(exc), "settings": public()}
    return {"ok": True, "key": key, "settings": public()}
