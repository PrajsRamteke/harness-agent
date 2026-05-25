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


def run_bash(cmd: str, timeout: int = DEFAULT_BASH_TIMEOUT) -> str:
    DANGEROUS = ["rm -rf /", "mkfs", ":(){:|:&};:", "dd if=/dev/zero"]
    if any(d in cmd for d in DANGEROUS):
        return "BLOCKED: dangerous command"

    with _bash_lock:
        if not state.auto_approve and not _is_safe_readonly_command(cmd):
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
            if state.cancel_requested.is_set():
                raise KeyboardInterrupt()
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
