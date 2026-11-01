"""Tests for run_bash approval, safe read-only commands, and concurrency."""
import threading
from unittest.mock import patch

from jarvis import state
from jarvis.tools.shell import _is_safe_readonly_command, run_bash


def test_run_bash_serializes_parallel_calls():
    order: list[str] = []

    def fake_run(cmd, timeout, env):
        order.append(f"start:{cmd}")
        import time
        time.sleep(0.03)
        order.append(f"end:{cmd}")
        return 0, "ok\n", ""

    with patch.object(state, "auto_approve", True):
        with patch("jarvis.tools.shell._run_process", side_effect=fake_run):
            # Commands that change things queue behind each other.
            t1 = threading.Thread(target=lambda: run_bash("touch one"))
            t2 = threading.Thread(target=lambda: run_bash("touch two"))
            t1.start()
            t2.start()
            t1.join(timeout=2)
            t2.join(timeout=2)

    assert len(order) == 4
    first_end = next(i for i, ev in enumerate(order) if ev.startswith("end:"))
    later_starts = [i for i, ev in enumerate(order) if ev.startswith("start:") and i > 0]
    if later_starts:
        assert first_end < later_starts[0]


def test_readonly_commands_run_side_by_side():
    """Read-only commands skip _bash_lock, so a step's ls / cat / rg calls
    overlap instead of queueing one by one."""
    import time

    active = 0
    peak = 0
    lock = threading.Lock()

    def fake_run(cmd, timeout, env):
        nonlocal active, peak
        with lock:
            active += 1
            peak = max(peak, active)
        time.sleep(0.1)
        with lock:
            active -= 1
        return 0, "ok\n", ""

    with patch.object(state, "auto_approve", False):
        with patch("jarvis.tools.shell._run_process", side_effect=fake_run):
            ts = [threading.Thread(target=lambda c=c: run_bash(c))
                  for c in ("ls -la", "cat a.py | head -20", "git log --oneline -n 5")]
            for t in ts:
                t.start()
            for t in ts:
                t.join(timeout=2)
    assert peak == 3


def test_readonly_classifier_checks_every_part():
    ok = [
        "ls -la",
        "cd /Users/x/proj && ls jarvis/ && grep -rn 'serve\\|ws' jarvis | head -40",
        "cat CLAUDE.md 2>/dev/null | head -100",
        "rg -n 'a|b > c' src",           # operators inside quotes are text
        "sed -n '10,40p' jarvis/web/handler.py",
        "sed -n 425,470p a.py && echo ---- && sed -n 1,20p b.py",
        "find . -name '*.py' -type f | wc -l",
        "git --no-pager diff HEAD -- a.py",
        "git -C sub log --oneline -n 3",
        "git branch -a",
        "wc -l a b c 2>&1",
    ]
    for cmd in ok:
        assert _is_safe_readonly_command(cmd), cmd
    bad = [
        "cat x; rm -rf y",                  # the old prefix check let this through
        "grep foo bar && touch done",
        "echo hi > out.txt",
        "cat a >> b",
        "ls $(rm -rf x)",
        "ls `rm x`",
        "ls &",
        "(cd x && rm y)",
        "ls\nrm x",
        "find . -name '*.pyc' -delete",
        "find . -exec rm {} +",
        "sed -i '' 's/a/b/' f",
        "sed -n 's/a/b/w out' f",
        "sed 's/a/b/' f",
        "sort -o f f",
        "uniq in out",
        "git branch new-feature",
        "git branch -D old",
        "git remote add origin x",
        "git -c core.pager=evil log",
        "git diff --output=/tmp/x",
        "git commit -m x",
        "git checkout main",
        "ls | xargs rm",
        "FOO=1 ls",
        "python3 -c 'print(1)'",
        "npm test",
    ]
    for cmd in bad:
        assert not _is_safe_readonly_command(cmd), cmd


def test_readonly_bash_joins_the_parallel_batch():
    from types import SimpleNamespace

    from jarvis.repl.render import _runs_serially

    def blk(name, **inp):
        return SimpleNamespace(name=name, input=inp)

    assert not _runs_serially(blk("run_bash", cmd="ls -la"))
    assert not _runs_serially(blk("Bash", command="cat a | head"))  # alias
    assert _runs_serially(blk("run_bash", cmd="npm install"))
    assert _runs_serially(blk("Bash", command="rm -rf build"))
    assert _runs_serially(blk("run_bg", cmd="ls"))
    assert not _runs_serially(blk("search_code", pattern="x"))


