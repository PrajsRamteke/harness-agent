"""AppleScript / osascript helper + generic applescript tool."""
import subprocess
from ...constants import MAX_TOOL_OUTPUT


def _osa(script: str, timeout: int = 30, lang: str = "AppleScript") -> str:
    try:
        r = subprocess.run(
            ["osascript", "-l", lang, "-e", script],
            capture_output=True, text=True, timeout=timeout,
        )
        out = r.stdout.strip()
        if r.returncode != 0:
            return f"ERROR (exit {r.returncode}): {r.stderr.strip()}\n{out}"
        return out or "OK"
    except subprocess.TimeoutExpired:
        return f"TIMEOUT after {timeout}s"


def applescript(code: str, timeout: int = 60) -> str:
    """Run arbitrary AppleScript. Return the script result (or error)."""
    return _osa(code, timeout)[:MAX_TOOL_OUTPUT]
