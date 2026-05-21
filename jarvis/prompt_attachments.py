"""Drag-and-drop file attachments in the composer.

Whitelisted media/document paths become numbered chips like ``[image 1]`` in the
UI. On submit, chips are swapped for the absolute file path in the LLM message
— no inline file upload or OCR.
"""
from __future__ import annotations

import pathlib
import re
import shlex
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple
from urllib.parse import unquote, urlparse

from .constants import CWD
from .path_resolve import robust_resolve

ATTACHMENT_TOKEN_RE = re.compile(
    r"\[(image|video|audio|document|csv) (\d+)\]",
    re.IGNORECASE,
)

# Whitelist — only these extensions become composer chips.
_IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".svg"}
_VIDEO_EXTS = {".mp4", ".mov", ".avi", ".mkv"}
_AUDIO_EXTS = {".mp3", ".wav", ".aac", ".ogg"}
_CSV_EXTS = {".csv"}
_DOCUMENT_EXTS = {".pdf", ".doc", ".docx", ".xls", ".xlsx", ".txt"}
_ATTACHABLE_EXTS = (
    _IMAGE_EXTS | _VIDEO_EXTS | _AUDIO_EXTS | _CSV_EXTS | _DOCUMENT_EXTS
)
_ATTACHABLE_KINDS = {"image", "video", "audio", "csv", "document"}

# Extensions worth scanning when regex-fallback hunting for dropped paths.
_SCAN_EXTS = tuple(
    ext.lstrip(".")
    for ext in sorted(_ATTACHABLE_EXTS, key=len, reverse=True)
)
_PATH_FALLBACK_RE = re.compile(
    rf"(?:[\w./~\-\\ ]+?)\.(?:{'|'.join(_SCAN_EXTS)})\b",
    re.IGNORECASE,
)
# Quoted absolute paths — common when terminals paste spaced filenames.
_QUOTED_SINGLE_RE = re.compile(r"'(/[^'\n]+)'")
_QUOTED_DOUBLE_RE = re.compile(r'"(/[^"\n]+)"')


def classify_attachment(path: pathlib.Path) -> str:
    """Return attachment kind: image, video, audio, csv, document, or file."""
    ext = path.suffix.lower()
    if ext in _IMAGE_EXTS:
        return "image"
    if ext in _VIDEO_EXTS:
        return "video"
    if ext in _AUDIO_EXTS:
        return "audio"
    if ext in _CSV_EXTS:
        return "csv"
    if ext in _DOCUMENT_EXTS:
        return "document"
    return "file"


def is_attachable(path: pathlib.Path) -> bool:
    """Only whitelisted media/document files become composer chips."""
    return classify_attachment(path) in _ATTACHABLE_KINDS


def _llm_path(path: pathlib.Path, *, dropped_as: str | None = None) -> str:
    """Path string sent to the model — prefer the original drag-drop form."""
    if dropped_as:
        return _normalize_dropped_token(dropped_as)
    return str(path)


def _cursor_to_offset(text: str, row: int, col: int) -> int:
    lines = (text or "").split("\n")
    row = max(0, min(row, max(0, len(lines) - 1)))
    col = max(0, min(col, len(lines[row]) if lines else 0))
    offset = sum(len(lines[i]) + 1 for i in range(row))
    return offset + col


def _offset_to_cursor(text: str, offset: int) -> Tuple[int, int]:
    offset = max(0, min(offset, len(text or "")))
    row = 0
    remaining = offset
    for i, line in enumerate((text or "").split("\n")):
        line_len = len(line)
        if remaining <= line_len:
            return i, remaining
        remaining -= line_len + 1
        row = i + 1
    lines = (text or "").split("\n")
    last = lines[-1] if lines else ""
    return max(0, len(lines) - 1), len(last)


