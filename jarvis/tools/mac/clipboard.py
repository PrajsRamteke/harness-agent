"""Clipboard get/set via pbpaste/pbcopy."""
import subprocess
from ...constants import MAX_TOOL_OUTPUT


def clipboard_get() -> str:
    r = subprocess.run(["pbpaste"], capture_output=True, text=True, timeout=5)
    return r.stdout[:MAX_TOOL_OUTPUT]


def clipboard_set(text: str) -> str:
    p = subprocess.Popen(["pbcopy"], stdin=subprocess.PIPE)
    p.communicate(text.encode("utf-8"))
    return f"clipboard set ({len(text)} chars)"
