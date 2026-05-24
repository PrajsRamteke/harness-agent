"""Run PowerShell commands from tool handlers."""
import subprocess

from ...constants import MAX_TOOL_OUTPUT


def run_ps(command: str, timeout: int = 60) -> str:
    """Execute a PowerShell command and return stdout/stderr."""
    try:
        r = subprocess.run(
            ["powershell", "-NoProfile", "-NonInteractive", "-Command", command],
            capture_output=True,
            text=True,
            timeout=max(5, min(int(timeout), 600)),
        )
    except subprocess.TimeoutExpired:
        return "ERROR: PowerShell timed out"
    except OSError as e:
        return f"ERROR: {e}"
    out = (r.stdout or "").strip()
    err = (r.stderr or "").strip()
    if r.returncode != 0:
        msg = err or out or f"exit code {r.returncode}"
        return f"ERROR: {msg[:MAX_TOOL_OUTPUT]}"
    combined = out
    if err and err not in out:
        combined = f"{out}\n[stderr]\n{err}" if out else err
    return combined[:MAX_TOOL_OUTPUT] or "OK"
