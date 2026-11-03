"""``/btw`` in the terminal — a small window for side questions.

``/btw how far along are you?`` opens this window over the chat. The answer
streams in here and the conversation can go on in the input below. The turn
that's running keeps running behind it: nothing is queued, added to the chat or
saved (``jarvis/btw.py``). ``esc`` stops a running answer, or closes the window —
closing **keeps** the chat in the side-chat history, like the web window.
"""
from __future__ import annotations

import threading

from rich.console import Group
from rich.markdown import Markdown
from rich.text import Text
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import CenterMiddle, Vertical, VerticalScroll
from textual.widgets import Input, Static

from .. import btw
from . import theme as ui
from .modal_chrome import TUI_MODAL_CHROME_CSS, TuiModalScreen, hint_line
from .mouse_toggle import disable_mouse, enable_mouse


class BtwScreen(TuiModalScreen[None]):
    """Side question window: streamed answers plus an input for follow-ups."""

    DEFAULT_CSS = (
        TUI_MODAL_CHROME_CSS
        + """
    BtwScreen #modal {
        width: 76%;
        max-width: 100;
        max-height: 84%;
    }
    BtwScreen #btw_log {
        height: auto;
        min-height: 4;
        max-height: 24;
        padding: 0 1;
        scrollbar-size-vertical: 1;
    }
    BtwScreen .btw_q {
        color: {ui.FG_MUTE};
        margin-top: 1;
    }
    BtwScreen .btw_a {
        height: auto;
        padding-left: 2;
    }
    BtwScreen #btw_input {
        margin-top: 1;
    }
    BtwScreen #btw_note {
        color: {ui.FG_DIM};
        padding: 0 1;
    }
    """
    )

    BINDINGS = [
        Binding("escape", "esc", "Close", show=True),
    ]

    def __init__(self, question: str = "", *, tab_id: int | None = None) -> None:
        super().__init__()
        self._side_first = (question or "").strip()
        self._side_tab = tab_id
        self._side_thread = ""
        # {"q": str, "a": str, "status": wait|live|done|error|stopped, "w": Static}
        self._side_turns: list[dict] = []
        self._ui_thread = threading.get_ident()

    # ── layout ───────────────────────────────────────────────────────
    def compose(self) -> ComposeResult:
        with CenterMiddle():
            with Vertical(id="modal"):
                yield Static("◐  /btw — a question on the side", id="modal_title")
                yield VerticalScroll(id="btw_log")
                yield Static(
                    f"[{ui.FG_DIM}]Jarvis keeps working · read-only · closing saves this side chat[/]",
                    id="btw_note",
                )
                yield Input(placeholder="ask on the side…", id="btw_input")
                yield Static(
                    hint_line(("↵", "ask"), ("esc", "stop / close")),
                    id="modal_hint",
                )

    def on_mount(self) -> None:
        enable_mouse()
        self.query_one("#btw_input", Input).focus()
        if self._side_first:
            self._ask(self._side_first)
        else:
            self._empty_hint()

    def on_unmount(self) -> None:
        disable_mouse()
        if self._side_thread:
            btw.close(self._side_thread)

    # ── asking ───────────────────────────────────────────────────────
    def _answering(self) -> bool:
        return any(t["status"] in ("wait", "live") for t in self._side_turns)

    def _empty_hint(self) -> None:
        log = self.query_one("#btw_log", VerticalScroll)
        log.mount(Static(Text(
            "Ask about progress, what Jarvis is doing, or how long it will take.",
            style=ui.FG_DIM,
        ), id="btw_empty"))

    def _ask(self, text: str) -> None:
        if self._answering():
            self.query_one("#btw_note", Static).update(
                f"[{ui.WARN}]still answering — esc stops it[/]"
            )
            return
        log = self.query_one("#btw_log", VerticalScroll)
        for w in self.query("#btw_empty"):
            w.remove()
        turn = {"q": text, "a": "", "status": "wait"}
        q_widget = Static(Text(f"› {text}", style=ui.FG_MUTE), classes="btw_q")
        turn["w"] = Static(self._body(turn), classes="btw_a")
        log.mount(q_widget, turn["w"])
        self._side_turns.append(turn)
        log.scroll_end(animate=False)
        try:
            thread = btw.ask(text, thread_id=self._side_thread, tab_id=self._side_tab, on_event=self._emit)
        except btw.BtwError as exc:
            turn["status"], turn["a"] = "error", str(exc)
            if "closed" in str(exc):
                self._side_thread = ""
            self._paint(turn)
            return
        self._side_thread = thread.id

    # ── events from the worker thread ────────────────────────────────
    def _emit(self, event: btw.Event) -> None:
        if threading.get_ident() == self._ui_thread:
            self._apply(event)
            return
        try:
            self.app.call_from_thread(self._apply, event)
        except Exception:  # noqa: BLE001 — the window closed while it answered
            pass

    def _apply(self, event: btw.Event) -> None:
        if self._side_thread and event.thread != self._side_thread:
            return
        if event.turn >= len(self._side_turns):
            return
        turn = self._side_turns[event.turn]
        if event.op == "delta":
            if turn["status"] == "stopped":
                return
            turn["status"] = "live"
            turn["a"] += event.text
        elif event.op == "done":
            turn["status"], turn["a"] = "done", event.text or turn["a"]
        elif event.op == "error":
            turn["status"], turn["a"] = "error", event.text
        else:
            return
        self._paint(turn)

    def _body(self, turn: dict):
        status, text = turn["status"], turn["a"]
        if status == "wait":
            return Text("…", style=ui.FG_DIM)
        if status == "error":
            return Text(text, style=ui.ERR)
        parts = [Markdown(text)] if text.strip() else []
        if status == "stopped":
            parts.append(Text("stopped", style=ui.FG_DIM))
        return Group(*parts) if parts else Text("")

    def _paint(self, turn: dict) -> None:
        turn["w"].update(self._body(turn))
        self.query_one("#btw_log", VerticalScroll).scroll_end(animate=False)
        note = self.query_one("#btw_note", Static)
        if not self._answering():
            note.update(f"[{ui.FG_DIM}]Jarvis keeps working · read-only · closing saves this side chat[/]")

    # ── input / keys ─────────────────────────────────────────────────
    def on_input_submitted(self, event: Input.Submitted) -> None:
        text = (event.value or "").strip()
        event.input.value = ""
        if not text:
            return
        parsed = btw.parse(text)  # "/btw …" typed here works too
        self._ask(parsed if parsed else text)

    def action_esc(self) -> None:
        if self._answering() and self._side_thread:
            btw.stop(self._side_thread)
            for turn in self._side_turns:
                if turn["status"] in ("wait", "live"):
                    turn["status"] = "stopped"
                    self._paint(turn)
            return
        self.dismiss(None)
