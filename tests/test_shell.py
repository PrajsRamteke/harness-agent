"""Tests for shell tool concurrency."""
import threading
from unittest.mock import patch

from jarvis import state
from jarvis.tools.shell import run_bash


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
