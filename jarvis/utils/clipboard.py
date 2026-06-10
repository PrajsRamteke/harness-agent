"""Cross-platform clipboard + copy-text helpers for the TUI and commands.

Native terminal selection over a full-width TUI copies panel borders and
whitespace-padded lines. These helpers provide the clean path: copy the raw
message text (no box drawing, no padding) straight to the system clipboard.

``copy_text_to_clipboard`` tries, in order: pyperclip, then the platform's
native CLI tool (pbcopy / clip / wl-copy / xclip / xsel).
"""
from __future__ import annotations

import platform
import re
import shutil
import subprocess
from typing import Any

__all__ = [
    "copy_text_to_clipboard",
    "normalize_copy_text",
    "extract_last_code_block",
    "conversation_plain_text",
]


def normalize_copy_text(text: str) -> str:
    """Make text paste-friendly: unify newlines, strip trailing spaces,
    collapse runs of 3+ blank lines down to one blank line."""
    if not text:
        return ""
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    lines = [ln.rstrip() for ln in text.split("\n")]
    out = "\n".join(lines)
    out = re.sub(r"\n{3,}", "\n\n", out)
    return out.strip("\n")


_FENCE_RE = re.compile(
    r"```[ \t]*([A-Za-z0-9_+.#-]*)[ \t]*\n(.*?)\n?```",
    re.DOTALL,
)


def extract_last_code_block(text: str) -> str | None:
    """Return the contents of the last fenced code block, without the fences.

    Returns ``None`` when the text has no fenced code block.
    """
    if not text:
        return None
    blocks = _FENCE_RE.findall(text)
    if not blocks:
        return None
    _lang, body = blocks[-1]
    return body.rstrip("\n")


def _block_dict(block: Any) -> dict:
    if hasattr(block, "model_dump"):
        return block.model_dump()
    return block if isinstance(block, dict) else {}


def _content_text(content: Any) -> str:
    """Visible text of a message — text blocks only (no tool noise)."""
    if isinstance(content, str):
        return content
    texts: list[str] = []
    for block in content or []:
        data = _block_dict(block)
        if data.get("type") == "text":
            texts.append(data.get("text", ""))
    return "\n\n".join(t for t in texts if t.strip())


def conversation_plain_text(messages: list[dict]) -> str:
    """Whole conversation as clean markdown — user/assistant text only.

    Tool calls, tool results, and thinking blocks are skipped so the copied
    text reads like a chat log rather than a debug dump.
    """
    parts: list[str] = []
    for msg in messages or []:
        role = msg.get("role", "")
        text = _content_text(msg.get("content", "")).strip()
        if not text:
            continue
        label = "You" if role == "user" else "Assistant"
        parts.append(f"## {label}\n\n{normalize_copy_text(text)}")
    return "\n\n".join(parts)


def copy_text_to_clipboard(text: str) -> bool:
    """Copy *text* to the system clipboard. Returns True on success."""
    if not text:
        return False
    try:
        import pyperclip  # type: ignore

        pyperclip.copy(text)
        return True
    except Exception:
        pass

    sysname = platform.system()
    candidates: list[list[str]] = []
    if sysname == "Darwin":
        candidates.append(["pbcopy"])
    elif sysname == "Windows":
        candidates.append(["clip"])
    else:
        if shutil.which("wl-copy"):
            candidates.append(["wl-copy"])
        if shutil.which("xclip"):
            candidates.append(["xclip", "-selection", "clipboard"])
        if shutil.which("xsel"):
            candidates.append(["xsel", "--clipboard", "--input"])

    for cmd in candidates:
        try:
            subprocess.run(cmd, input=text.encode("utf-8"), check=True, timeout=2)
            return True
        except Exception:
            continue
    return False
