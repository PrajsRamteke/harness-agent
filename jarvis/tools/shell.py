"""Shell execution tool with approval prompt."""
import os
import re
import subprocess
import threading

from ..console import console
from ..constants import CWD, MAX_TOOL_OUTPUT, DEFAULT_BASH_TIMEOUT
from .. import state

_bash_lock = threading.Lock()

# Read-only agent tools (search_code, git_status, …) must not block on approval.
_SAFE_READONLY = re.compile(
    r"^(?:"
    r"rg\b|grep\b|"
    r"git(?:\s+--no-pager)?\s+(?:status|log|diff|show|rev-parse|branch|remote)\b|"
    r"which\b|file\b|wc\b|head\b|tail\b|cat\b|pwd\b|echo\b|test\b|\["
    r")",
    re.IGNORECASE,
)


def _is_safe_readonly_command(cmd: str) -> bool:
    return bool(_SAFE_READONLY.match((cmd or "").strip()))


_DANGEROUS = ["rm -rf /", "mkfs", ":(){:|:&};:", "dd if=/dev/zero"]


def is_dangerous(cmd: str) -> bool:
    return any(d in cmd for d in _DANGEROUS)


def ask_approval(cmd: str) -> str | None:
    """Ask the user before running ``cmd`` (unless auto-approved / read-only).

    Returns ``"USER DENIED"`` when refused, else None. Call with
    ``_bash_lock`` held so approval prompts never overlap.
    """
    if state.auto_approve or _is_safe_readonly_command(cmd):
        return None
    console.print(f"[yellow]→ run:[/] [cyan]{cmd}[/]")
    try:
        approve = getattr(console, "prompt_shell_approval", None)
        if approve is not None:
            ok = approve(cmd).strip().lower()
        else:
            ok = console.input(
                "[dim]approve? [Y/n/a=always] [/]"
            ).strip().lower()
    except (RuntimeError, EOFError):
        ok = ""
    if ok == "a":
        state.auto_approve = True
    elif ok == "n" or ok == "":
        return "USER DENIED"
    if state.turn_cancelled():
        raise KeyboardInterrupt()
    return None


def run_bash(cmd: str, timeout: int = DEFAULT_BASH_TIMEOUT) -> str:
    if is_dangerous(cmd):
        return "BLOCKED: dangerous command"

    with _bash_lock:
        denied = ask_approval(cmd)
        if denied:
            return denied
        try:
            env = os.environ.copy()
            env.setdefault("GIT_PAGER", "cat")
            env.setdefault("PAGER", "cat")
            r = subprocess.run(
                cmd,
                shell=True,
                capture_output=True,
                text=True,
                timeout=timeout,
                cwd=str(CWD),
                env=env,
            )
            out = (r.stdout or "") + (f"\n[stderr]\n{r.stderr}" if r.stderr else "")
            return f"$ {cmd}\nexit={r.returncode}\n{out[-MAX_TOOL_OUTPUT:]}"
        except subprocess.TimeoutExpired:
            return f"TIMEOUT after {timeout}s"
