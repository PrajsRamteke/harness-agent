"""Tests for run_bash approval, safe read-only commands, and concurrency."""
import threading
from unittest.mock import patch

from jarvis import state
from jarvis.tools.shell import _is_safe_readonly_command, run_bash


def test_run_bash_serializes_parallel_calls():
    order: list[str] = []

    def fake_run(cmd, **kwargs):
        order.append(f"start:{cmd}")
        import time
        time.sleep(0.03)
        order.append(f"end:{cmd}")
        return type("R", (), {"stdout": "ok\n", "stderr": "", "returncode": 0})()

    with patch.object(state, "auto_approve", True):
        with patch("jarvis.tools.shell.subprocess.run", side_effect=fake_run):
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
        with patch("jarvis.tools.shell.subprocess.run") as mock_run:
            mock_run.return_value = type(
                "R", (), {"stdout": "match\n", "stderr": "", "returncode": 0}
            )()
            out = run_bash("rg -n agent .harness", 20)
    assert "match" in out
    assert "USER DENIED" not in out
