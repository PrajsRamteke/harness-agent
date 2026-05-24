"""Convert Rich renderables to plain text for the web UI."""
from __future__ import annotations

from io import StringIO
from typing import Any

from rich.console import Console


def to_plain(*objects: Any, sep: str = " ") -> str:
    if not objects:
        return ""
    buf = StringIO()
    renderer = Console(
        file=buf,
        width=120,
        force_terminal=False,
        legacy_windows=False,
        no_color=True,
        highlight=False,
    )
    text = sep.join(str(o) for o in objects)
    if all(isinstance(o, str) for o in objects):
        try:
            from rich.text import Text

            renderer.print(Text.from_markup(text))
        except Exception:
            renderer.print(text)
    else:
        for obj in objects:
            renderer.print(obj)
    return buf.getvalue().rstrip("\n")
