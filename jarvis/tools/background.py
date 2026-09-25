"""Background shell jobs — ``run_bg`` / ``bg_output`` / ``bg_kill``.

Slow commands (test suites, builds, installs, dev servers, watchers) run
detached so the agent keeps working while they go; output streams to a log
file and is read on demand.

* ``run_bg(cmd)`` — same safety check and approval as ``run_bash``, then
  returns a job id at once (or the output, if it finished within a second).
* ``bg_output(job_id, wait, tail, new_only)`` — status + latest output;
  ``wait`` blocks until the job finishes; no ``job_id`` lists every job.
* ``bg_kill(job_id)`` — SIGTERM the job's whole process group, SIGKILL
  after a grace period.

Finished jobs whose output hasn't been read are listed in the system prompt
(``prompt_block``) so the model notices on its next request; the UI prints a
notice when a job finishes (``add_finish_hook``). Every job still running is
killed when Jarvis exits.
"""
from __future__ import annotations

import atexit
import os
import pathlib
import re
import signal
import subprocess
import tempfile
import threading
import time
from dataclasses import dataclass, field
from typing import Callable

from .. import state
from ..constants import CWD, MAX_TOOL_OUTPUT

MAX_RUNNING = 8
MAX_WAIT = 600.0
DEFAULT_TAIL = 60
KILL_GRACE = 3.0
LOG_DIR = pathlib.Path(tempfile.gettempdir()) / "jarvis-bg" / str(os.getpid())

_ANSI_RE = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]|\x1b\][^\x07\x1b]*(?:\x07|\x1b\\)|\x1b[()][A-Za-z0-9]")


@dataclass
class Job:
    id: int
    cmd: str
    proc: subprocess.Popen
    log: pathlib.Path
    t0: float = field(default_factory=time.monotonic)
    t1: float | None = None
    code: int | None = None
    killed: bool = False
    read_done: bool = False  # the finished output has been returned to the model
    offset: int = 0          # bytes already returned (new_only)

    @property
    def running(self) -> bool:
        return self.code is None

    @property
    def elapsed(self) -> float:
        return (self.t1 or time.monotonic()) - self.t0

    @property
    def status(self) -> str:
        if self.running:
            return "running"
        if self.killed:
            return "killed"
        return f"exit {self.code}"


_jobs: dict[int, Job] = {}
_next_id = 1
_lock = threading.Lock()
_finish_hooks: list[Callable[[Job], None]] = []


def add_finish_hook(fn: Callable[[Job], None]) -> None:
    """``fn(job)`` runs on the watcher thread when a job exits."""
    if fn not in _finish_hooks:
        _finish_hooks.append(fn)


def remove_finish_hook(fn: Callable[[Job], None]) -> None:
    if fn in _finish_hooks:
        _finish_hooks.remove(fn)


def jobs() -> list[Job]:
    with _lock:
        return list(_jobs.values())


def fmt_secs(s: float) -> str:
    if s < 60:
        return f"{s:.0f}s" if s >= 10 else f"{s:.1f}s"
    m, sec = divmod(int(s), 60)
    if m < 60:
        return f"{m}m {sec:02d}s"
    h, m = divmod(m, 60)
    return f"{h}h {m:02d}m"


def _short(cmd: str, width: int = 80) -> str:
    one = " ".join(cmd.split())
    return one if len(one) <= width else one[: width - 1] + "…"


# ── lifecycle ─────────────────────────────────────────────────────────────


def _watch(job: Job) -> None:
    code = job.proc.wait()
    with _lock:
        job.code = code
        job.t1 = time.monotonic()
    for hook in list(_finish_hooks):
        try:
            hook(job)
        except Exception:
            pass


def _signal_group(job: Job, sig: int) -> None:
    try:
        if os.name == "posix":
            os.killpg(job.proc.pid, sig)
        elif sig == signal.SIGTERM:
            job.proc.terminate()
        else:
            job.proc.kill()
    except (OSError, ProcessLookupError):
        pass


def _stop(job: Job, grace: float = KILL_GRACE) -> None:
    if not job.running:
        return
    job.killed = True
    _signal_group(job, signal.SIGTERM)
    deadline = time.monotonic() + grace
    while job.proc.poll() is None and time.monotonic() < deadline:
        time.sleep(0.05)
    if job.proc.poll() is None:
        _signal_group(job, getattr(signal, "SIGKILL", signal.SIGTERM))


