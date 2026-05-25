"""Status-bar shell command approval (run_bash) — visible alternative to modal overlay."""
from __future__ import annotations

from typing import Callable

from rich.markup import escape as _rich_escape
from rich.text import Text

from . import theme as ui


class ShellApprovalController:
    """Shows pending shell commands in #askbar; y/n/a keys approve or deny."""

    def __init__(self, app) -> None:
        self._app = app
        self._cmd = ""
        self._on_done: Callable[[str], None] | None = None

    @property
    def active(self) -> bool:
        return self._on_done is not None

    def begin(self, cmd: str, on_done: Callable[[str], None]) -> None:
        self._cmd = (cmd or "").replace("\n", " ")[:4000]
        self._on_done = on_done
        try:
            self._app.query_one("#prompt", object).blur()
        except Exception:
            pass
        self._refresh_bar()

    def finish_with(self, result: str) -> None:
        if not self.active:
            return
        self._finish(str(result).strip().lower() or "n")

    def cancel(self) -> None:
        self.finish_with("n")

    def _finish(self, result: str) -> None:
        cb = self._on_done
        self._on_done = None
        self._cmd = ""
        self._hide_bar()
        if cb:
            cb(result)

    def _hide_bar(self) -> None:
        try:
            bar = self._app.query_one("#askbar", object)
            bar.add_class("hidden")
            bar.update("")
        except Exception:
            pass

    def _refresh_bar(self) -> None:
        if not self.active:
            self._hide_bar()
            return
        try:
            bar = self._app.query_one("#askbar", object)
        except Exception:
            return
        preview = self._cmd
        if len(preview) > 220:
            preview = preview[:217] + "…"
        bar.remove_class("hidden")
        bar.update(
            Text.from_markup(
                f"[{ui.WARN}]⚡ Run shell command?[/]\n"
                f"[{ui.FG}]{_rich_escape(preview)}[/]\n"
                f"[{ui.OK}]y / ↵[/] run   "
                f"[{ui.ERR}]n / esc[/] cancel   "
                f"[{ui.WARN}]a[/] always (this session)"
            )
        )

    def handle_key(self, key: str) -> bool:
        if not self.active:
            return False
        if key in ("y", "enter", "return"):
            self._finish("y")
            return True
        if key in ("n", "escape"):
            self._finish("n")
            return True
        if key == "a":
            self._finish("a")
            return True
        return False
