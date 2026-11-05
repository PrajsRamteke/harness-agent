"""Shell execution tool with approval prompt."""
import os
import re
import signal
import subprocess
import threading
import time

from rich.markup import escape

from ..console import console
from ..constants import CWD, BASH_MAX_OUTPUT, DEFAULT_BASH_TIMEOUT, MAX_BASH_TIMEOUT
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


def _run_process(cmd: str, timeout: int, env: dict, cwd: str | None = None) -> tuple[int, str, str]:
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
        cwd=str(cwd or CWD),
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

# ── read-only commands ──────────────────────────────────────────────────────
# A command made only of these (joined by | && || ;) changes nothing on disk:
# it needs no approval and runs in parallel with other read-only calls instead
# of queueing behind _bash_lock. Read-only agent tools (search_code,
# git_status, …) go through here too. The check is per segment, so
# `cat x; rm -rf y` is *not* read-only (the old prefix regex let it through).
_READONLY_CMDS = frozenset({
    "rg", "grep", "egrep", "fgrep", "cat", "head", "tail", "wc", "file", "which",
    "pwd", "echo", "printf", "test", "[", "ls", "tree", "stat", "du", "df",
    "basename", "dirname", "realpath", "readlink", "cut", "tr", "nl", "diff",
    "cmp", "comm", "true", "cd", "type", "column", "jq", "sort", "uniq", "find",
    "sed", "git", "shasum", "md5",
})
_GIT_READONLY = frozenset({
    "status", "log", "diff", "show", "rev-parse", "branch", "remote", "ls-files",
    "blame", "grep", "describe", "shortlog",
})
_FIND_WRITES = frozenset({
    "-delete", "-exec", "-execdir", "-ok", "-okdir", "-fprint", "-fprint0",
    "-fprintf", "-fls",
})
_SED_PRINT = re.compile(r"^\s*(?:(?:\d+|\$)(?:\s*,\s*(?:\d+|\$))?\s*p\s*;?\s*)+$")
_SEPARATORS = frozenset({"|", "||", "&&", ";"})


def _segment_is_readonly(words: list[str]) -> bool:
    if not words:
        return False
    name, args = words[0], words[1:]
    if name not in _READONLY_CMDS:
        return False
    if name == "find":
        return not any(a in _FIND_WRITES for a in args)
    if name == "sed":
        # Only `sed -n '10,40p' file` style printing — no -i, no w/e commands.
        if "-n" not in args or any(a.startswith(("-i", "--in-place", "-I")) for a in args):
            return False
        scripts = [a for a in args if not a.startswith("-")]
        return bool(scripts) and bool(_SED_PRINT.match(scripts[0]))
    if name in ("sort", "tree"):
        return not any(a == "-o" or a.startswith("--output") for a in args)
    if name == "uniq":
        return len([a for a in args if not a.startswith("-")]) <= 1
    if name == "git":
        rest = list(args)
        while rest and rest[0] in ("--no-pager", "-C"):
            rest = rest[2:] if rest[0] == "-C" else rest[1:]
        # (`-c key=val` is refused: config overrides can run programs — core.pager …)
        if not rest or rest[0] not in _GIT_READONLY:
            return False
        sub, sub_args = rest[0], rest[1:]
        if any(a.startswith("--output") or a == "--ext-diff" for a in sub_args):
            return False
        if sub == "branch":
            # listing only — `git branch name` creates, -d/-D/-m delete/rename
            return all(a in ("-a", "-r", "-v", "-vv", "-l", "--list", "--all",
                             "--remotes", "--show-current", "--no-color", "--color")
                       for a in sub_args)
        if sub == "remote":
            # `git remote add/remove/set-url …` change config
            return not sub_args or sub_args[0] in ("-v", "--verbose", "show", "get-url")
        return True
    return True


def _is_safe_readonly_command(cmd: str) -> bool:
    """True when every part of ``cmd`` only reads (see _READONLY_CMDS)."""
    import shlex

    cmd = (cmd or "").strip()
    # Command substitution, backgrounding, multi-line scripts: never "read-only".
    if not cmd or "\n" in cmd or "`" in cmd or "$(" in cmd or "<(" in cmd:
        return False
    try:
        lex = shlex.shlex(cmd, posix=True, punctuation_chars=True)
        lex.whitespace_split = True
        tokens = list(lex)
    except ValueError:
        return False
    words: list[str] = []
    i = 0
    while i < len(tokens):
        tok = tokens[i]
        if tok in _SEPARATORS:
            if not _segment_is_readonly(words):
                return False
            words = []
        elif tok in (">", ">>", "&>"):
            # Only throwing output away: `2>/dev/null`, `>/dev/null`.
            if i + 1 >= len(tokens) or tokens[i + 1] != "/dev/null":
                return False
            if words and words[-1].isdigit():
                words.pop()
            i += 1
        elif tok == ">&":
            # `2>&1`
            if i + 1 >= len(tokens) or not tokens[i + 1].isdigit():
                return False
            if words and words[-1].isdigit():
                words.pop()
            i += 1
        elif tok and set(tok) <= set("();<>|&"):
            return False  # subshells, `&`, `<`, anything unexpected
        else:
            words.append(tok)
        i += 1
    return _segment_is_readonly(words)