@atexit.register
def _kill_all() -> None:
    for job in jobs():
        if job.running:
            _stop(job, grace=0.5)


def reset() -> None:
    """Stop and forget every job (tests, /new)."""
    global _next_id
    for job in jobs():
        _stop(job, grace=0.5)
    with _lock:
        _jobs.clear()
        _next_id = 1


# ── output ────────────────────────────────────────────────────────────────


def clean_output(raw: str) -> str:
    """Strip ANSI codes; keep only the last frame of ``\\r`` progress bars."""
    text = _ANSI_RE.sub("", raw.replace("\r\n", "\n"))
    return "\n".join(line.rsplit("\r", 1)[-1] for line in text.split("\n"))


def _read(job: Job, *, tail: int, new_only: bool) -> tuple[str, int]:
    """``(last ``tail`` lines, lines omitted)``; advances ``job.offset``."""
    try:
        size = job.log.stat().st_size
        start = job.offset if new_only else max(0, size - 512 * 1024)
        start = min(start, size)
        with open(job.log, "rb") as fh:
            fh.seek(start)
            data = fh.read(size - start)
    except OSError:
        return "", 0
    job.offset = size
    text = clean_output(data.decode("utf-8", errors="replace")).rstrip("\n")
    if not text.strip():
        return "", 0
    lines = text.split("\n")
    omitted = max(0, len(lines) - tail)
    body = "\n".join(lines[-tail:])
    if len(body) > MAX_TOOL_OUTPUT - 400:
        body = "…" + body[-(MAX_TOOL_OUTPUT - 400):]
    return body, omitted


def _headline(job: Job) -> str:
    if job.running:
        state_txt = f"running for {fmt_secs(job.elapsed)}"
    elif job.killed:
        state_txt = f"killed after {fmt_secs(job.elapsed)}"
    else:
        state_txt = f"finished · exit {job.code} after {fmt_secs(job.elapsed)}"
    return f"background job #{job.id} {state_txt}: {job.cmd}"


# ── tools ─────────────────────────────────────────────────────────────────


def run_bg(cmd: str) -> str:
    """Start ``cmd`` in the background; return its job id immediately."""
    global _next_id
    from .shell import _bash_lock, ask_approval, is_dangerous

    cmd = (cmd or "").strip()
    if not cmd:
        return "ERROR: cmd is required"
    if is_dangerous(cmd):
        return "BLOCKED: dangerous command"
    running = [j for j in jobs() if j.running]
    if len(running) >= MAX_RUNNING:
        return (f"ERROR: {len(running)} background jobs are already running — "
                "bg_kill one first (bg_output with no job_id lists them).")
    with _bash_lock:
        denied = ask_approval(cmd)
        if denied:
            return denied

    LOG_DIR.mkdir(parents=True, exist_ok=True)
    with _lock:
        job_id = _next_id
        _next_id += 1
    log = LOG_DIR / f"job-{job_id}.log"
    env = os.environ.copy()
    env.setdefault("GIT_PAGER", "cat")
    env.setdefault("PAGER", "cat")
    env.setdefault("PYTHONUNBUFFERED", "1")
    try:
        with open(log, "wb") as fh:
            proc = subprocess.Popen(
                cmd, shell=True, cwd=str(CWD), env=env, stdout=fh,
                stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL,
                start_new_session=(os.name == "posix"),
            )
    except OSError as e:
        return f"ERROR: could not start: {e}"
    job = Job(id=job_id, cmd=cmd, proc=proc, log=log)
    with _lock:
        _jobs[job_id] = job
    watcher = threading.Thread(target=_watch, args=(job,), name=f"bg-job-{job_id}", daemon=True)
    watcher.start()

    # Commands that fail fast (typo, missing file) answer right away.
    watcher.join(timeout=1.0)
    if not job.running:
        body, omitted = _read(job, tail=DEFAULT_TAIL, new_only=False)
        job.read_done = True
        more = f"(… {omitted} earlier lines)\n" if omitted else ""
        return f"$ {cmd}\nexit={job.code} (finished within a second — no need to poll)\n{more}{body}"
    return (
        f"started background job #{job_id} (pid {proc.pid}): {cmd}\n"
        f"log: {log}\n"
        f"Keep working meanwhile. Check it with bg_output(job_id={job_id}); "
        f"add wait=120 to block until it finishes."
    )


