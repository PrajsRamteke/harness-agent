"""App lifecycle controls: launch / focus / quit / list / frontmost."""
import subprocess, time
from ...constants import CLICK_WAIT_ATTEMPTS, CLICK_WAIT_DELAY, SETTLE_WAIT
from .applescript import _osa


def launch_app(name: str) -> str:
    r = subprocess.run(["open", "-a", name], capture_output=True, text=True)
    if r.returncode != 0:
        return f"ERROR: {r.stderr.strip() or 'could not open ' + name}"
    # wait for the process to register with System Events, then bring to front
    for _ in range(CLICK_WAIT_ATTEMPTS):
        time.sleep(CLICK_WAIT_DELAY)
        probe = subprocess.run(
            ["osascript", "-e",
             f'tell application "System Events" to exists (process "{name}")'],
            capture_output=True, text=True, timeout=3,
        )
        if probe.stdout.strip() == "true":
            break
    subprocess.run(["osascript", "-e", f'tell application "{name}" to activate'],
                   capture_output=True, text=True, timeout=5)
    time.sleep(SETTLE_WAIT)
    return f"launched and focused {name}"


def focus_app(name: str) -> str:
    return _osa(f'tell application "{name}" to activate')


def quit_app(name: str) -> str:
    return _osa(f'tell application "{name}" to quit')


def list_apps() -> str:
    return _osa('tell application "System Events" to get name of (every process whose background only is false)')


def frontmost_app() -> str:
    return _osa('tell application "System Events" to get name of first process whose frontmost is true')
