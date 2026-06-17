"""Build native image content blocks for multimodal-capable models.

When the current model supports native image inputs (e.g. Claude, GPT-5.5),
this module builds Anthropic-format ``image`` content blocks directly from
image file paths — instead of going through OCR.

The caller (``prepare_user_prompt`` in main.py) decides whether to use native
image blocks or fall back to OCR based on:
  1. Whether the model supports images (``model_supports_images()``)
  2. Whether the user intent is text-extraction (→ OCR) vs visual analysis (→ native)
  3. Whether it's a single file or a bulk directory scan (bulk → OCR)
"""

from __future__ import annotations

import base64
import pathlib
import re
from typing import Optional

IMAGE_EXTS = frozenset({
    ".png", ".jpg", ".jpeg", ".gif", ".bmp",
    ".tiff", ".tif", ".webp", ".heic",
})

# Intent detection: when the user's message contains these patterns we
# assume they want to *read text* from the image rather than visually
# analyze it.  In that case we stay with OCR — it's more precise for
# dense document text than any model's native vision.
_TEXT_READING_RE = re.compile(
    r"\b(?:"
    r"read|extract|ocr|transcribe|digitize|"
    r"text from|what does it say|what does this say|"
    r"characters?|words? in this|printed text|scan this"
    r")\b",
    re.I,
)


def is_text_reading_intent(user_text: str) -> bool:
    """Return True if the user wants to read/extract text from an image.

    When True the pipeline should use OCR (more accurate for dense text).
    When False the image is for visual analysis → native multimodal.
    """
    return bool(_TEXT_READING_RE.search(user_text))


def is_image_path(s: str) -> Optional[pathlib.Path]:
    """Return a resolved Path if *s* looks like an image file, else None."""
    from ..path_resolve import robust_resolve
    from ..constants import CWD

    if not s:
        return None
    p = robust_resolve(s, CWD)
    if p.is_file() and p.suffix.lower() in IMAGE_EXTS:
        return p
    return None


def _detect_mime(path: pathlib.Path) -> Optional[str]:
    ext = path.suffix.lower()
    return {
        ".png": "image/png",
        ".jpg": "image/jpeg",
        ".jpeg": "image/jpeg",
        ".gif": "image/gif",
        ".bmp": "image/bmp",
        ".webp": "image/webp",
        ".heic": "image/heic",
        ".tiff": "image/tiff",
        ".tif": "image/tiff",
    }.get(ext)


def _image_to_base64(path: pathlib.Path) -> Optional[str]:
    """Read image file and return base64-encoded ASCII string."""
    try:
        return base64.standard_b64encode(path.read_bytes()).decode("ascii")
    except Exception:
        return None


def build_image_content_block(path: pathlib.Path) -> Optional[dict]:
    """Build an Anthropic-compatible native image content block.

    Returns::

        {"type": "image", "source": {"type": "base64",
         "media_type": "image/png", "data": "…base64…"}}

    Returns None if the file can't be read or the format is unsupported.
    """
    mime = _detect_mime(path)
    if not mime:
        return None
    data = _image_to_base64(path)
    if not data:
        return None
    return {
        "type": "image",
        "source": {
            "type": "base64",
            "media_type": mime,
            "data": data,
        },
    }


def build_content_blocks(
    text_parts: list[str],
    image_paths: list[pathlib.Path],
) -> list[dict]:
    """Build a list of text + image content blocks for the Anthropic API.

    Each image path that can be read produces an ``image`` content block.
    Every text that remains between/around images becomes a ``text`` block.
    Empty strings are filtered out.

    Usage::

        blocks = build_content_blocks(["describe this: ", ""], [path])
    """
    blocks: list[dict] = []
    parts_len = len(text_parts)
    images_len = len(image_paths)

    for i, path in enumerate(image_paths):
        if i < parts_len and text_parts[i]:
            blocks.append({"type": "text", "text": text_parts[i]})
        img_block = build_image_content_block(path)
        if img_block:
            blocks.append(img_block)

    # Append trailing text after the last image
    if parts_len > images_len:
        remaining = text_parts[-1]
        if remaining:
            blocks.append({"type": "text", "text": remaining})

    # If ALL image blocks failed to load and there's no text, show a caption
    if not blocks:
        caption = " ".join(t for t in text_parts if t) if any(text_parts) else "(image provided)"
        blocks.append({"type": "text", "text": caption})

    return blocks
