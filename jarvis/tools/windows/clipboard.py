"""Clipboard get/set on Windows."""
from ...constants import MAX_TOOL_OUTPUT


def _clip():
    try:
        import pyperclip  # type: ignore
        return pyperclip
    except ImportError:
        return None


def clipboard_get() -> str:
    clip = _clip()
    if clip is None:
        from ._ps import run_ps
        return run_ps("Get-Clipboard -Raw", timeout=10)[:MAX_TOOL_OUTPUT]
    try:
        return (clip.paste() or "")[:MAX_TOOL_OUTPUT]
    except Exception as e:
        return f"ERROR: {e}"


def clipboard_set(text: str) -> str:
    clip = _clip()
    if clip is None:
        from ._ps import run_ps
        escaped = text.replace("'", "''")
        run_ps(f"Set-Clipboard -Value '{escaped}'", timeout=10)
        return f"clipboard set ({len(text)} chars)"
    try:
        clip.copy(text)
        return f"clipboard set ({len(text)} chars)"
    except Exception as e:
        return f"ERROR: {e}"
