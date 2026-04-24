"""Shell execution tool with approval prompt."""
import subprocess

from ..console import console
from ..constants import CWD, MAX_TOOL_OUTPUT
from .. import state


def _is_tui_console() -> bool:
    # TUIConsole lives in jarvis.tui.console_shim; fall back to duck-typing.
    return type(console).__name__ == "TUIConsole"


def run_bash(cmd: str, timeout: int = 60) -> str:
    DANGEROUS = ["rm -rf /", "mkfs", ":(){:|:&};:", "dd if=/dev/zero"]
    if any(d in cmd for d in DANGEROUS): return "BLOCKED: dangerous command"

    # Hard redirect: scanning the home directory or root with `find` is 100-1000x
    # slower than Spotlight (mdfind). Reject and tell the model to use fast_find.
    stripped = cmd.strip()
    if stripped.startswith("find "):
        lowered = stripped.lower()
        slow_targets = ("find ~", "find $home", "find /users/", "find /",
                        "find . ", "find ./")
        if any(t in lowered for t in slow_targets):
            return (
                "BLOCKED: slow filesystem scan. Use the `fast_find` tool instead "
                "(Spotlight/mdfind — milliseconds vs. 30s+). Example: "
                "fast_find(query='qr', ext='png', kind='file'). "
                "For scoped repo searches, prefer `search_code` or `glob_files`."
            )

    if not state.auto_approve:
        if _is_tui_console():
            # The TUI has no blocking prompt mechanism from inside a tool call.
            # Show the command in the log and proceed; dangerous commands are
            # still blocked above. Users can disable this by implementing a
            # modal approval flow later.
            console.print(f"[yellow]→ run:[/] [cyan]{cmd}[/]")
        else:
            console.print(f"[yellow]→ run:[/] [cyan]{cmd}[/]")
            try:
                ok = console.input("[dim]approve? [Y/n/a=always] [/]").strip().lower()
            except (RuntimeError, EOFError):
                ok = ""
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
