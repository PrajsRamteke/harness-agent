"""Shell execution tool with approval prompt."""
import os
import re
import signal
import subprocess
import threading
import time

from rich.markup import escape

from ..console import console
from ..constants import CWD, MAX_TOOL_OUTPUT, DEFAULT_BASH_TIMEOUT
from .. import file_changes, state

_bash_lock = threading.Lock()

# How often a running command checks whether its turn was stopped (Esc / web
# Stop / New chat). Short enough to feel instant, long enough to cost nothing.
_CANCEL_POLL = 0.2

CANCELLED_RESULT = "CANCELLED: the command was stopped because the turn was interrupted"


class _Cancelled(Exception):
    """The turn running this command was cancelled; the command is stopped."""


def _descendants(root: int) -> list[int]:
    """Every process started (directly or not) by ``root``, via ``ps``."""
    try:
        out = subprocess.run(
            ["ps", "-A", "-o", "pid=,ppid="], capture_output=True, text=True, timeout=3,
        ).stdout
    except (OSError, subprocess.SubprocessError):
        return []
    children: dict[int, list[int]] = {}
    for line in out.splitlines():
        parts = line.split()
        if len(parts) == 2 and parts[0].isdigit() and parts[1].isdigit():
            children.setdefault(int(parts[1]), []).append(int(parts[0]))
    found: list[int] = []
    stack = [root]
    while stack:
        for child in children.get(stack.pop(), []):
            if child not in found:
                found.append(child)
                stack.append(child)
    return found


def _kill_tree(proc: subprocess.Popen) -> None:
    """Stop ``proc`` and everything it started: TERM, a moment, then KILL.

    The shell (``sh -c …``) is rarely the process doing the work; killing only
    it would leave e.g. ``pytest`` running with the output pipes open.
    Children are collected first — once the shell dies they're reparented
    and can't be found from it any more.
    """
    pids = _descendants(proc.pid) if os.name == "posix" else []
    for sig in (signal.SIGTERM, getattr(signal, "SIGKILL", signal.SIGTERM)):
        for pid in pids:
            try:
                os.kill(pid, sig)
            except OSError:
                pass
        try:
            proc.send_signal(sig)
        except OSError:
            pass
        try:
            proc.wait(timeout=1.0)
            if sig == signal.SIGTERM and not any(_pid_alive(p) for p in pids):
                return
        except subprocess.TimeoutExpired:
            pass


def _pid_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    return True


def _drain(proc: subprocess.Popen) -> tuple[str, str]:
    try:
        out, err = proc.communicate(timeout=2)
    except (subprocess.TimeoutExpired, ValueError, OSError):
        return "", ""
    return out or "", err or ""


def _run_process(cmd: str, timeout: int, env: dict) -> tuple[int, str, str]:
    """Run ``cmd`` like ``subprocess.run(shell=True, capture_output=True)``,
    but stop it as soon as the turn is cancelled.

    Raises ``subprocess.TimeoutExpired`` on timeout and ``_Cancelled`` on a
    cancelled turn — in both cases after the whole process tree is gone, so
    the shell lock is never held by a command nobody is waiting for.
    """
    proc = subprocess.Popen(
        cmd,
        shell=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        cwd=str(CWD),
        env=env,
    )
    deadline = time.monotonic() + timeout
    try:
        while True:
            try:
                # Repeated communicate() calls never lose output.
                out, err = proc.communicate(timeout=_CANCEL_POLL)
                return proc.returncode, out or "", err or ""
            except subprocess.TimeoutExpired:
                pass
            if state.turn_cancelled():
                _kill_tree(proc)
                _drain(proc)
                raise _Cancelled()
            if time.monotonic() >= deadline:
                _kill_tree(proc)
                _drain(proc)
                raise subprocess.TimeoutExpired(cmd, timeout)
    except BaseException:
        # KeyboardInterrupt injected by Esc, or anything else: never leave the
        # command running behind us.
        if proc.poll() is None:
            _kill_tree(proc)
            _drain(proc)
        raise

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
    # The command is model-written text: a `[x for x in y]` in it must print as
    # text, not be read as a Rich style tag (the TUI crashes on an unknown one).
    from ..subagents.context import current as _subagent

    sub = _subagent()
    who = ""
    if sub is not None:
        who = f"[bold]{escape(sub[1].name)}[/] (agent) "
        sub[0].set_activity(sub[1], "Waiting for your approval")
    console.print(f"[yellow]→ {who}run:[/] [cyan]{escape(cmd)}[/]")
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


_CMD_ECHO_MAX = 1500


def _format_result(cmd: str, code: int, out: str) -> str:
    """``$ cmd`` / ``exit=N`` / output, kept within MAX_TOOL_OUTPUT. Long output
    keeps its start and its end (first errors *and* the final summary) with a
    note in between, instead of silently keeping only the tail."""
    if len(cmd) > _CMD_ECHO_MAX:
        cmd = cmd[:1000] + f" … [command truncated, {len(cmd):,} chars]"
    header = f"$ {cmd}\nexit={code}\n"
    budget = max(2000, MAX_TOOL_OUTPUT - len(header))
    if len(out) > budget:
        note_room = 120
        head_n = (budget - note_room) // 3
        tail_n = budget - note_room - head_n
        omitted = len(out) - head_n - tail_n
        out = (
            f"{out[:head_n]}\n… [{omitted:,} chars of output omitted — rerun "
            f"piped through grep / head / tail to see them] …\n{out[-tail_n:]}"
        )
    return header + out


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
            # Files the command deletes / renames / edits in place show up in
            # the web Changes panel (nothing is recorded if nothing changed).
            settle_changes = file_changes.watch_shell(cmd)
            try:
                code, stdout, stderr = _run_process(cmd, timeout, env)
            finally:
                settle_changes()
            out = (stdout or "") + (f"\n[stderr]\n{stderr}" if stderr else "")
            return _format_result(cmd, code, out)
        except subprocess.TimeoutExpired:
            return f"TIMEOUT after {timeout}s"
        except _Cancelled:
            return CANCELLED_RESULT