def bg_output(job_id: int | str | None = None, wait: float = 0, tail: int = DEFAULT_TAIL,
              new_only: bool = False) -> str:
    """Status + latest output of a background job (all jobs when no id)."""
    if job_id in (None, "", 0, "0"):
        return list_jobs()
    try:
        jid = int(str(job_id).lstrip("#"))
    except ValueError:
        return f"ERROR: job_id must be a number, got {job_id!r}"
    with _lock:
        job = _jobs.get(jid)
    if job is None:
        return f"ERROR: no background job #{jid}. {list_jobs()}"
    try:
        wait = max(0.0, min(float(wait or 0), MAX_WAIT))
    except (TypeError, ValueError):
        wait = 0.0
    try:
        tail = max(1, min(int(tail or DEFAULT_TAIL), 400))
    except (TypeError, ValueError):
        tail = DEFAULT_TAIL

    interrupted = False
    if wait and job.running:
        deadline = time.monotonic() + wait
        while job.running and time.monotonic() < deadline:
            if state.turn_cancelled():
                interrupted = True
                break
            time.sleep(0.2)

    finished = not job.running
    body, omitted = _read(job, tail=tail, new_only=new_only)
    if finished:
        job.read_done = True
    lines = [_headline(job), f"log: {job.log}"]
    if body:
        label = "new output" if new_only else "output"
        more = f", {omitted} earlier lines not shown — raise tail or read the log" if omitted else ""
        lines.append(f"--- {label} (last {min(tail, body.count(chr(10)) + 1)} lines{more}) ---")
        lines.append(body)
    else:
        lines.append("(no new output)" if new_only else "(no output yet)")
    if not finished:
        if interrupted:
            lines.append("(stopped waiting — the turn was cancelled; the job keeps running)")
        elif wait:
            lines.append(f"still running after waiting {fmt_secs(wait)} — check again later "
                         f"or bg_kill(job_id={job.id}).")
    return "\n".join(lines)


def bg_kill(job_id: int | str) -> str:
    """Stop a background job and its child processes."""
    try:
        jid = int(str(job_id).lstrip("#"))
    except (TypeError, ValueError):
        return f"ERROR: job_id must be a number, got {job_id!r}"
    with _lock:
        job = _jobs.get(jid)
    if job is None:
        return f"ERROR: no background job #{jid}. {list_jobs()}"
    if not job.running:
        job.read_done = True
        return f"background job #{jid} already {job.status}: {job.cmd}"
    _stop(job)
    try:
        job.proc.wait(timeout=5)
    except subprocess.TimeoutExpired:
        pass
    # The watcher records the exit code; give it a moment so status is final.
    deadline = time.monotonic() + 1.0
    while job.running and time.monotonic() < deadline:
        time.sleep(0.02)
    job.read_done = True
    body, _omitted = _read(job, tail=15, new_only=True)
    out = f"stopped background job #{jid} after {fmt_secs(job.elapsed)}: {job.cmd}"
    return out + (f"\n--- last output ---\n{body}" if body else "")


def list_jobs() -> str:
    rows = jobs()
    if not rows:
        return "No background jobs. Start one with run_bg(cmd)."
    out = ["Background jobs:"]
    for j in rows:
        mark = "●" if j.running else ("✕" if j.killed else ("✓" if j.code == 0 else "✗"))
        extra = "" if j.running or j.read_done else " · output not read yet"
        out.append(f"{mark} #{j.id} {j.status} · {fmt_secs(j.elapsed)}{extra} — {_short(j.cmd)}")
    return "\n".join(out)


def prompt_block() -> str:
    """System-prompt section for jobs that are running or finished-but-unread."""
    rows = [j for j in jobs() if j.running or not j.read_done]
    if not rows:
        return ""
    out = ["", "", "BACKGROUND JOBS (started with run_bg — they keep running between turns):"]
    for j in rows:
        if j.running:
            out.append(f"- #{j.id} running for {fmt_secs(j.elapsed)}: {_short(j.cmd, 120)}")
        else:
            out.append(f"- #{j.id} FINISHED ({j.status}, {fmt_secs(j.elapsed)}) — output not read yet, "
                       f"call bg_output(job_id={j.id}): {_short(j.cmd, 120)}")
    return "\n".join(out)
