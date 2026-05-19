"""Expand @file references in user prompts (Cursor-style @ mentions).

Syntax:
  @README.md
  @jarvis/tui/app.py
  @./scripts/install
  @"path with spaces/file.py"

On submit, each reference is read and appended as an attached file block.
"""
from __future__ import annotations

import os
import re
from typing import List, Optional, Tuple

from .constants import CWD, MAX_FILE_READ
from .path_resolve import robust_resolve
from .tools.dirs import SKIP_DIRS
from .tools.files import read_file

# Quoted or unquoted path after @
_FILE_REF_RE = re.compile(r'@(?:"([^"]+)"|([^\s@]+))')

# Active @ mention being typed (at cursor)
_ACTIVE_QUOTED_RE = re.compile(r'@"([^"]*)$')
_ACTIVE_PLAIN_RE = re.compile(r'@([^\s@]*)$')

MAX_PROMPT_REF_FILES = 10
MAX_PROMPT_REF_TOTAL_CHARS = 100_000


def extract_file_refs(text: str) -> List[str]:
    """Return unique @-referenced paths in first-seen order."""
    seen: set[str] = set()
    refs: List[str] = []
    for m in _FILE_REF_RE.finditer(text or ""):
        path = (m.group(1) or m.group(2) or "").strip()
        if not path or path in seen:
            continue
        seen.add(path)
        refs.append(path)
    return refs


def active_file_ref_at_cursor(
    text: str, row: int, col: int
) -> Optional[Tuple[int, int, str]]:
    """Return ``(row, start_col, query)`` when the cursor is inside an @ mention."""
    lines = (text or "").split("\n")
    if row < 0 or row >= len(lines):
        return None
    line = lines[row]
    col = max(0, min(col, len(line)))
    before = line[:col]

    m = _ACTIVE_QUOTED_RE.search(before)
    if m:
        start = m.start()
        if start > 0 and not before[start - 1].isspace():
            return None
        return row, start, m.group(1)

    m = _ACTIVE_PLAIN_RE.search(before)
    if not m:
        return None
    start = m.start()
    if start > 0 and not before[start - 1].isspace():
        return None
    return row, start, m.group(1) or ""


def replace_file_ref_at_cursor(
    text: str, row: int, col: int, rel_path: str
) -> Tuple[str, Tuple[int, int]]:
    """Replace the active @ mention with ``@rel_path `` and return new text + cursor."""
    active = active_file_ref_at_cursor(text, row, col)
    if active is None:
        return text, (row, col)

    row, start_col, _query = active
    lines = text.split("\n")
    line = lines[row]
    col = max(0, min(col, len(line)))
    token = f"@{rel_path} "
    new_line = line[:start_col] + token + line[col:]
    lines[row] = new_line
    new_col = start_col + len(token)
    return "\n".join(lines), (row, new_col)


def _is_skipped(path) -> bool:
    return any(part in SKIP_DIRS for part in path.parts)


def search_project_files(query: str, max_results: int = 50) -> List[str]:
    """Return project-relative file paths matching a substring query."""
    q = (query or "").strip().lower()
    try:
        max_results = max(1, min(200, int(max_results)))
    except (TypeError, ValueError):
        max_results = 50

    scored: List[Tuple[int, str]] = []
    for root_dir, dirs, names in os.walk(CWD, topdown=True):
        dirs[:] = [d for d in dirs if d not in SKIP_DIRS]
        for name in names:
            full = os.path.join(root_dir, name)
            try:
                rel = os.path.relpath(full, CWD)
            except ValueError:
                continue
            if rel.startswith(".."):
                continue
            rel_norm = rel.replace("\\", "/")
            hay = rel_norm.lower()
            name_l = name.lower()
            if q:
                if q not in hay and q not in name_l:
                    continue
                score = 0
                if name_l == q:
                    score += 100
                elif name_l.startswith(q):
                    score += 80
                elif hay.startswith(q):
                    score += 60
                elif q in name_l:
                    score += 40
                else:
                    score += 20
                depth_penalty = rel_norm.count("/")
                score -= depth_penalty
            else:
                score = -rel_norm.count("/")
            scored.append((score, rel_norm))
            if len(scored) >= max_results * 8:
                break
        if len(scored) >= max_results * 8:
            break

    scored.sort(key=lambda row: (-row[0], row[1]))
    out: List[str] = []
    seen: set[str] = set()
    for _, rel in scored:
        if rel in seen:
            continue
        seen.add(rel)
        p = CWD / rel
        if not p.is_file():
            continue
        out.append(rel)
        if len(out) >= max_results:
            break
    return out


def expand_file_refs(text: str) -> Tuple[str, List[str]]:
    """Expand @file tokens into attached file blocks for the model.

    Returns ``(expanded_message, attached_relative_paths)``.
    The original user text (with @ tokens) is kept; file bodies are appended.
    """
    refs = extract_file_refs(text)
    if not refs:
        return text, []

    blocks: List[str] = []
    attached: List[str] = []
    total_chars = len(text or "")

    for ref in refs[:MAX_PROMPT_REF_FILES]:
        content = read_file(ref)
        p = robust_resolve(ref)
        try:
            display = str(p.relative_to(CWD)).replace("\\", "/")
        except ValueError:
            display = ref

        if content.startswith("ERROR:"):
            blocks.append(
                f'<file path="{display}" error="true">\n{content}\n</file>'
            )
            continue

        if total_chars + len(content) > MAX_PROMPT_REF_TOTAL_CHARS:
            blocks.append(
                f'<file path="{display}" truncated="true">\n'
                f"(omitted — total @ attachment size would exceed "
                f"{MAX_PROMPT_REF_TOTAL_CHARS:,} chars)\n</file>"
            )
            continue

        attached.append(display)
        total_chars += len(content)
        if len(content) > MAX_FILE_READ:
            content = content[:MAX_FILE_READ] + "\n… (truncated)"
        blocks.append(f'<file path="{display}">\n{content}\n</file>')

    if len(refs) > MAX_PROMPT_REF_FILES:
        blocks.append(
            f"(skipped {len(refs) - MAX_PROMPT_REF_FILES} extra @ references — "
            f"max {MAX_PROMPT_REF_FILES} files per message)"
        )

    expanded = (text or "").strip()
    if blocks:
        expanded += "\n\n--- Attached files ---\n" + "\n\n".join(blocks)
    return expanded, attached
