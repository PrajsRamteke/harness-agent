"""Best-effort desktop notifications (macOS · Linux), fire-and-forget."""
from __future__ import annotations

import shutil
import subprocess
import sys
import threading


def _escape(s: str) -> str:
    return (s or "").replace("\\", "\\\\").replace('"', '\\"')[:200]


def desktop_notify(title: str, message: str) -> bool:
    """Show a system notification without blocking; False when unsupported."""
    if sys.platform == "darwin" and shutil.which("osascript"):
        cmd = ["osascript", "-e",
               f'display notification "{_escape(message)}" with title "{_escape(title)}"']
    elif sys.platform.startswith("linux") and shutil.which("notify-send"):
        cmd = ["notify-send", "--app-name=Jarvis", title[:80], message[:200]]
    else:
        return False

    def _run() -> None:
        try:
            subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=5)
        except Exception:
            pass

    threading.Thread(target=_run, name="desktop-notify", daemon=True).start()
    return True