_DANGEROUS = ["mkfs", ":(){:|:&};:", "dd if=/dev/zero"]
# `rm -rf /` itself (or `/*`) — not `rm -rf /tmp/x`, which is everyday cleanup.
_RM_ROOT = re.compile(r"\brm\s+-[a-zA-Z]*\s+/\*?(?=$|[\s;&|)])")


def is_dangerous(cmd: str) -> bool:
    return any(d in cmd for d in _DANGEROUS) or bool(_RM_ROOT.search(cmd))


# ── commands that would stop Jarvis itself ─────────────────────────────────
# `lsof -ti :8765 | xargs kill` with 8765 = the web remote's port killed the
# Jarvis running the command (the model had picked that port for a test server).

_KILL_WORD = re.compile(r"(?<![\w-])(?:kill|pkill|killall|fuser)(?![\w-])")
_PROC_NAMES = {"python", "python3", "Python", "jarvis", "agent.py"}


def _own_listen_ports() -> set[int]:
    """TCP ports this process listens on (web remote, sign-in callbacks …)."""
    try:
        out = subprocess.run(
            ["lsof", "-nP", "-a", "-p", str(os.getpid()), "-iTCP", "-sTCP:LISTEN", "-Fn"],
            capture_output=True, text=True, timeout=3,
        ).stdout
    except (OSError, subprocess.SubprocessError):
        return set()
    return {int(m) for m in re.findall(r"^n.*:(\d+)$", out, re.MULTILINE)}


def _mentions(cmd: str, number: int) -> bool:
    return re.search(rf"(?<!\d){number}(?!\d)", cmd) is not None


def _kills_by_name(cmd: str) -> str | None:
    """A ``pkill`` / ``killall`` pattern that matches Jarvis's own process."""
    import shlex

    for seg in re.split(r"[;&|\n]+", cmd):
        try:
            words = shlex.split(seg)
        except ValueError:
            words = seg.split()
        for i, word in enumerate(words):
            if os.path.basename(word) not in ("pkill", "killall"):
                continue
            for arg in words[i + 1:]:
                if arg.startswith("-"):
                    continue
                if arg in _PROC_NAMES or "jarvis" in arg.lower() or "agent.py" in arg:
                    return arg
    return None


def self_kill_reason(cmd: str) -> str | None:
    """Why ``cmd`` would stop the Jarvis running it, or None."""
    if not _KILL_WORD.search(cmd or ""):
        return None
    for pid in {os.getpid(), os.getppid()}:
        if pid > 1 and _mentions(cmd, pid):
            return f"process {pid} is Jarvis itself"
    for port in sorted(_own_listen_ports()):
        if _mentions(cmd, port):
            return (f"port {port} is Jarvis's own web remote / listener, so the "
                    "process found on it is Jarvis itself (and any browser "
                    "connected to it)")
    name = _kills_by_name(cmd)
    if name:
        return f"`{name}` matches Jarvis's own process"
    return None


def self_kill_block(reason: str) -> str:
    return (f"BLOCKED: this command would stop Jarvis itself — {reason}. Nothing "
            "was run. Kill your own process by its PID (from `$!` or `ps`) instead, "
            "and run test servers on a port Jarvis isn't using.")


def own_port_note(cmd: str) -> str:
    """A note when ``cmd`` names a port Jarvis listens on — a server started
    there can't bind, and requests to it reach Jarvis, not the new server."""
    if not re.search(r"(?<!\d)\d{4,5}(?!\d)", cmd or ""):
        return ""
    ports = [p for p in sorted(_own_listen_ports()) if _mentions(cmd, p)]
    if not ports:
        return ""
    names = ", ".join(map(str, ports))
    return (f"\n[note] port {names} is in use by Jarvis itself (its web remote) — "
            "a server started there can't bind, and requests to it reach Jarvis. "
            "Use another port, and never kill whatever listens on it.")