@dataclass
class AttachmentRegistry:
    """Maps composer chips like ``[image 1]`` to resolved file paths."""

    by_label: Dict[str, pathlib.Path] = field(default_factory=dict)
    llm_paths: Dict[str, str] = field(default_factory=dict)
    counters: Dict[str, int] = field(default_factory=dict)

    def reset(self) -> None:
        self.by_label.clear()
        self.llm_paths.clear()
        self.counters.clear()

    def register(self, path: pathlib.Path, *, dropped_as: str | None = None) -> str:
        kind = classify_attachment(path)
        if kind == "file":
            raise ValueError(f"not an attachable media/document file: {path}")
        n = self.counters.get(kind, 0) + 1
        self.counters[kind] = n
        label = f"[{kind} {n}]"
        resolved = path.resolve()
        self.by_label[label] = resolved
        self.llm_paths[label] = _llm_path(resolved, dropped_as=dropped_as or str(path))
        return label

    def llm_path_for_label(self, label: str) -> Optional[str]:
        return self.llm_paths.get(label)

    def path_for_label(self, label: str) -> Optional[pathlib.Path]:
        return self.by_label.get(label)

    def labels_in_text(self, text: str) -> List[str]:
        labels: List[str] = []
        seen: set[str] = set()
        for match in ATTACHMENT_TOKEN_RE.finditer(text or ""):
            label = match.group(0)
            if label in seen:
                continue
            seen.add(label)
            if label in self.by_label:
                labels.append(label)
        return labels

    def snapshot(self) -> Dict[str, pathlib.Path]:
        return dict(self.by_label)

    def snapshot_llm_paths(self) -> Dict[str, str]:
        return dict(self.llm_paths)

    def restore(self, snapshot: Dict[str, pathlib.Path]) -> None:
        self.by_label = dict(snapshot)
        self.llm_paths = {label: str(path) for label, path in snapshot.items()}
        self.counters.clear()
        for label in snapshot:
            match = ATTACHMENT_TOKEN_RE.fullmatch(label)
            if not match:
                continue
            kind = (match.group(1) or "file").lower()
            n = int(match.group(2))
            self.counters[kind] = max(self.counters.get(kind, 0), n)


_registry = AttachmentRegistry()


def get_registry() -> AttachmentRegistry:
    return _registry


def reset_registry() -> None:
    _registry.reset()


def snapshot_registry() -> tuple[Dict[str, pathlib.Path], Dict[str, str]]:
    return _registry.snapshot(), _registry.snapshot_llm_paths()


def restore_registry(
    snapshot: Dict[str, pathlib.Path],
    llm_paths: Dict[str, str] | None = None,
) -> None:
    _registry.restore(snapshot)
    if llm_paths:
        _registry.llm_paths = dict(llm_paths)


def _normalize_dropped_token(raw: str) -> str:
    candidate = (raw or "").strip().strip("'\"")
    if candidate.lower().startswith("file://"):
        parsed = urlparse(candidate)
        return unquote(parsed.path)
    return candidate


def _looks_like_dropped_path(tok: str) -> bool:
    tok = _normalize_dropped_token(tok)
    if not tok or tok == ".":
        return False
    if ATTACHMENT_TOKEN_RE.fullmatch(tok):
        return False
    if tok.startswith("@"):
        return False
    if tok.startswith(("http://", "https://", "ftp://")):
        return False
    if tok.lower().startswith("file://"):
        return True
    if tok.startswith(("/", "~", "./", "../")):
        return True
    ext = pathlib.Path(tok).suffix.lower()
    return bool(ext and ext in _ATTACHABLE_EXTS)


def _resolve_file_path(raw: str) -> Optional[pathlib.Path]:
    candidate = _normalize_dropped_token(raw)
    if not candidate or candidate == ".":
        return None
    path = robust_resolve(candidate, CWD)
    return path if path.is_file() else None


def _try_add(
    found: List[Tuple[str, pathlib.Path]],
    seen_paths: set[pathlib.Path],
    raw: str,
) -> None:
    if not _looks_like_dropped_path(raw):
        return
    path = _resolve_file_path(raw)
    if path is None or not is_attachable(path) or path in seen_paths:
        return
    seen_paths.add(path)
    found.append((raw, path))