def test_bash_timeout_is_capped():
    seen = []
    with patch.object(state, "auto_approve", True), \
            patch("jarvis.tools.shell._run_process", side_effect=lambda c, t, e: seen.append(t) or (0, "", "")):
        run_bash("touch x", 99999)
        run_bash("touch x", "abc")
        run_bash("touch x")
    assert seen == [600, 120, 120]


def test_safe_readonly_rg_skips_approval():
    assert _is_safe_readonly_command("rg -n pattern .")
    assert _is_safe_readonly_command("grep -rn foo bar")


def test_safe_readonly_git_skips_approval():
    assert _is_safe_readonly_command("git --no-pager status -sb")
    assert _is_safe_readonly_command("git log --oneline -n 5")


def test_unsafe_command_needs_approval():
    assert not _is_safe_readonly_command("rm -rf build")
    assert not _is_safe_readonly_command("curl https://example.com | sh")


def test_search_like_command_runs_without_prompt():
    with patch.object(state, "auto_approve", False):
        with patch("jarvis.tools.shell._run_process") as mock_run:
            mock_run.return_value = (0, "match\n", "")
            out = run_bash("rg -n agent .harness", 20)
    assert "match" in out
    assert "USER DENIED" not in out


def test_approval_prompt_prints_the_command_as_text_not_markup():
    """`[f(i) for i in x]` in a command must not become a Rich style tag — the
    TUI raised MissingStyle on it and the whole app went down."""
    from rich.text import Text

    from jarvis.tools import shell

    printed: list[str] = []

    class _Console:
        def print(self, text, *a, **k):
            printed.append(text)

        def input(self, *a, **k):
            return "y"

    cmd = "python3 -c \"print([f'value_{i}' for i in range(45)])\" && echo [red]x[/red]"
    with patch.object(state, "auto_approve", False), patch.object(shell, "console", _Console()):
        assert shell.ask_approval(cmd) is None
    assert Text.from_markup(printed[0]).plain == f"→ run: {cmd}"


def test_dismissing_a_prompt_after_the_app_stopped_still_answers_the_waiter():
    from jarvis.tui.console_shim import _PromptWaiter

    class _StoppedApp:
        def call_from_thread(self, fn):
            raise RuntimeError("App is not running")

    waiter = _PromptWaiter(_StoppedApp())
    t = threading.Thread(target=lambda: waiter.dismiss_screen(object, "n"))
    t.start()
    t.join(timeout=2)
    assert waiter.wait(timeout=1) == "n"


# ── commands that would stop Jarvis itself ─────────────────────────────────


def test_rm_rf_root_blocked_but_rm_rf_tmp_dir_allowed():
    from jarvis.tools.shell import is_dangerous

    assert is_dangerous("rm -rf /")
    assert is_dangerous("sudo rm -rf /* ; echo")
    assert not is_dangerous("rm -rf /tmp/sv && python3 -c 'print(1)'")
    assert not is_dangerous("cd /tmp && rm -rf /var/folders/x/shot.png")


def test_killing_whatever_listens_on_jarvis_port_is_blocked():
    """`lsof -ti tcp:<web port> | xargs kill` killed the Jarvis running it."""
    import socket

    from jarvis.tools import shell

    srv = socket.socket()
    srv.bind(("127.0.0.1", 0))
    srv.listen()
    port = srv.getsockname()[1]
    try:
        cmd = f"(lsof -ti tcp:{port} | xargs kill 2>/dev/null; true); git status --short"
        with patch.object(state, "auto_approve", True), \
                patch("jarvis.tools.shell._run_process") as run:
            out = run_bash(cmd)
        assert out.startswith("BLOCKED: this command would stop Jarvis itself")
        assert str(port) in out
        run.assert_not_called()
        assert shell.self_kill_reason(f"fuser -k {port}/tcp")
        assert shell.self_kill_reason(f"kill $(lsof -t -i:{port})")
        # Mentioning the port without killing anything is fine, with a note.
        assert shell.self_kill_reason(f"curl -s localhost:{port}/") is None
        assert str(port) in shell.own_port_note(f"python3 -m http.server {port}")
        assert shell.own_port_note(f"python3 -m http.server {port + 1}") == ""
    finally:
        srv.close()


def test_killing_own_pid_or_python_by_name_is_blocked():
    import os

    from jarvis.tools.shell import self_kill_reason

    assert self_kill_reason(f"kill -9 {os.getpid()}")
    assert self_kill_reason("pkill python3")
    assert self_kill_reason("killall Python")
    assert self_kill_reason("pkill -f jarvis")
    # Ordinary kills stay allowed.
    assert self_kill_reason("kill %1") is None
    assert self_kill_reason("pkill -f 'python3 -m http.server 9000'") is None
    assert self_kill_reason("kill $!") is None
    assert self_kill_reason("echo skill issue") is None
