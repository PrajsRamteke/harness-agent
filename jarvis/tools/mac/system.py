"""System-level tools: open_url, notify, speck, shortcut_run, mac_control."""
import subprocess
from ...constants import MAX_TOOL_OUTPUT, SPECK_MAX_CHARS
from .applescript import _osa
from ..shell import run_bash


def open_url(url: str) -> str:
    r = subprocess.run(["open", url], capture_output=True, text=True)
    return "opened" if r.returncode == 0 else f"ERROR: {r.stderr.strip()}"


def notify(title: str, message: str = "") -> str:
    t = title.replace('"', '\\"')
    m = message.replace('"', '\\"')
    return _osa(f'display notification "{m}" with title "{t}"')


def speck(
    text: str,
    voice: str = "",
    rate: int = 0,
) -> str:
    """Speak text aloud with the macOS `say` command (default output device)."""
    t = (text or "").strip()
    if not t:
        return "ERROR: text is empty"
    if len(t) > SPECK_MAX_CHARS:
        return f"ERROR: text exceeds {SPECK_MAX_CHARS} characters (split into shorter speck calls)"
    cmd = ["say"]
    if voice:
        cmd.extend(["-v", voice])
    if rate and rate > 0:
        cmd.extend(["-r", str(int(rate))])
    try:
        r = subprocess.run(
            cmd,
            input=t,
            text=True,
            capture_output=True,
            timeout=max(30.0, min(600.0, len(t) * 0.05)),
        )
    except subprocess.TimeoutExpired:
        return "ERROR: say timed out"
    except OSError as e:
        return f"ERROR: {e}"
    if r.returncode != 0:
        err = (r.stderr or r.stdout or "").strip()
        return f"ERROR: say failed: {err[:MAX_TOOL_OUTPUT]}"
    return "spoke"


def shortcut_run(name: str, input_text: str = "") -> str:
    """Run an Apple Shortcut by name. Optional text input piped in."""
    cmd = ["shortcuts", "run", name]
    try:
        r = subprocess.run(cmd, input=input_text, capture_output=True, text=True, timeout=120)
        out = r.stdout + (f"\n[stderr]\n{r.stderr}" if r.stderr else "")
        return out[:MAX_TOOL_OUTPUT] or "OK"
    except Exception as e:
        return f"ERROR: {e}"


def mac_control(action: str, value: str = "") -> str:
    """
    System controls. action ∈ {volume, mute, unmute, brightness, battery, wifi_on,
    wifi_off, sleep, lock, dark_mode, light_mode, toggle_dark}. value optional.
    """
    a = action.lower()
    if a == "volume":
        return _osa(f'set volume output volume {int(value) if value else 50}')
    if a == "mute":
        return _osa('set volume with output muted')
    if a == "unmute":
        return _osa('set volume without output muted')
    if a == "battery":
        return run_bash("pmset -g batt | tail -1", 5)
    if a == "wifi_on":
        return run_bash("networksetup -setairportpower en0 on", 5)
    if a == "wifi_off":
        return run_bash("networksetup -setairportpower en0 off", 5)
    if a == "sleep":
        return run_bash("pmset sleepnow", 5)
    if a == "lock":
        return _osa('tell application "System Events" to keystroke "q" using {control down, command down}')
    if a == "dark_mode":
        return _osa('tell application "System Events" to tell appearance preferences to set dark mode to true')
    if a == "light_mode":
        return _osa('tell application "System Events" to tell appearance preferences to set dark mode to false')
    if a == "toggle_dark":
        return _osa('tell application "System Events" to tell appearance preferences to set dark mode to not dark mode')
    if a == "brightness":
        # no native AppleScript for brightness; best-effort via key codes F1/F2
        return "brightness not directly scriptable; use key_press 'f1'/'f2' if function keys mapped"
    return f"ERROR: unknown action {action}"