def extract_droppable_paths(text: str) -> List[Tuple[str, pathlib.Path]]:
    """Return ``[(raw_span_in_text, resolved_path), ...]`` for attachable files."""
    if not (text or "").strip():
        return []

    found: List[Tuple[str, pathlib.Path]] = []
    seen_paths: set[pathlib.Path] = set()
    consumed_spans: List[Tuple[int, int]] = []

    def _overlaps(start: int, end: int) -> bool:
        return any(start < e and end > s for s, e in consumed_spans)

    for match in _QUOTED_SINGLE_RE.finditer(text):
        raw = match.group(0)
        inner = match.group(1)
        start, end = match.span()
        if _overlaps(start, end):
            continue
        before = len(found)
        _try_add(found, seen_paths, inner)
        if len(found) > before:
            consumed_spans.append((start, end))
            found[-1] = (raw, found[-1][1])

    for match in _QUOTED_DOUBLE_RE.finditer(text):
        raw = match.group(0)
        inner = match.group(1)
        start, end = match.span()
        if _overlaps(start, end):
            continue
        before = len(found)
        _try_add(found, seen_paths, inner)
        if len(found) > before:
            consumed_spans.append((start, end))
            found[-1] = (raw, found[-1][1])

    try:
        tokens = shlex.split(text, posix=True)
    except ValueError:
        tokens = text.split()

    for tok in tokens:
        if ATTACHMENT_TOKEN_RE.fullmatch(tok):
            continue
        idx = text.find(tok)
        if idx >= 0 and _overlaps(idx, idx + len(tok)):
            continue
        before = len(found)
        _try_add(found, seen_paths, tok)
        if len(found) > before:
            consumed_spans.append((idx, idx + len(tok)))

    for match in _PATH_FALLBACK_RE.finditer(text):
        raw = match.group(0)
        if ATTACHMENT_TOKEN_RE.search(raw):
            continue
        start, end = match.span()
        if _overlaps(start, end):
            continue
        before = len(found)
        _try_add(found, seen_paths, raw.replace("\\ ", " "))
        if len(found) > before:
            consumed_spans.append((start, end))
            found[-1] = (raw, found[-1][1])

    return found


def _find_replacement_span(text: str, raw: str) -> Optional[Tuple[int, int]]:
    candidates = [raw, _normalize_dropped_token(raw), raw.replace("\\ ", " ")]
    seen: set[str] = set()
    for candidate in candidates:
        if not candidate or candidate in seen:
            continue
        seen.add(candidate)
        idx = text.find(candidate)
        if idx >= 0:
            return idx, len(candidate)
    return None


def tokenize_dropped_paths(
    text: str,
    registry: AttachmentRegistry | None = None,
    *,
    cursor_row: int | None = None,
    cursor_col: int | None = None,
) -> Tuple[str, int | None, int | None]:
    """Replace dropped attachable paths with numbered chips.

    Returns ``(new_text, cursor_row, cursor_col)`` with the cursor placed after
    the inserted chip nearest to the original cursor.
    """
    reg = registry or _registry
    out = text or ""
    cursor_offset: int | None = None
    if cursor_row is not None and cursor_col is not None:
        cursor_offset = _cursor_to_offset(out, cursor_row, cursor_col)

    replacements: List[Tuple[int, int, str]] = []
    for raw, path in extract_droppable_paths(out):
        inner = _normalize_dropped_token(raw)
        label = reg.register(path, dropped_as=inner)
        span = _find_replacement_span(out, raw)
        if span is None:
            continue
        replacements.append((span[0], span[1], label))

    replacements.sort(key=lambda item: (item[0], -item[1]))
    deduped: List[Tuple[int, int, str]] = []
    end = -1
    for start, length, label in replacements:
        if start < end:
            continue
        deduped.append((start, length, label))
        end = start + length

    for start, length, label in sorted(deduped, key=lambda item: item[0], reverse=True):
        if cursor_offset is not None:
            if cursor_offset > start + length:
                cursor_offset += len(label) - length
            elif cursor_offset >= start:
                cursor_offset = start + len(label)
        out = out[:start] + label + out[start + length :]

    if cursor_offset is None:
        return out, None, None
    row, col = _offset_to_cursor(out, cursor_offset)
    return out, row, col


def expand_attachment_tokens(
    text: str,
    registry: AttachmentRegistry | None = None,
) -> Tuple[str, List[str]]:
    """Swap composer chips for absolute paths in the LLM message (no file upload)."""
    reg = registry or _registry
    labels = reg.labels_in_text(text)
    if not labels:
        return text, []

    expanded = (text or "").strip()
    attached: List[str] = []
    for label in labels:
        path = reg.path_for_label(label)
        if path is None:
            continue
        llm_path = reg.llm_path_for_label(label) or str(path)
        attached.append(llm_path)
        expanded = expanded.replace(label, llm_path, 1)
    return expanded, attached
