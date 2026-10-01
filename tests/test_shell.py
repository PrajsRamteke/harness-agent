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
            t1 = threading.Thread(target=lambda: run_bash("echo one"))
            t2 = threading.Thread(target=lambda: run_bash("echo two"))
            t1.start()
            t2.start()
            t1.join(timeout=2)
            t2.join(timeout=2)

    assert len(order) == 4
    first_end = next(i for i, ev in enumerate(order) if ev.startswith("end:"))
    later_starts = [i for i, ev in enumerate(order) if ev.startswith("start:") and i > 0]
    if later_starts:
        assert first_end < later_starts[0]


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
