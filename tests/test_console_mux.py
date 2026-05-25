"""Tests for TUI + web dual-channel prompts (shell approval, ask_user, input)."""
from __future__ import annotations

import json
import queue
import threading
import time

from jarvis.web.bridge import WebBridge
from jarvis.web.console_mux import WebMuxConsole


class _FakePrimary:
    def __init__(self) -> None:
        self.shell_calls: list[str] = []
        self.ask_calls = 0
        self.input_calls = 0
        self.shell_cancel: list[str] = []
        self.ask_cancel: list[str] = []
        self.input_cancel: list[str | None] = []
        self._shell_wait = threading.Event()
        self._shell_result = "y"
        self._ask_wait = threading.Event()
        self._ask_result = json.dumps({"answers": [{"question_id": "q1", "selected": ["a"]}]})
        self._input_wait = threading.Event()
        self._input_result = "hello"

    def prompt_shell_approval(self, cmd: str) -> str:
        self.shell_calls.append(cmd)
        self._shell_wait.wait(timeout=2.0)
        return self._shell_result

    def cancel_shell_approval(self, result: str = "n") -> None:
        self.shell_cancel.append(result)
        self._shell_result = result
        self._shell_wait.set()

    def prompt_ask_user_question(self, questions) -> str:
        self.ask_calls += 1
        self._ask_wait.wait(timeout=2.0)
        return self._ask_result

    def cancel_ask_user_question(self, payload: str) -> None:
        self.ask_cancel.append(payload)
        self._ask_result = payload
        self._ask_wait.set()

    def input(self, prompt: str = "", *, password: bool = False, **kwargs) -> str:
        self.input_calls += 1
        self._input_wait.wait(timeout=2.0)
        if self._input_result is None:
            raise EOFError("Input cancelled")
        return self._input_result

    def cancel_text_input(self, result: str | None) -> None:
        self.input_cancel.append(result)
        self._input_result = result
        self._input_wait.set()


def _attach_subscriber(bridge: WebBridge) -> queue.Queue[str]:
    sub = bridge.subscribe()
    return sub


def test_shell_approval_without_web_uses_tui_only():
    primary = _FakePrimary()
    bridge = WebBridge()
    mux = WebMuxConsole(primary, bridge)

    def run_tui():
        primary._shell_wait.set()
        return mux.prompt_shell_approval("echo hi")

    t = threading.Thread(target=run_tui)
    t.start()
    t.join(timeout=3.0)

    assert not t.is_alive()
    assert primary.shell_calls == ["echo hi"]
    assert primary.shell_cancel == []


def test_shell_approval_web_answer_unblocks_tui():
    primary = _FakePrimary()
    bridge = WebBridge()
    mux = WebMuxConsole(primary, bridge)
    _attach_subscriber(bridge)

    out_holder: list[str] = []

    def run_prompt():
        out_holder.append(mux.prompt_shell_approval("echo web"))

    t = threading.Thread(target=run_prompt)
    t.start()

    deadline = time.time() + 2.0
    prompt_id = None
    while time.time() < deadline:
        for evt in bridge.pending_events():
            if evt["type"] == "shell_approval":
                prompt_id = evt["data"]["id"]
                break
        if prompt_id:
            break
        time.sleep(0.02)

    assert prompt_id is not None
    assert bridge.resolve_prompt(prompt_id, "a")
    t.join(timeout=3.0)

    assert not t.is_alive()
    assert out_holder == ["a"]
    assert primary.shell_cancel == ["a"]
    assert primary.shell_calls == ["echo web"]


def test_shell_approval_tui_answer_dismisses_web_prompt():
    primary = _FakePrimary()
    bridge = WebBridge()
    mux = WebMuxConsole(primary, bridge)
    _attach_subscriber(bridge)

    def run_prompt():
        primary._shell_result = "y"
        primary._shell_wait.set()
        return mux.prompt_shell_approval("echo tui")

    assert run_prompt() == "y"
    assert bridge.pending_events() == []


def test_ask_user_web_answer_reaches_tui():
    primary = _FakePrimary()
    bridge = WebBridge()
    mux = WebMuxConsole(primary, bridge)
    _attach_subscriber(bridge)

    payload = {"answers": [{"question_id": "q1", "selected": ["opt1"]}]}
    out_holder: list[str] = []

    def run_prompt():
        out_holder.append(
            mux.prompt_ask_user_question(
                [{"id": "q1", "prompt": "Pick", "options": [{"id": "opt1", "label": "One"}, {"id": "opt2", "label": "Two"}]}]
            )
        )

    t = threading.Thread(target=run_prompt)
    t.start()

    prompt_id = None
    deadline = time.time() + 2.0
    while time.time() < deadline:
        for evt in bridge.pending_events():
            if evt["type"] == "ask_user":
                prompt_id = evt["data"]["id"]
                break
        if prompt_id:
            break
        time.sleep(0.02)

    assert prompt_id is not None
    assert bridge.resolve_prompt(prompt_id, json.dumps(payload))
    t.join(timeout=3.0)

    assert not t.is_alive()
    assert json.loads(out_holder[0]) == payload
    assert primary.ask_cancel


def test_input_web_answer_unblocks_tui():
    primary = _FakePrimary()
    bridge = WebBridge()
    mux = WebMuxConsole(primary, bridge)
    _attach_subscriber(bridge)

    out_holder: list[str] = []

    def run_prompt():
        out_holder.append(mux.input("Name?", password=False))

    t = threading.Thread(target=run_prompt)
    t.start()

    prompt_id = None
    deadline = time.time() + 2.0
    while time.time() < deadline:
        for evt in bridge.pending_events():
            if evt["type"] == "text_input":
                prompt_id = evt["data"]["id"]
                break
        if prompt_id:
            break
        time.sleep(0.02)

    assert prompt_id is not None
    assert bridge.resolve_prompt(prompt_id, "Alice")
    t.join(timeout=3.0)

    assert not t.is_alive()
    assert out_holder == ["Alice"]
    assert primary.input_cancel == ["Alice"]
