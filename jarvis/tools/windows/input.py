"""Keyboard and mouse input on Windows."""
import ctypes


def _send_keys(seq: str, *, with_spaces: bool = False) -> None:
    from pywinauto.keyboard import send_keys  # type: ignore
    send_keys(seq, with_spaces=with_spaces, pause=0.02)

_KEY_MAP = {
    "return": "{ENTER}",
    "enter": "{ENTER}",
    "tab": "{TAB}",
    "space": " ",
    "delete": "{DELETE}",
    "backspace": "{BACKSPACE}",
    "escape": "{ESC}",
    "esc": "{ESC}",
    "left": "{LEFT}",
    "right": "{RIGHT}",
    "down": "{DOWN}",
    "up": "{UP}",
    "home": "{HOME}",
    "end": "{END}",
    "pageup": "{PGUP}",
    "pagedown": "{PGDN}",
    "f1": "{F1}", "f2": "{F2}", "f3": "{F3}", "f4": "{F4}",
    "f5": "{F5}", "f6": "{F6}", "f7": "{F7}", "f8": "{F8}",
    "f9": "{F9}", "f10": "{F10}", "f11": "{F11}", "f12": "{F12}",
}


def type_text(text: str) -> str:
    if not text:
        return "ERROR: text empty"
    try:
        _send_keys(text, with_spaces=True)
        return "typed"
    except Exception as e:
        return f"ERROR: {e}"


def key_press(keys: str) -> str:
    parts = [p.strip().lower() for p in (keys or "").split("+")]
    mods = {
        "ctrl": "^", "control": "^",
        "shift": "+", "alt": "%", "win": "{VK_LWIN}",
    }
    mod_prefix = ""
    key_parts = []
    for p in parts:
        if p in mods:
            mod_prefix += mods[p]
        else:
            key_parts.append(p)
    if len(key_parts) != 1:
        return f"ERROR: bad key spec: {keys}"
    k = key_parts[0]
    token = _KEY_MAP.get(k, k if len(k) == 1 else "{" + k.upper() + "}")
    try:
        _send_keys(mod_prefix + token)
        return f"pressed {keys}"
    except Exception as e:
        return f"ERROR: {e}"


def click_menu(app: str, path: list) -> str:
    if not path:
        return "ERROR: path required"
    from .ui import click_element, wait

    # Alt activates menu bar on most Windows apps; then type first letter / arrow nav
    key_press("alt")
    wait(0.3)
    for i, item in enumerate(path):
        if i == 0:
            # first menu: often underlined letter — try typing prefix
            ch = item[0].lower() if item else ""
            if ch:
                type_text(ch)
            wait(0.4)
        else:
            r = click_element(app, item, role="menuitem")
            if r.startswith("ERROR"):
                key_press("down")
                wait(0.2)
            wait(0.3)
    return f"menu path: {' > '.join(path)}"


def click_at(x: int, y: int) -> str:
    try:
        ctypes.windll.user32.SetCursorPos(int(x), int(y))
        ctypes.windll.user32.mouse_event(0x0002, 0, 0, 0, 0)  # down
        ctypes.windll.user32.mouse_event(0x0004, 0, 0, 0, 0)  # up
        return f"clicked at ({x},{y})"
    except Exception as e:
        return f"ERROR: {e}"
