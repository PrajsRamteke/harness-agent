"""Images in tool results — how a tool hands the model something to *see*.

Tools return plain strings everywhere else in the pipeline (UI rows, the
tools inspector, session history), so an image travels as a one-line marker
inside the string::

    [[jarvis:image /tmp/jarvis-screenshots/shot-….png]]

``tool_result_content`` swaps markers for native ``image`` blocks right
before the tool_result goes into the conversation. Providers without
image-in-tool-result support get the image re-homed by their converter
(``opencode_client`` / ``codex_client``), and ``repl.trim.prune_tool_images``
keeps only the latest few so screenshots don't pile up in the context.
"""
from __future__ import annotations

import pathlib
import re
from typing import Any

_MARKER_RE = re.compile(r"^\[\[jarvis:image (.+?)\]\]\s*$", re.M)

# Anthropic accepts these natively; everything else is converted first.
_MIME = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".gif": "image/gif",
    ".webp": "image/webp",
}
MAX_IMAGE_BYTES = 4_500_000  # API limit is 5 MB per image (before base64)


def image_marker(path: str | pathlib.Path) -> str:
    return f"[[jarvis:image {path}]]"


def split_image_markers(text: str) -> tuple[str, list[str]]:
    """``(text without marker lines, [image paths])``."""
    paths = [m.group(1).strip() for m in _MARKER_RE.finditer(text or "")]
    if not paths:
        return text, []
    cleaned = _MARKER_RE.sub("", text)
    cleaned = re.sub(r"\n{3,}", "\n\n", cleaned).strip()
    return cleaned, paths


def image_block(path: str | pathlib.Path) -> dict | None:
    """Anthropic ``image`` block for a png/jpeg/gif/webp file, or None."""
    import base64

    p = pathlib.Path(path)
    mime = _MIME.get(p.suffix.lower())
    if not mime:
        return None
    try:
        raw = p.read_bytes()
    except OSError:
        return None
    if not raw or len(raw) > MAX_IMAGE_BYTES:
        return None
    return {
        "type": "image",
        "source": {"type": "base64", "media_type": mime,
                   "data": base64.standard_b64encode(raw).decode("ascii")},
    }


def tool_result_content(out: str) -> str | list[dict]:
    """The tool_result ``content``: the string itself, or text + image blocks
    when the tool attached images."""
    text, paths = split_image_markers(out)
    if not paths:
        return out
    blocks: list[dict] = []
    missing: list[str] = []
    images = []
    for p in paths:
        blk = image_block(p)
        if blk is None:
            missing.append(p)
        else:
            images.append(blk)
    if missing:
        text = (text + "\n" if text else "") + "".join(
            f"(image could not be attached: {p})\n" for p in missing
        ).rstrip()
    if text:
        blocks.append({"type": "text", "text": text})
    blocks.extend(images)
    return blocks or out


def _as_dict(block: Any) -> dict:
    if isinstance(block, dict):
        return block
    if hasattr(block, "model_dump"):
        return block.model_dump()
    return {}


def split_tool_result(content: Any) -> tuple[str, list[dict]]:
    """``(text, [image blocks])`` of a tool_result's content (str or list)."""
    if isinstance(content, str):
        return content, []
    if not isinstance(content, list):
        return str(content or ""), []
    texts: list[str] = []
    images: list[dict] = []
    for raw in content:
        b = _as_dict(raw)
        if b.get("type") == "image":
            images.append(b)
        elif b.get("type") == "text":
            texts.append(str(b.get("text") or ""))
        elif isinstance(raw, str):
            texts.append(raw)
    return "\n".join(t for t in texts if t), images


def data_url(block: dict) -> str | None:
    """``data:`` URL for a base64 image block (OpenAI-style providers)."""
    src = block.get("source") or {}
    if src.get("type") != "base64" or not src.get("data"):
        return None
    return f"data:{src.get('media_type') or 'image/png'};base64,{src['data']}"
