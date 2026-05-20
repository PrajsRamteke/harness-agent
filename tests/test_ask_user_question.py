"""Tests for ask_user_question tool and question normalization."""
import json

import pytest

from jarvis.tui.ask_user import normalize_questions, format_answers_payload
from jarvis.tools.ask_user import ask_user_question


def test_normalize_questions_minimal():
    qs = normalize_questions([
        {
            "id": "approach",
            "prompt": "Which approach?",
            "options": [
                {"id": "a", "label": "Reuse existing"},
                {"id": "b", "label": "New module"},
            ],
        },
    ])
    assert len(qs) == 1
    assert qs[0].id == "approach"
    assert len(qs[0].options) == 2


def test_normalize_requires_two_options():
    with pytest.raises(ValueError, match="at least 2"):
        normalize_questions([
            {"id": "q1", "prompt": "Pick one", "options": [{"id": "a", "label": "Only"}]},
        ])


def test_ask_user_question_error_on_bad_input():
    out = ask_user_question(questions=[])
    assert out.startswith("ERROR:")


def test_format_answers_payload():
    payload = json.loads(format_answers_payload([
        {"question_id": "q1", "selected_ids": ["a"], "labels": ["A"]},
    ]))
    assert payload["answers"][0]["selected_ids"] == ["a"]
