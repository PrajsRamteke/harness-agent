"""Rich-Console-compatible shim that routes output to a Textual RichLog.

Only the subset of the Rich Console API actually used across the codebase is
implemented: ``print``, ``rule``, ``status`` (as a no-op context manager), and
``input`` (unused in TUI mode — raises if called). Renderables are forwarded
to the app's RichLog from any thread via ``App.call_from_thread``.
"""
from contextlib import contextmanager
from typing import Any

from rich.console import Console as _RichConsole
from rich.rule import Rule
from rich.text import Text


class TUIConsole:
    def __init__(self, app, log_widget, status_widget=None):
        self._app = app
        self._log = log_widget
        self._status = status_widget
        # A headless Rich console used only to render markup strings into
        # styled Text objects before handing them to the RichLog.
        self._renderer = _RichConsole(file=None, record=False, width=120)

    # ─── internal ───────────────────────────────────────────────────────
    def _write(self, renderable):
        try:
            self._app.call_from_thread(self._log.write, renderable)
        except Exception:
            # Outside worker context or app not ready — best-effort fallback.
            try:
                self._log.write(renderable)
            except Exception:
                pass

    def _terminal_width(self) -> int:
        try:
            return max(24, int(self._log.size.width))
        except Exception:
            return 80

    # ─── Rich.Console surface ──────────────────────────────────────────
    def print(self, *objects: Any, sep: str = " ", end: str = "\n", **kwargs):
        if not objects:
            self._write(Text(""))
            return
        self._renderer.width = self._terminal_width()
        if all(isinstance(obj, str) for obj in objects):
            text = sep.join(objects)
            if end and end != "\n":
                text += end
            self._write(Text.from_markup(text))
            return
        for obj in objects:
            if isinstance(obj, str):
                # Render Rich markup strings into a Text renderable.
                self._write(Text.from_markup(obj))
            else:
                self._write(obj)

    def rule(self, title: str = "", *, style: str = "rule.line", **kwargs):
        self._write(Rule(title=title, style=style))

    @contextmanager
    def status(self, message: str = "", **kwargs):
        prev = None
        if self._status is not None:
            try:
                prev = getattr(self._status, "renderable", None)
                self._app.call_from_thread(self._status.update, message)
            except Exception:
                pass
        try:
            yield self
        finally:
            if self._status is not None:
                try:
                    self._app.call_from_thread(self._status.update, prev or "")
                except Exception:
                    pass

    def clear(self, *args, **kwargs):
        try:
            self._app.call_from_thread(self._log.clear)
        except Exception:
            try:
                self._log.clear()
            except Exception:
                pass

    def input(self, *args, **kwargs):  # noqa: D401 — unused in TUI mode
        raise RuntimeError("TUIConsole.input called — input flows through the Input widget")
