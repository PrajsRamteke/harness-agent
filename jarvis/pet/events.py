"""Spot the moments worth reacting to in tool results — tests and git.

``classify`` looks at a finished tool call (usually ``run_bash``, whose output
starts ``$ <cmd>`` / ``exit=<code>``) and returns event names for
:meth:`jarvis.pet.model.Pet.on_work_event`: ``tests_pass`` · ``tests_fail`` ·
``commit`` · ``push`` · ``conflict``.
"""
from __future__ import annotations

import re
from typing import Any

_TEST_CMD = re.compile(
    r"(?:^|[\s;&|(])(?:"
    r"pytest|py\.test|python3?\s+-m\s+(?:pytest|unittest)|"
    r"(?:npm|pnpm|yarn|bun)\s+(?:run\s+)?test|npx\s+(?:jest|vitest|mocha)|jest|vitest|mocha|"
    r"go\s+test|cargo\s+(?:test|nextest)|mvn\b.*\btest|\.?/?gradlew?\s+test|rspec|phpunit|"
    r"tox|nox|make\s+(?:test|check)|ctest|dotnet\s+test|swift\s+test|mix\s+test|deno\s+test"
    r")\b"
)
_COMMIT = re.compile(r"(?:^|[\s;&|(])git\s+(?:-\S+\s+)*commit\b")
_PUSH = re.compile(r"(?:^|[\s;&|(])git\s+(?:-\S+\s+)*push\b")
_CONFLICT = re.compile(r"CONFLICT \(|Automatic merge failed|Merge conflict in", re.I)
_EXIT = re.compile(r"^exit=(-?\d+)\s*$", re.M)


def _command(tool_input: Any, output: str) -> str:
    if isinstance(tool_input, dict):
        for key in ("cmd", "command"):
            if isinstance(tool_input.get(key), str):
                return tool_input[key]
    first = (output or "").split("\n", 1)[0]
    return first[2:] if first.startswith("$ ") else ""


def exit_code(output: str) -> int | None:
    m = _EXIT.search((output or "")[:400])
    return int(m.group(1)) if m else None


def classify(name: str, tool_input: Any, output: str, *, error: bool = False) -> list[str]:
    """Event names for one finished tool call (empty when nothing notable)."""
    if name != "run_bash":
        return []
    out = output or ""
    if out.startswith(("USER DENIED", "BLOCKED", "TIMEOUT")):
        return []
    cmd = _command(tool_input, out)
    code = exit_code(out)
    events: list[str] = []
    if _CONFLICT.search(out):
        events.append("conflict")
    if cmd and _TEST_CMD.search(cmd) and code is not None:
        if code == 0:
            events.append("tests_pass")
        elif code != 5:  # pytest's "no tests collected" isn't a failure
            events.append("tests_fail")
    if code == 0 and cmd and "conflict" not in events:
        if _COMMIT.search(cmd) and "nothing to commit" not in out:
            events.append("commit")
        if _PUSH.search(cmd) and "Everything up-to-date" not in out:
            events.append("push")
    return events
