"""Ask the user structured multiple-choice questions (TUI status bar or REPL fallback)."""
from __future__ import annotations

import json

from ..console import console
from ..tui.ask_user import normalize_questions, format_answers_payload


def ask_user_question(questions: list | None = None, **kwargs) -> str:
    """Block until the user picks option(s) for each question.

    Returns JSON: ``{"answers": [{"question_id", "selected_ids", "labels"}, ...]}``.
    On cancel: ``{"answers": [], "cancelled": true}``.
    """
    raw = questions if questions is not None else kwargs.get("Questions") or kwargs.get("question")
    if raw is None and kwargs:
        # Some models pass a single question object instead of a list.
        if "prompt" in kwargs or "options" in kwargs:
            raw = [kwargs]
        else:
            raw = kwargs.get("questions")
    try:
        qs = normalize_questions(raw)
    except ValueError as e:
        return f"ERROR: {e}"

    prompt_fn = getattr(console, "prompt_ask_user_question", None)
    if prompt_fn is not None:
        try:
            return prompt_fn(qs)
        except Exception as e:
            return f"ERROR: prompt failed: {e}"

    return _repl_fallback(qs)


def _repl_fallback(questions) -> str:
    """Rich REPL: numbered options per question."""
    answers: list[dict] = []
    for q in questions:
        console.print(f"\n[bold cyan]? {q.prompt}[/]")
        if q.header:
            console.print(f"[dim]{q.header}[/]")
        for i, opt in enumerate(q.options, 1):
            line = f"  [yellow]{i}[/] {opt.label}"
            if opt.description:
                line += f" [dim]— {opt.description}[/]"
            console.print(line)
        try:
            raw = console.input("[dim]choice (number, comma-separated if multiple): [/]").strip()
        except (EOFError, KeyboardInterrupt):
            return json.dumps({"answers": [], "cancelled": True})
        if not raw:
            return json.dumps({"answers": [], "cancelled": True})
        parts = [p.strip() for p in raw.replace(",", " ").split() if p.strip()]
        indices: list[int] = []
        for p in parts:
            try:
                n = int(p)
                if 1 <= n <= len(q.options):
                    indices.append(n - 1)
            except ValueError:
                pass
        if not indices:
            indices = [0]
        if not q.allow_multiple:
            indices = indices[:1]
        selected = [q.options[i] for i in sorted(set(indices))]
        answers.append({
            "question_id": q.id,
            "selected_ids": [o.id for o in selected],
            "labels": [o.label for o in selected],
        })
    return format_answers_payload(answers)