def ask_approval(cmd: str, project: str = "", cwd: str | None = None) -> str | None:
    """Ask the user before running ``cmd`` (unless auto-approved / read-only).

    ``project`` / ``cwd`` name a linked project the command runs in, so the prompt says so.
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
    where = f" in [bold]{escape(project)}[/] (linked project)" if project else ""
    console.print(f"[yellow]→ {who}run{where}:[/] [cyan]{escape(cmd)}[/]")
    shown = f"# in project {project}: {cwd}\n{cmd}" if project else cmd
    try:
        approve = getattr(console, "prompt_shell_approval", None)
        if approve is not None:
            ok = approve(shown).strip().lower()
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


def ask_linked_write(project: str, path: str, tool: str) -> str:
    """Ask before editing a file in a linked project whose write policy is ``ask``.

    Reuses the shell-approval prompt (TUI modal / web card / terminal). Returns
    ``"y"`` (this once), ``"a"`` (always for this project this session) or ``"n"``.
    ``state.auto_approve`` counts as yes — the user already waved everything through.
    """
    if state.auto_approve:
        return "y"
    from ..subagents.context import current as _subagent

    question = f"Edit a file in {project} (linked project)?"
    with _bash_lock:
        sub = _subagent()
        who = ""
        if sub is not None:
            who = f"[bold]{escape(sub[1].name)}[/] (agent) "
            sub[0].set_activity(sub[1], "Waiting for your approval")
        console.print(f"[yellow]→ {who}{escape(question)}[/] [cyan]{escape(tool)} {escape(path)}[/]")
        try:
            approve = getattr(console, "prompt_shell_approval", None)
            if approve is not None:
                ok = approve(f"# {question}\n{tool} → {path}").strip().lower()
            else:
                ok = console.input("[dim]approve? [Y/n/a=always for this project] [/]").strip().lower()
        except (RuntimeError, EOFError):
            ok = ""
        if state.turn_cancelled():
            raise KeyboardInterrupt()
    return ok if ok in ("y", "a") else "n"


_CMD_ECHO_MAX = 1500


def _format_result(cmd: str, code: int, out: str) -> str:
    """``$ cmd`` / ``exit=N`` / output, kept within BASH_MAX_OUTPUT. Long output
    keeps its start and its end (first errors *and* the final summary) with a
    note in between, instead of silently keeping only the tail."""
    if len(cmd) > _CMD_ECHO_MAX:
        cmd = cmd[:1000] + f" … [command truncated, {len(cmd):,} chars]"
    header = f"$ {cmd}\nexit={code}\n"
    budget = max(2000, BASH_MAX_OUTPUT - len(header))
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


def run_bash(cmd: str, timeout: int = DEFAULT_BASH_TIMEOUT, project: str | None = None) -> str:
    """Run ``cmd`` in the project root, or — with ``project`` — in a linked project's root."""
    try:
        timeout = max(1, min(int(timeout or DEFAULT_BASH_TIMEOUT), MAX_BASH_TIMEOUT))
    except (TypeError, ValueError):
        timeout = DEFAULT_BASH_TIMEOUT
    if is_dangerous(cmd):
        return "BLOCKED: dangerous command"
    reason = self_kill_reason(cmd)
    if reason:
        return self_kill_block(reason)

    from .linked import shell_root

    root, err = shell_root(project, readonly=_is_safe_readonly_command(cmd))
    if err:
        return err
    cwd = root["path"] if root else None
    pid = root["id"] if root else ""

    env = os.environ.copy()
    env.setdefault("GIT_PAGER", "cat")
    env.setdefault("PAGER", "cat")
    if _is_safe_readonly_command(cmd):
        # Changes nothing and asks nothing: no lock, so several read-only
        # calls from one step run side by side (render.py batches them).
        return _exec(cmd, timeout, env, watch=False, cwd=cwd)
    with _bash_lock:
        denied = ask_approval(cmd, pid, cwd)
        if denied:
            return denied
        return _exec(cmd, timeout, env, watch=True, cwd=cwd)


def _exec(cmd: str, timeout: int, env: dict, *, watch: bool, cwd: str | None = None) -> str:
    try:
        # Files the command deletes / renames / edits in place show up in
        # the web Changes panel (nothing is recorded if nothing changed).
        if not watch:
            settle_changes = lambda: None  # noqa: E731
        else:
            settle_changes = file_changes.watch_shell(cmd, cwd) if cwd else file_changes.watch_shell(cmd)
        try:
            code, stdout, stderr = (
                _run_process(cmd, timeout, env, cwd) if cwd else _run_process(cmd, timeout, env)
            )
        finally:
            settle_changes()
        out = (stdout or "") + (f"\n[stderr]\n{stderr}" if stderr else "")
        return _format_result(cmd, code, out) + own_port_note(cmd)
    except subprocess.TimeoutExpired:
        return (f"TIMEOUT after {timeout}s — pass a larger timeout (max "
                f"{MAX_BASH_TIMEOUT}s), or use run_bg for long builds / test runs")
    except _Cancelled:
        return CANCELLED_RESULT
