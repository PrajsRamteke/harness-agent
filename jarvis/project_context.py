"""Project instruction file discovery.

Discovery records the file path only. Content is loaded on demand through the
normal read_file tool when repository instructions are relevant.
"""
from __future__ import annotations

from pathlib import Path

from . import state


PROJECT_CONTEXT_FILES = ("AGENT.md", "CLAUDE.md", "JARVIS.md")


def detect_project_context(cwd: str | Path | None = None) -> bool:
    """Detect a project context file without reading its content."""
    root = Path(cwd or Path.cwd())
    for filename in PROJECT_CONTEXT_FILES:
        path = root / filename
        if path.is_file():
            state.project_context_file = filename
            state.project_context_path = str(path)
            state.project_context_content = ""
            return True

    state.project_context_file = ""
    state.project_context_path = ""
    state.project_context_content = ""
    return False
