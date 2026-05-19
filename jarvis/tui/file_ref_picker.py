"""Inline @file picker helpers (search runs from the composer, not a separate field)."""
from __future__ import annotations

from rich.text import Text

from ..prompt_refs import search_project_files
from .modal_chrome import ROW_NAME_WIDTH


def filter_project_files(query: str) -> list[str]:
    q = (query or "").strip()
    if q.startswith("@"):
        q = q[1:]
    return search_project_files(q, max_results=60)


def file_ref_option_label(path: str) -> Text:
    name = path.rsplit("/", 1)[-1]
    label = Text()
    label.append(f"{name:<{ROW_NAME_WIDTH}}", style="bold")
    label.append(path, style="dim")
    return label
