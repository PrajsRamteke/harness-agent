"""Tool wrappers that call the Chrome extension over the browser bridge."""
from __future__ import annotations

import json
from typing import Any

from .server import bridge_state


def _call(name: str, **args: Any) -> str:
    result = bridge_state().call_tool(name, args)
    return json.dumps(result, ensure_ascii=False, indent=2)


def browser_status() -> str:
    state = bridge_state()
    return json.dumps({
        "connected": state.connected,
        "extension": state.last_hello,
    }, ensure_ascii=False, indent=2)


def browser_navigate(url: str, new_tab: bool = False) -> str:
    return _call("navigate", url=url, newTab=new_tab)


def browser_find_tab(url: str, active: bool = False) -> str:
    return _call("find_tab", url=url, active=active)


def browser_snapshot() -> str:
    return _call("snapshot")


def browser_page_context(max_chars: int = 9000) -> str:
    return _call("page_context", maxChars=max_chars)


def browser_click(selector: str) -> str:
    return _call("click", selector=selector)


def browser_mouse_click(selector: str) -> str:
    return _call("mouse_click", selector=selector)


def browser_fill(selector: str, value: str) -> str:
    return _call("fill", selector=selector, value=value)


def browser_type(text: str) -> str:
    return _call("key_type", text=text)


def browser_keys(keys: str, repeat: int = 1) -> str:
    return _call("send_keys", keys=keys, repeat=repeat)


def browser_evaluate(code: str) -> str:
    return _call("evaluate", code=code)


def browser_screenshot(selector: str = "", format: str = "png") -> str:
    return _call("screenshot", selector=selector, format=format)


BROWSER_TOOLS = [
    {"name": "browser_status", "description": "Check Harness WebBridge Chrome extension connection. Call first before any browser_* tool.",
     "input_schema": {"type": "object", "properties": {}}},
    {"name": "browser_navigate", "description": "REQUIRED for opening URLs in Chrome. Do not use launch_app, open_url, or AppleScript for web navigation.",
     "input_schema": {"type": "object", "properties": {
         "url": {"type": "string"},
         "new_tab": {"type": "boolean", "description": "Open in a new tab. Default false."}},
         "required": ["url"]}},
    {"name": "browser_find_tab", "description": "Attach to an existing Chrome tab by URL or hostname.",
     "input_schema": {"type": "object", "properties": {
         "url": {"type": "string"},
         "active": {"type": "boolean", "description": "Prefer the active tab. Default false."}},
         "required": ["url"]}},
    {"name": "browser_snapshot", "description": "REQUIRED before clicking/filling web pages. Returns accessibility tree with refs like @e1.",
     "input_schema": {"type": "object", "properties": {}}},
    {"name": "browser_page_context", "description": "Read current Chrome page URL, title, selection, and visible text.",
     "input_schema": {"type": "object", "properties": {
         "max_chars": {"type": "integer", "description": "Maximum visible text chars. Default 9000."}}}},
    {"name": "browser_click", "description": "Click a web element in Chrome (CSS selector or @e ref). Do not use click_element or AppleScript.",
     "input_schema": {"type": "object", "properties": {
         "selector": {"type": "string"}}, "required": ["selector"]}},
    {"name": "browser_mouse_click", "description": "Physical mouse click in Chrome at a CSS selector or @e ref.",
     "input_schema": {"type": "object", "properties": {
         "selector": {"type": "string"}}, "required": ["selector"]}},
    {"name": "browser_fill", "description": "Fill a web form field in Chrome. Do not use type_text or read_ui.",
     "input_schema": {"type": "object", "properties": {
         "selector": {"type": "string"},
         "value": {"type": "string"}}, "required": ["selector", "value"]}},
    {"name": "browser_type", "description": "Type into the focused Chrome element. Do not use type_text for web forms.",
     "input_schema": {"type": "object", "properties": {
         "text": {"type": "string"}}, "required": ["text"]}},
    {"name": "browser_keys", "description": "Send keyboard keys to Chrome (Enter, Escape, Mod+A, etc.).",
     "input_schema": {"type": "object", "properties": {
         "keys": {"type": "string"},
         "repeat": {"type": "integer"}}, "required": ["keys"]}},
    {"name": "browser_evaluate", "description": "Run JavaScript in the current Chrome tab.",
     "input_schema": {"type": "object", "properties": {
         "code": {"type": "string"}}, "required": ["code"]}},
    {"name": "browser_screenshot", "description": "Capture the Chrome viewport or element as base64 PNG/JPEG.",
     "input_schema": {"type": "object", "properties": {
         "selector": {"type": "string"},
         "format": {"type": "string", "enum": ["png", "jpeg"]}}}},
]


browser_bridge_tools = {
    "browser_status": browser_status,
    "browser_navigate": browser_navigate,
    "browser_find_tab": browser_find_tab,
    "browser_snapshot": browser_snapshot,
    "browser_page_context": browser_page_context,
    "browser_click": browser_click,
    "browser_mouse_click": browser_mouse_click,
    "browser_fill": browser_fill,
    "browser_type": browser_type,
    "browser_keys": browser_keys,
    "browser_evaluate": browser_evaluate,
    "browser_screenshot": browser_screenshot,
}
