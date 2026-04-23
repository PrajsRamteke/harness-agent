"""Read accessibility UI tree and click elements by text."""
import subprocess, time
from ._jxa_scripts import READ_UI_JXA, FIND_CLICK_JXA


def read_ui(app: str = "", max_depth: int = 7, max_lines: int = 400, max_chars: int = 14000) -> str:
    """
    Read the accessibility UI tree of `app` (blank = frontmost). Returns a
    hierarchical text dump: role, name, value, description, and center-point
    coordinates for each element. No screenshots, no OCR.
    """
    try:
        r = subprocess.run(
            ["osascript", "-l", "JavaScript", "-e", READ_UI_JXA,
             app or "", str(max_depth), str(max_lines)],
            capture_output=True, text=True, timeout=25,
        )
        if r.returncode != 0:
            return f"ERROR: {r.stderr.strip() or r.stdout.strip()}"
        out = r.stdout.rstrip()
        if len(out) > max_chars:
            out = out[:max_chars] + f"\n… [truncated, {len(out)} chars total]"
        return out or "(empty UI tree)"
    except subprocess.TimeoutExpired:
        return "TIMEOUT reading UI (app may be unresponsive or not accessibility-enabled)"


def click_element(app: str, query: str, role: str = "", nth: int = 1) -> str:
    """
    Find a UI element in `app` whose name/value/description contains `query`
    (case-insensitive) and click it. Optional `role` filter (e.g. 'button',
    'row', 'textfield'). `nth` picks the nth match (1-based).
    """
    try:
        r = subprocess.run(
            ["osascript", "-l", "JavaScript", "-e", FIND_CLICK_JXA,
             app, query, role, str(nth)],
            capture_output=True, text=True, timeout=15,
        )
        return (r.stdout or r.stderr).strip() or "OK"
    except subprocess.TimeoutExpired:
        return "TIMEOUT"


def wait(seconds: float = 0.8) -> str:
    """Sleep — useful after launching/clicking so the UI settles before read_ui."""
    time.sleep(max(0.0, min(float(seconds), 10.0)))
    return f"slept {seconds}s"


def check_permissions() -> str:
    """Verify Accessibility permission. Returns a diagnostic string."""
    probe = 'tell application "System Events" to get name of first process whose frontmost is true'
    r = subprocess.run(["osascript", "-e", probe], capture_output=True, text=True, timeout=5)
    if r.returncode == 0:
        return f"Accessibility OK. Frontmost app: {r.stdout.strip()}"
    return ("ACCESSIBILITY DENIED.\n"
            "Open System Settings → Privacy & Security → Accessibility, add & enable your "
            "Terminal app (Terminal.app / iTerm / the one you launched this agent from), then "
            "also enable it under 'Automation' if prompted. Error: " + r.stderr.strip())
