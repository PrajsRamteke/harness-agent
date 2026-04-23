"""Keyboard & mouse input: type_text, key_press, click_menu, click_at."""
from .applescript import _osa

_KEYCODES = {
    "return": 36, "enter": 36, "tab": 48, "space": 49, "delete": 51, "backspace": 51,
    "escape": 53, "esc": 53, "left": 123, "right": 124, "down": 125, "up": 126,
    "home": 115, "end": 119, "pageup": 116, "pagedown": 121,
    "f1": 122, "f2": 120, "f3": 99, "f4": 118, "f5": 96, "f6": 97, "f7": 98,
    "f8": 100, "f9": 101, "f10": 109, "f11": 103, "f12": 111,
}


def type_text(text: str) -> str:
    # escape backslashes and quotes for AppleScript string literal
    esc = text.replace("\\", "\\\\").replace('"', '\\"')
    return _osa(f'tell application "System Events" to keystroke "{esc}"')


def key_press(keys: str) -> str:
    """
    Press a key or chord. Examples: 'return', 'cmd+f', 'cmd+shift+t', 'down'.
    A single printable character (like 'a') will also work.
    """
    parts = [p.strip().lower() for p in keys.split("+")]
    mods = {"cmd": "command down", "command": "command down",
            "shift": "shift down", "opt": "option down", "option": "option down",
            "alt": "option down", "ctrl": "control down", "control": "control down"}
    mod_flags = [mods[p] for p in parts if p in mods]
    key = [p for p in parts if p not in mods]
    if len(key) != 1:
        return f"ERROR: bad key spec: {keys}"
    k = key[0]
    using = ""
    if mod_flags:
        using = " using {" + ", ".join(mod_flags) + "}"
    if k in _KEYCODES:
        return _osa(f'tell application "System Events" to key code {_KEYCODES[k]}{using}')
    # printable char
    esc = k.replace("\\", "\\\\").replace('"', '\\"')
    return _osa(f'tell application "System Events" to keystroke "{esc}"{using}')


def click_menu(app: str, path: list) -> str:
    """Click a menu item. path = ['File', 'New Window'] or ['Edit','Find','Find…']."""
    if not path:
        return "ERROR: path required"
    # simpler: use hierarchy: menu bar item 1 → menu 1 → menu item "x" → (optional submenu)
    top = path[0]
    if len(path) == 1:
        script = f'''
        tell application "System Events" to tell process "{app}"
            click menu bar item "{top}" of menu bar 1
        end tell
        '''
        return _osa(script)
    # build nested: click menu item "last" of menu "second-to-last" of menu item "..." ... of menu "top" of menu bar item "top" of menu bar 1
    ref = f'menu item "{path[-1]}"'
    cur = ref
    for mid in reversed(path[1:-1]):
        cur = f'{cur} of menu "{mid}" of menu item "{mid}"'
    cur = f'{cur} of menu "{path[0]}" of menu bar item "{path[0]}" of menu bar 1'
    script = f'''
    tell application "System Events" to tell process "{app}"
        click {cur}
    end tell
    '''
    return _osa(script)


def click_at(x: int, y: int) -> str:
    return _osa(f'tell application "System Events" to click at {{{int(x)}, {int(y)}}}')
