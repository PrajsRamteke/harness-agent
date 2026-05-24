"""App lifecycle controls on Windows."""
import subprocess
import time

from ...constants import CLICK_WAIT_ATTEMPTS, CLICK_WAIT_DELAY, SETTLE_WAIT
from ._ps import run_ps


def _pywinauto():
    try:
        from pywinauto import Desktop  # type: ignore
        return Desktop
    except ImportError:
        return None


def launch_app(name: str) -> str:
    name = (name or "").strip()
    if not name:
        return "ERROR: name required"
    # start tries shell execute / registered app name
    r = subprocess.run(
        ["cmd", "/c", "start", "", name],
        capture_output=True,
        text=True,
        timeout=15,
    )
    if r.returncode != 0:
        return f"ERROR: {r.stderr.strip() or 'could not launch ' + name}"
    for _ in range(CLICK_WAIT_ATTEMPTS):
        time.sleep(CLICK_WAIT_DELAY)
        front = frontmost_app()
        if name.lower() in front.lower():
            break
    time.sleep(SETTLE_WAIT)
    focus_app(name)
    return f"launched {name}"


def focus_app(name: str) -> str:
    name = (name or "").strip()
    Desktop = _pywinauto()
    if Desktop is None:
        return run_ps(
            f"(Get-Process | Where-Object {{$_.MainWindowTitle -like '*{name}*'}} | "
            f"Select-Object -First 1).MainWindowHandle"
        )
    try:
        desktop = Desktop(backend="uia")
        for win in desktop.windows():
            title = win.window_text() or ""
            if name.lower() in title.lower():
                win.set_focus()
                return f"focused {title}"
        return f"ERROR: no window matching '{name}'"
    except Exception as e:
        return f"ERROR: {e}"


def quit_app(name: str) -> str:
    name = (name or "").strip()
    safe = name.replace("'", "''")
    return run_ps(
        f"Get-Process | Where-Object {{$_.ProcessName -like '*{safe}*' -or "
        f"$_.MainWindowTitle -like '*{safe}*'}} | Stop-Process -Force"
    )


def list_apps() -> str:
    Desktop = _pywinauto()
    if Desktop is None:
        return run_ps(
            "Get-Process | Where-Object {$_.MainWindowHandle -ne 0} | "
            "Select-Object -ExpandProperty MainWindowTitle | Where-Object {$_}"
        )
    try:
        titles = []
        for win in Desktop(backend="uia").windows():
            t = (win.window_text() or "").strip()
            if t:
                titles.append(t)
        return ", ".join(titles[:80]) or "(no visible windows)"
    except Exception as e:
        return f"ERROR: {e}"


def frontmost_app() -> str:
    Desktop = _pywinauto()
    if Desktop is None:
        return run_ps(
            "Add-Type @'\n"
            "using System; using System.Runtime.InteropServices; using System.Text;\n"
            "public class FG {\n"
            "  [DllImport(\"user32.dll\")] public static extern IntPtr GetForegroundWindow();\n"
            "  [DllImport(\"user32.dll\")] public static extern int GetWindowText(IntPtr h, StringBuilder s, int n);\n"
            "}\n"
            "'@;\n"
            "$h=[FG]::GetForegroundWindow(); $sb=New-Object System.Text.StringBuilder 512;\n"
            "[FG]::GetWindowText($h,$sb,512)|Out-Null; $sb.ToString()"
        )
    try:
        win = Desktop(backend="uia").get_active()
        return win.window_text() or "(unknown)"
    except Exception as e:
        return f"ERROR: {e}"
