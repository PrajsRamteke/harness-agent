"""Rich Console live panel while assistant tokens stream in (legacy REPL)."""
from __future__ import annotations

from rich.live import Live
from rich.markdown import Markdown
from rich.panel import Panel


class RichAssistantStreamDisplay:
    """Updating Panel(Markdown) during `stream.text_stream` iteration."""

    def __init__(self, console, refresh_per_second: float = 20.0):
        self._console = console
        self._refresh = refresh_per_second
        self._title = ""
        self._buf = ""
        self._live: Live | None = None

    def start(self, title: str) -> None:
        self._title = title
        self._buf = ""
        self._live = Live(
            self._panel(),
            console=self._console,
            refresh_per_second=self._refresh,
            transient=True,
        )
        self._live.start()

    def _panel(self) -> Panel:
        return Panel(
            Markdown(self._buf or " "),
            title=self._title,
            border_style="magenta",
            padding=(0, 1),
        )

    def push(self, chunk: str) -> None:
        self._buf += chunk
        if self._live is not None:
            self._live.update(self._panel())

    def stop(self) -> None:
        if self._live is not None:
            self._live.stop()
            self._live = None
