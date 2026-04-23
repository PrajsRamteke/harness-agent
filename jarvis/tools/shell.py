"""Shell execution tool with approval prompt."""
import subprocess

from ..console import console
from ..constants import CWD, MAX_TOOL_OUTPUT
from .. import state


def run_bash(cmd: str, timeout: int = 60) -> str:
    DANGEROUS = ["rm -rf /", "mkfs", ":(){:|:&};:", "dd if=/dev/zero"]
    if any(d in cmd for d in DANGEROUS): return "BLOCKED: dangerous command"
    if not state.auto_approve:
        console.print(f"[yellow]→ run:[/] [cyan]{cmd}[/]")
        ok = console.input("[dim]approve? [Y/n/a=always] [/]").strip().lower()
        if ok == "a":
            state.auto_approve = True
        elif ok == "n":
            return "USER DENIED"
    try:
        r = subprocess.run(cmd, shell=True, capture_output=True, text=True,
                           timeout=timeout, cwd=str(CWD))
        out = (r.stdout or "") + (f"\n[stderr]\n{r.stderr}" if r.stderr else "")
        return f"$ {cmd}\nexit={r.returncode}\n{out[-MAX_TOOL_OUTPUT:]}"
    except subprocess.TimeoutExpired:
        return f"TIMEOUT after {timeout}s"
