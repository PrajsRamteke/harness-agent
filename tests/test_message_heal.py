"""Tests for tool_use/tool_result history repair before OpenCode/DeepSeek calls."""
import copy
import json
import sqlite3
import unittest
from pathlib import Path

import jarvis.state as state
from jarvis.auth.opencode_client import _anthropic_messages_to_openai
from jarvis.repl.stream import _heal_message_history


def _orphan_tool_indices(messages: list[dict]) -> list[int]:
    out = _anthropic_messages_to_openai(messages)
    bad: list[int] = []
    i = 0
    while i < len(out):
        msg = out[i]
        if msg.get("role") == "assistant" and msg.get("tool_calls"):
            i += 1
            while i < len(out) and out[i].get("role") == "tool":
                i += 1
            continue
        if msg.get("role") == "tool":
            bad.append(i)
        i += 1
    return bad


class MessageHealTests(unittest.TestCase):
    def test_keeps_valid_tool_result_pairing(self):
        state.messages = [
            {"role": "user", "content": "run"},
            {
                "role": "assistant",
                "content": [
                    {"type": "tool_use", "id": "t1", "name": "run_bash", "input": {}},
                ],
            },
            {
                "role": "user",
                "content": [{"type": "tool_result", "tool_use_id": "t1", "content": "ok"}],
            },
        ]
        _heal_message_history()
        self.assertEqual(len(state.messages), 3)
        self.assertEqual(_orphan_tool_indices(state.messages), [])

    def test_drops_tool_results_after_intervening_user_text(self):
        state.messages = [
            {
                "role": "assistant",
                "content": [
                    {"type": "tool_use", "id": "old1", "name": "resolve_context", "input": {}},
                    {"type": "tool_use", "id": "old2", "name": "list_dir", "input": {}},
                ],
            },
            {"role": "user", "content": "use another approach"},
            {
                "role": "assistant",
                "content": [
                    {"type": "tool_use", "id": "new1", "name": "run_bash", "input": {}},
                ],
            },
            {
                "role": "user",
                "content": [{"type": "tool_result", "tool_use_id": "new1", "content": "done"}],
            },
            {
                "role": "user",
                "content": [
                    {
                        "type": "tool_result",
                        "tool_use_id": "old1",
                        "content": "ERROR: tool execution cancelled before completion",
                    },
                    {
                        "type": "tool_result",
                        "tool_use_id": "old2",
                        "content": "ERROR: tool execution cancelled before completion",
                    },
                ],
            },
        ]
        _heal_message_history()
        self.assertEqual(len(state.messages), 4)
        self.assertEqual(_orphan_tool_indices(state.messages), [])
        assistant = state.messages[0]
        self.assertEqual(
            [b.get("type") for b in assistant["content"] if isinstance(b, dict)],
            [],
        )

    def test_session_1706_shape_heals(self):
        db = Path.home() / ".config/harness-agent/sessions.db"
        if not db.exists():
            self.skipTest("no local sessions.db")
        conn = sqlite3.connect(db)
        rows = conn.execute(
            "SELECT role, content_json FROM messages WHERE session_id=? ORDER BY idx",
            (1706,),
        ).fetchall()
        if not rows:
            self.skipTest("session 1706 missing")
        msgs = [{"role": r[0], "content": json.loads(r[1])} for r in rows]
        state.messages = copy.deepcopy(msgs)
        _heal_message_history()
        self.assertEqual(_orphan_tool_indices(state.messages), [])


if __name__ == "__main__":
    unittest.main()
