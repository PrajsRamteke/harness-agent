"""Detect image inputs from drag-drop paths or clipboard — routes to OCR or native multimodal.

Decision logic (same as documented in ``prepare_user_prompt``):
  • **Text-reading intent** (keywords: read, extract, ocr, transcribe, …) → OCR
  • **Multiple images / bulk directory** → OCR
  • **Single image + visual analysis intent + model supports images** → native image content blocks
  • **Single image + model does NOT support images** → OCR
"""
import hashlib
import re
import shlex
import subprocess
import tempfile
import pathlib
from typing import Optional, Union

from ..constants import CWD
from .ocr import read_image_text
from ..path_resolve import robust_resolve

IMAGE_EXTS = frozenset({".png", ".jpg", ".jpeg", ".gif", ".bmp", ".tiff", ".tif", ".webp", ".heic"})


def _is_image_path(s: str) -> Optional[pathlib.Path]:
    if not s:
        return None
    p = robust_resolve(s, CWD)
    if p.is_file() and p.suffix.lower() in IMAGE_EXTS:
        return p
    return None


def extract_image_paths(text: str) -> list[tuple[str, pathlib.Path]]:
    """Return [(raw_token, resolved_path)] for image file paths found in text.

    Handles shell-escaped drag-drop paths (e.g. ``/foo/bar\\ baz.png``) and quoted paths.
    """
    found: list[tuple[str, pathlib.Path]] = []
    try:
        tokens = shlex.split(text, posix=True)
    except ValueError:
        tokens = text.split()

    for tok in tokens:
        p = _is_image_path(tok)
        if p:
            found.append((tok, p))

    # also scan raw regex for anything ending with image ext we missed
    for m in re.finditer(r"(?:[\w./~\-\\ ]+?)\.(?:png|jpg|jpeg|gif|bmp|tiff?|webp|heic)\b",
                         text, re.IGNORECASE):
        raw = m.group(0).replace("\\ ", " ")
        p = _is_image_path(raw)
        if p and not any(p == rp for _, rp in found):
            found.append((m.group(0), p))
    return found


def clipboard_image_to_file() -> Optional[pathlib.Path]:
    """If the macOS clipboard contains an image, write it to a temp PNG and return its path."""
    tmp = pathlib.Path(tempfile.gettempdir()) / "jarvis_clipboard.png"
    script = f'''try
    set pngData to (the clipboard as «class PNGf»)
    set fp to open for access POSIX file "{tmp}" with write permission
    set eof fp to 0
    write pngData to fp
    close access fp
    return "ok"
on error errMsg
    try
        close access POSIX file "{tmp}"
    end try
    return "err:" & errMsg
end try'''
    try:
        r = subprocess.run(["osascript", "-"], input=script, text=True,
                           capture_output=True, timeout=5)
    except Exception:
        return None
    if r.stdout.strip().startswith("ok") and tmp.exists() and tmp.stat().st_size > 0:
        return tmp
    return None


def file_digest(path: pathlib.Path) -> str:
    """Return a stable digest for a file so repeated clipboard pastes can be deduped."""
    return hashlib.sha256(path.read_bytes()).hexdigest()


def ocr_image_block(path: pathlib.Path, label: Optional[str] = None) -> tuple[str, str]:
    """Return an OCR wrapper block and raw OCR text for an image file."""
    display_name = label or path.name
    ocr = read_image_text(str(path))
    block = f"[image: {display_name} — OCR text]\n{ocr}\n[/image]"
    return block, ocr


def append_image_block(text: str, block: str) -> str:
    """Append an OCR block to user text with stable spacing."""
    if not text.strip():
        return block
    return f"{text.rstrip()}\n\n{block}"


# ── OCR-only processing (legacy / fallback) ────────────────────────────────

def process_input_for_images(text: str) -> str:
    """Scan input for image paths; replace each with an OCR'd text block.

    Returns plain text only (no native image content blocks).
    Always uses macOS Vision OCR.
    """
    hits = extract_image_paths(text)
    if not hits:
        return text
    out = text
    for raw, path in hits:
        block, _ = ocr_image_block(path)
        replacement = f"\n\n{block}\n"
        if raw in out:
            out = out.replace(raw, replacement, 1)
        else:
            out = append_image_block(out, block)
    return out.strip()


# ── Multimodal-aware processing ────────────────────────────────────────────

def process_input_for_images_multimodal(
    text: str,
    model_supports_images: bool = False,
) -> Union[str, list[dict]]:
    """Scan *text* for image paths and decide image-handling strategy.

    Returns:
        * ``str`` — OCR'd text when the model can't take native images or
          when the user's intent is text extraction / bulk scan.
        * ``list[dict]`` — Anthropic-format content blocks (text + image)
          when the model supports native images AND the user wants visual
          analysis of a single image.
    """
    from .image_multimodal import is_text_reading_intent, build_content_blocks

    hits = extract_image_paths(text)
    if not hits:
        return text  # no images at all

    # Multiple images → always OCR (bulk read is OCR territory)
    if len(hits) > 1:
        return process_input_for_images(text)

    # Single image
    single_path = hits[0][1]

    # Text‑reading intent → OCR (more accurate for dense text)
    if is_text_reading_intent(text):
        return process_input_for_images(text)

    # Model doesn't support native images → OCR
    if not model_supports_images:
        return process_input_for_images(text)

    # Visual analysis on a multimodal model → native image content blocks
    parts = _split_text_at_path(text, hits[0][0])
    blocks = build_content_blocks(parts, [single_path])
    return blocks


def _split_text_at_path(text: str, raw_path: str) -> list[str]:
    """Split *text* into [before, after] around *raw_path*.

    Returns at most two strings (text before, text after).  The path itself
    is removed.  Each part is stripped; empty strings are kept so the
    caller knows the position.
    """
    before, sep, after = text.partition(raw_path)
    return [before.strip(), after.strip()]
