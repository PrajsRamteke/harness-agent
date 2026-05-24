"""PowerShell automation (Windows equivalent of AppleScript)."""
from ._ps import run_ps


def run_powershell(code: str, timeout: int = 60) -> str:
    """Run arbitrary PowerShell. Use for Explorer, Edge, Outlook, Notepad, etc."""
    return run_ps(code, timeout=timeout)
