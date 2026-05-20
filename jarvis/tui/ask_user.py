"""Status-bar ask-user flow: structured multiple-choice prompts for the LLM."""
from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Callable

from rich.markup import escape as _rich_escape
from rich.text import Text

from . import theme as ui


@dataclass
class AskOption:
    id: str
    label: str
    description: str = ""


@dataclass
class AskQuestion:
    id: str
    prompt: str
    options: list[AskOption]
    header: str = ""
    allow_multiple: bool = False


def normalize_questions(raw: Any) -> list[AskQuestion]:
    """Validate and normalize tool input ``questions``."""
    if not isinstance(raw, list) or not raw:
        raise ValueError("questions must be a non-empty array")
    out: list[AskQuestion] = []
    for i, q in enumerate(raw):
        if not isinstance(q, dict):
            raise ValueError(f"questions[{i}] must be an object")
        qid = str(q.get("id") or f"q{i + 1}").strip()
        prompt = str(q.get("prompt") or "").strip()
        if not prompt:
            raise ValueError(f"questions[{i}].prompt is required")
        header = str(q.get("header") or "").strip()
        allow_multiple = bool(q.get("allow_multiple"))
        opts_raw = q.get("options")
        if not isinstance(opts_raw, list) or len(opts_raw) < 2:
            raise ValueError(f"questions[{i}].options needs at least 2 choices")
        options: list[AskOption] = []
        seen: set[str] = set()
        for j, o in enumerate(opts_raw):
            if not isinstance(o, dict):
                raise ValueError(f"questions[{i}].options[{j}] must be an object")
            oid = str(o.get("id") or f"opt{j + 1}").strip()
            if oid in seen:
                raise ValueError(f"duplicate option id {oid!r} in question {qid!r}")
            seen.add(oid)
            label = str(o.get("label") or "").strip()
            if not label:
                raise ValueError(f"questions[{i}].options[{j}].label is required")
            desc = str(o.get("description") or "").strip()
            options.append(AskOption(id=oid, label=label, description=desc))
        out.append(
            AskQuestion(
                id=qid,
                prompt=prompt,
                options=options,
                header=header,
                allow_multiple=allow_multiple,
            )
        )
    return out


def format_answers_payload(answers: list[dict]) -> str:
    return json.dumps({"answers": answers}, ensure_ascii=False)


class AskUserController:
    """Renders the #askbar strip and collects keyboard answers."""

    def __init__(self, app) -> None:
        self._app = app
        self._questions: list[AskQuestion] = []
        self._q_index = 0
        self._option_index = 0
        self._selected: set[int] = set()
        self._answers: list[dict] = []
        self._on_done: Callable[[str], None] | None = None

    @property
    def active(self) -> bool:
        return bool(self._questions) and self._on_done is not None

    def begin(self, questions: list[AskQuestion], on_done: Callable[[str], None]) -> None:
        self._questions = questions
        self._q_index = 0
        self._option_index = 0
        self._selected = set()
        self._answers = []
        self._on_done = on_done
        try:
            self._app.query_one("#prompt", object).blur()
        except Exception:
            pass
        self._refresh_bar()

    def cancel(self) -> None:
        if not self.active:
            return
        self._finish(json.dumps({"answers": [], "cancelled": True}))

    def _finish(self, payload: str) -> None:
        cb = self._on_done
        self._questions = []
        self._on_done = None
        self._hide_bar()
        if cb:
            cb(payload)

    def _hide_bar(self) -> None:
        try:
            bar = self._app.query_one("#askbar", object)
            bar.add_class("hidden")
            bar.update("")
        except Exception:
            pass

    def _current(self) -> AskQuestion | None:
        if 0 <= self._q_index < len(self._questions):
            return self._questions[self._q_index]
        return None

    def _refresh_bar(self) -> None:
        q = self._current()
        if q is None:
            self._hide_bar()
            return
        try:
            bar = self._app.query_one("#askbar", object)
        except Exception:
            return
        bar.remove_class("hidden")
        total = len(self._questions)
        pos = f"{self._q_index + 1}/{total}" if total > 1 else ""
        header = q.header or "Question"
        title = (
            f"[{ui.ACCENT}]❓ {_rich_escape(header)}[/]"
            f" [{ui.FG_DIM}]{_rich_escape(pos)}[/]"
            f"  [{ui.FG_DIM}]↑↓ select · ↵ confirm"
        )
        if q.allow_multiple:
            title += f" · space toggle · ↵ done"
        prompt_line = f"[{ui.FG}]{_rich_escape(q.prompt)}[/]"
        rows: list[str] = []
        for i, opt in enumerate(q.options):
            marker = "▶" if i == self._option_index else " "
            if q.allow_multiple and i in self._selected:
                check = f"[{ui.OK}]☑[/]"
            elif q.allow_multiple:
                check = f"[{ui.FG_DIM}]☐[/]"
            else:
                check = ""
            label = _rich_escape(opt.label)
            if i == self._option_index:
                row = f" [{ui.ACCENT}]{marker}[/] {check} [bold {ui.FG}]{label}[/]"
            else:
                row = f" [{ui.FG_DIM}]{marker}[/] {check} [{ui.FG}]{label}[/]"
            if opt.description:
                row += f" [{ui.FG_DIM}]— {_rich_escape(opt.description)}[/]"
            rows.append(row)
        bar.update(Text.from_markup(title + "\n" + prompt_line + "\n" + "\n".join(rows)))

    def handle_key(self, key: str) -> bool:
        if not self.active:
            return False
        q = self._current()
        if q is None:
            return False
        n = len(q.options)
        if key == "escape":
            self.cancel()
            return True
        if key in ("up", "k"):
            self._option_index = max(0, self._option_index - 1)
            self._refresh_bar()
            return True
        if key in ("down", "j"):
            self._option_index = min(n - 1, self._option_index + 1)
            self._refresh_bar()
            return True
        if key == "space" and q.allow_multiple:
            if self._option_index in self._selected:
                self._selected.discard(self._option_index)
            else:
                self._selected.add(self._option_index)
            self._refresh_bar()
            return True
        if key in ("enter", "return"):
            self._confirm_current()
            return True
        return False

    def _confirm_current(self) -> None:
        q = self._current()
        if q is None:
            return
        if q.allow_multiple:
            indices = sorted(self._selected) if self._selected else [self._option_index]
        else:
            indices = [self._option_index]
        selected_opts = [q.options[i] for i in indices if 0 <= i < len(q.options)]
        self._answers.append({
            "question_id": q.id,
            "selected_ids": [o.id for o in selected_opts],
            "labels": [o.label for o in selected_opts],
        })
        self._q_index += 1
        self._option_index = 0
        self._selected = set()
        if self._q_index >= len(self._questions):
            self._finish(format_answers_payload(self._answers))
        else:
            self._refresh_bar()
