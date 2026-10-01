"""Files attached in the web remote: photos, screen recordings, PDFs, logs …

The page uploads each file on its own (``POST /api/upload``, raw body) and
``save_upload`` streams it into ``<config>/uploads/<id>/`` next to a small
``.meta.json``. A prompt then names those ids, and ``user_content`` builds the
user message:

* the typed text, then a note listing every file with its path on this
  computer, so tools can open it (``read_document``, ``read_file``, shell);
* one *reference* block per image —
  ``{"type": "image", "source": {"type": "jarvis_upload", "id": …}}``.
  History keeps the reference, never the bytes: ``materialize_uploads`` turns
  the newest few into real base64 image blocks right before each request
  (``repl/stream.py``), or into a one-line note when the model can't see
  images. Switching models mid-session just works, and sessions.db and web
  snapshots stay small.

``attachments_in`` reads the note back so transcripts show the files instead
of paths. Files are only ever served by id (``/api/media/<id>``), never by
path; ``expose`` gives a tool's screenshot a memory-only id the same way.

Unsent uploads (removed from the tray, tab closed) are pruned after a day,
sent ones after ``HARNESS_UPLOAD_KEEP_DAYS`` (default 30) — ``prune`` runs when
the web remote starts.
"""
from __future__ import annotations

import base64
import errno
import hashlib
import json
import os
import re
import secrets
import shutil
import struct
import subprocess
import sys
import threading
import time
import unicodedata
from collections import OrderedDict
from pathlib import Path
from typing import Any, BinaryIO, Iterable

from .constants import CONFIG_DIR


def _env_int(name: str, default: int) -> int:
    try:
        value = int(os.environ.get(name, "") or default)
    except ValueError:
        return default
    return value if value > 0 else default


UPLOAD_DIR = CONFIG_DIR / "uploads"
MAX_FILE_MB = _env_int("HARNESS_UPLOAD_MAX_MB", 50)
MAX_FILE_BYTES = MAX_FILE_MB * 1024 * 1024
MAX_FILES = 10                  # per message
KEEP_DAYS = _env_int("HARNESS_UPLOAD_KEEP_DAYS", 30)
UNSENT_TTL = 24 * 3600          # attached, never sent

# What the model gets: newest images first, within a count and byte budget
# (the Messages API caps one image at 5 MB and a request at 32 MB).
KEEP_IMAGES = 8
REQUEST_IMAGE_BYTES = 16 * 1024 * 1024
MODEL_EDGE = 1568               # vision models downscale anything bigger anyway
MODEL_MAX_BYTES = 3_500_000
PNG_TO_JPEG_BYTES = 1_500_000
THUMB_EDGE = 720                # transcript / tray tiles (2× for retina)
VIEW_EDGE = 2560                # full-screen viewer, for formats browsers can't show

REF_SOURCE = "jarvis_upload"
NOTE_HEAD = "[Attached in Jarvis web · saved on this computer]"

_ID_RE = re.compile(r"^[0-9a-f]{16}$")
_EXPOSED_RE = re.compile(r"^s[0-9a-f]{15}$")
_NOTE_ID_RE = re.compile(r"[\\/]uploads[\\/]([0-9a-f]{16})[\\/]")
_NOTE_LINE_RE = re.compile(r"^- (?P<name>.+?) \((?P<info>[^()]*)\): (?P<path>.+)$")


class UploadError(Exception):
    """A refused or failed upload: ``status`` is the HTTP code, ``code`` a short
    machine-readable reason, the message is what the person reads."""

    def __init__(self, status: int, code: str, message: str) -> None:
        super().__init__(message)
        self.status = status
        self.code = code


# ─── Types ────────────────────────────────────────────────────────────────

# extension → (kind, mime). Kinds drive the icon, the label and the hint the
# model gets; anything not listed is accepted only when it reads as UTF-8 text.
_TYPES: dict[str, tuple[str, str]] = {
    ".png": ("image", "image/png"), ".jpg": ("image", "image/jpeg"), ".jpeg": ("image", "image/jpeg"),
    ".gif": ("image", "image/gif"), ".webp": ("image", "image/webp"), ".heic": ("image", "image/heic"),
    ".heif": ("image", "image/heif"), ".avif": ("image", "image/avif"), ".bmp": ("image", "image/bmp"),
    ".tif": ("image", "image/tiff"), ".tiff": ("image", "image/tiff"), ".svg": ("image", "image/svg+xml"),
    ".mp4": ("video", "video/mp4"), ".m4v": ("video", "video/x-m4v"), ".mov": ("video", "video/quicktime"),
    ".webm": ("video", "video/webm"), ".mkv": ("video", "video/x-matroska"), ".avi": ("video", "video/x-msvideo"),
    ".mp3": ("audio", "audio/mpeg"), ".m4a": ("audio", "audio/mp4"), ".aac": ("audio", "audio/aac"),
    ".wav": ("audio", "audio/wav"), ".ogg": ("audio", "audio/ogg"), ".oga": ("audio", "audio/ogg"),
    ".opus": ("audio", "audio/opus"), ".flac": ("audio", "audio/flac"), ".weba": ("audio", "audio/webm"),
    ".pdf": ("pdf", "application/pdf"),
    ".doc": ("document", "application/msword"),
    ".docx": ("document", "application/vnd.openxmlformats-officedocument.wordprocessingml.document"),
    ".odt": ("document", "application/vnd.oasis.opendocument.text"), ".rtf": ("document", "application/rtf"),
    ".pages": ("document", "application/vnd.apple.pages"), ".epub": ("document", "application/epub+zip"),
    ".xls": ("sheet", "application/vnd.ms-excel"),
    ".xlsx": ("sheet", "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"),
    ".ods": ("sheet", "application/vnd.oasis.opendocument.spreadsheet"),
    ".numbers": ("sheet", "application/vnd.apple.numbers"),
    ".csv": ("sheet", "text/csv"), ".tsv": ("sheet", "text/tab-separated-values"),
    ".ppt": ("slides", "application/vnd.ms-powerpoint"),
    ".pptx": ("slides", "application/vnd.openxmlformats-officedocument.presentationml.presentation"),
    ".odp": ("slides", "application/vnd.oasis.opendocument.presentation"),
    ".key": ("slides", "application/vnd.apple.keynote"),
    ".zip": ("archive", "application/zip"), ".tar": ("archive", "application/x-tar"),
    ".gz": ("archive", "application/gzip"), ".tgz": ("archive", "application/gzip"),
    ".7z": ("archive", "application/x-7z-compressed"),
}
_EXT_FOR_MIME = {
    "image/png": ".png", "image/jpeg": ".jpg", "image/gif": ".gif", "image/webp": ".webp",
    "image/heic": ".heic", "image/avif": ".avif", "image/bmp": ".bmp", "image/tiff": ".tiff",
    "image/svg+xml": ".svg", "application/pdf": ".pdf",
}
_KIND_LABEL = {
    "image": "image", "video": "video", "audio": "audio", "pdf": "PDF", "document": "document",
    "sheet": "spreadsheet", "slides": "slides", "archive": "archive", "text": "text",
}
# Raster formats the Messages API takes as-is; the rest are converted first.
_MODEL_NATIVE = {"image/png", "image/jpeg", "image/gif", "image/webp"}
# Formats every current browser can show in an <img>.
_BROWSER_IMAGES = {"image/png", "image/jpeg", "image/gif", "image/webp", "image/svg+xml", "image/avif", "image/bmp"}

ACCEPTED_SUMMARY = "images, video, audio, PDFs, Office and iWork files, spreadsheets, archives and text files"


def _sniff_image(head: bytes) -> str | None:
    """Real image type from the first bytes (the extension can lie)."""
    if head.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if head[:3] == b"\xff\xd8\xff":
        return "image/jpeg"
    if head[:6] in (b"GIF87a", b"GIF89a"):
        return "image/gif"
    if head[:4] == b"RIFF" and head[8:12] == b"WEBP":
        return "image/webp"
    if head[:2] == b"BM" and len(head) > 26:
        return "image/bmp"
    if head[:4] in (b"II*\x00", b"MM\x00*"):
        return "image/tiff"
    if head[4:8] == b"ftyp":
        brand = head[8:12]
        if brand in (b"avif", b"avis"):
            return "image/avif"
        if brand in (b"heic", b"heix", b"hevc", b"hevx", b"heim", b"heis", b"mif1", b"msf1"):
            return "image/heic"
    probe = head[:4096].lstrip().lower()
    if probe.startswith((b"<?xml", b"<svg", b"<!--")) and b"<svg" in probe:
        return "image/svg+xml"
    return None


def _looks_like_text(head: bytes) -> bool:
    if not head or b"\x00" in head:
        return False
    try:
        head.decode("utf-8")
    except UnicodeDecodeError as exc:
        # A multi-byte character cut off at the end of the sample is fine.
        return exc.start >= len(head) - 3 and exc.reason == "unexpected end of data"
    return True


def _clean_name(raw: str) -> str:
    """A safe display + file name: no folders, control or reserved characters."""
    name = unicodedata.normalize("NFC", str(raw or ""))
    name = name.replace("\\", "/").rsplit("/", 1)[-1]
    name = "".join(ch for ch in name if ch >= " " and ch not in '<>:"|?*\x7f')
    name = name.strip().strip(".").strip()
    stem, ext = os.path.splitext(name)
    if len(name) > 120:
        ext = ext[:16]
        name = stem[: 120 - len(ext)].rstrip() + ext
    return name


def _classify(filename: str, content_type: str, head: bytes) -> tuple[str, str, str]:
    """``(kind, mime, name)`` for an upload, or UploadError 415."""
    name = _clean_name(filename)
    ext = os.path.splitext(name)[1].lower()
    known = _TYPES.get(ext)
    sniffed = _sniff_image(head)
    ctype = (content_type or "").split(";")[0].strip().lower()

    if known and known[0] == "image":
        if sniffed is None:
            raise UploadError(415, "bad_image", f"“{name}” isn’t a readable image. It may be damaged.")
        mime = sniffed  # a .png that's really a JPEG is shown and sent as JPEG
        return "image", mime, name
    if known and known[0] == "pdf" and not head.startswith(b"%PDF-"):
        raise UploadError(415, "bad_pdf", f"“{name}” isn’t a readable PDF. It may be damaged.")
    if known:
        return known[0], known[1], name
    # No extension we know: pasted screenshots arrive as "image.png", but some
    # browsers send a bare name — trust the bytes for images and text.
    if sniffed:
        base = name or "image"
        return "image", sniffed, base + _EXT_FOR_MIME.get(sniffed, "")
    if head.startswith(b"%PDF-"):
        return "pdf", "application/pdf", (name or "document") + (".pdf" if not ext else "")
    if _looks_like_text(head):
        mime = "application/json" if ext == ".json" else "text/plain"
        return "text", mime, name or "text.txt"
    shown = name or ctype or "this file"
    raise UploadError(415, "unsupported", f"“{shown}” can’t be attached. Jarvis takes {ACCEPTED_SUMMARY}.")


def human_size(n: int | float) -> str:
    n = float(n or 0)
    if n < 1024:
        return f"{int(n)} B"
    for unit in ("KB", "MB", "GB"):
        n /= 1024
        if n < 1024 or unit == "GB":
            break
    shown = f"{n:.1f}".removesuffix(".0") if n < 10 else f"{n:.0f}"
    return f"{shown} {unit}"


# ─── Image helpers (no Pillow needed: sips on macOS, ImageMagick elsewhere) ─

def _pil():
    try:
        from PIL import Image, ImageOps  # optional — used when installed
    except Exception:
        return None
    return Image, ImageOps


def _have_sips() -> bool:
    return sys.platform == "darwin" and shutil.which("sips") is not None


def _run(args: list[str], timeout: float = 30.0) -> bool:
    try:
        r = subprocess.run(args, capture_output=True, timeout=timeout)
    except (OSError, subprocess.SubprocessError):
        return False
    return r.returncode == 0


def _sips_dims(path: Path) -> tuple[int, int] | None:
    if not _have_sips():
        return None
    try:
        r = subprocess.run(["sips", "-g", "pixelWidth", "-g", "pixelHeight", str(path)],
                           capture_output=True, text=True, timeout=15)
    except (OSError, subprocess.SubprocessError):
        return None
    w = re.search(r"pixelWidth:\s*(\d+)", r.stdout)
    h = re.search(r"pixelHeight:\s*(\d+)", r.stdout)
    return (int(w.group(1)), int(h.group(1))) if w and h else None


def _raw_dims(path: Path) -> tuple[int, int] | None:
    """Stored pixel size (before any EXIF rotation)."""
    from .tools.screenshot import image_size

    return image_size(path) or _sips_dims(path)


def _exif_orientation(path: Path) -> tuple[int, int, str] | None:
    """``(orientation, byte offset of its value, struct endian)`` from a JPEG's EXIF."""
    try:
        with open(path, "rb") as fh:
            data = fh.read(256 * 1024)
    except OSError:
        return None
    if data[:2] != b"\xff\xd8":
        return None
    i = 2
    try:
        while i + 4 <= len(data):
            if data[i] != 0xFF:
                return None
            marker = data[i + 1]
            if marker == 0xFF:  # fill byte
                i += 1
                continue
            if marker in (0xD9, 0xDA):  # end of image / start of scan
                return None
            seg = struct.unpack(">H", data[i + 2:i + 4])[0]
            if marker == 0xE1 and data[i + 4:i + 10] == b"Exif\x00\x00":
                tiff = i + 10
                order = data[tiff:tiff + 2]
                fmt = "<" if order == b"II" else ">" if order == b"MM" else ""
                if not fmt:
                    return None
                ifd = tiff + struct.unpack(fmt + "I", data[tiff + 4:tiff + 8])[0]
                count = struct.unpack(fmt + "H", data[ifd:ifd + 2])[0]
                for k in range(count):
                    entry = ifd + 2 + 12 * k
                    if struct.unpack(fmt + "H", data[entry:entry + 2])[0] == 0x0112:
                        value = entry + 8
                        return struct.unpack(fmt + "H", data[value:value + 2])[0], value, fmt
                return None
            i += 2 + seg
    except struct.error:
        return None
    return None


def display_dims(path: Path, mime: str) -> tuple[int, int] | None:
    """Pixel size as shown (EXIF rotation applied) — the page reserves space with it."""
    dims = _raw_dims(path)
    if dims and mime == "image/jpeg":
        found = _exif_orientation(path)
        if found and found[0] in (5, 6, 7, 8):
            dims = (dims[1], dims[0])
    return dims


# sips steps that undo each EXIF orientation (it keeps the tag, never rotates).
_SIPS_UPRIGHT = {
    2: [["-f", "horizontal"]], 3: [["-r", "180"]], 4: [["-f", "vertical"]],
    5: [["-r", "90"], ["-f", "horizontal"]], 6: [["-r", "90"]],
    7: [["-r", "270"], ["-f", "horizontal"]], 8: [["-r", "270"]],
}


def _sips_upright(jpeg: Path) -> None:
    """Rotate a JPEG's pixels to match its EXIF orientation, then mark it upright."""
    found = _exif_orientation(jpeg)
    if not found or found[0] not in _SIPS_UPRIGHT:
        return
    orientation, offset, fmt = found
    for step in _SIPS_UPRIGHT[orientation]:
        if not _run(["sips", *step, str(jpeg)]):
            return
    found = _exif_orientation(jpeg)  # sips may have rewritten the header
    if found:
        try:
            with open(jpeg, "r+b") as fh:
                fh.seek(found[1])
                fh.write(struct.pack(found[2] + "H", 1))
        except OSError:
            pass


def _convert(src: Path, dst: Path, *, max_edge: int, fmt: str) -> Path | None:
    """Write ``src`` as ``fmt`` ("jpeg" | "png"), upright, long edge ≤ ``max_edge``.
    Returns ``dst`` or None when no converter can read the file."""
    tmp = dst.with_name(f".{dst.name}.{secrets.token_hex(3)}")
    pil = _pil()
    if pil is not None:
        Image, ImageOps = pil
        try:
            with Image.open(src) as im:
                im.seek(0)
                im = ImageOps.exif_transpose(im)
                im.thumbnail((max_edge, max_edge))
                if fmt == "jpeg":
                    if im.mode in ("RGBA", "LA", "P"):
                        im = im.convert("RGBA")
                        flat = Image.new("RGB", im.size, (255, 255, 255))
                        flat.paste(im, mask=im.split()[-1])
                        im = flat
                    elif im.mode != "RGB":
                        im = im.convert("RGB")
                    im.save(tmp, "JPEG", quality=85, optimize=True)
                else:
                    if im.mode not in ("RGB", "RGBA", "L", "LA", "P"):
                        im = im.convert("RGBA")
                    im.save(tmp, "PNG", optimize=True)
            os.replace(tmp, dst)
            return dst
        except Exception:
            tmp.unlink(missing_ok=True)
    if _have_sips():
        dims = _sips_dims(src)
        args = ["sips", "-s", "format", fmt]
        if fmt == "jpeg":
            args += ["-s", "formatOptions", "85"]
        if dims is None or max(dims) > max_edge:  # -Z also enlarges, so only when bigger
            args += ["-Z", str(max_edge)]
        if _run(args + [str(src), "--out", str(tmp)]) and tmp.is_file() and tmp.stat().st_size:
            if fmt == "jpeg":
                _sips_upright(tmp)
            os.replace(tmp, dst)
            return dst
        tmp.unlink(missing_ok=True)
    magick = shutil.which("magick") or shutil.which("convert")
    if magick:
        out = f"{fmt}:{tmp}"
        args = [magick, f"{src}[0]", "-auto-orient", "-resize", f"{max_edge}x{max_edge}>"]
        if fmt == "jpeg":
            args += ["-background", "white", "-flatten", "-quality", "85"]
        if _run(args + [out]) and tmp.is_file() and tmp.stat().st_size:
            os.replace(tmp, dst)
            return dst
        tmp.unlink(missing_ok=True)
    return None


# ─── Store ────────────────────────────────────────────────────────────────

# Uploaded files keep their own name; everything Jarvis adds next to them
# starts with "." (cleaned names never do), so nothing can collide.
_META = ".meta.json"

_locks: dict[str, threading.Lock] = {}
_locks_guard = threading.Lock()


def _lock_for(uid: str) -> threading.Lock:
    with _locks_guard:
        lock = _locks.get(uid)
        if lock is None:
            if len(_locks) > 512:
                _locks.clear()
            lock = _locks[uid] = threading.Lock()
        return lock


def _write_meta(folder: Path, meta: dict[str, Any]) -> None:
    body = {k: v for k, v in meta.items() if k != "path"}
    tmp = folder / f".meta.{secrets.token_hex(3)}"
    tmp.write_text(json.dumps(body, ensure_ascii=False), encoding="utf-8")
    os.replace(tmp, folder / _META)


def save_upload(stream: BinaryIO, length: int, filename: str, content_type: str = "") -> dict[str, Any]:
    """Stream ``length`` bytes from ``stream`` into a new upload; its public record.

    Raises UploadError (empty, too large, cut off, unsupported or damaged,
    disk full) — nothing is left on disk when it does.
    """
    shown = _clean_name(filename) or "This file"
    if length <= 0:
        raise UploadError(400, "empty", f"“{shown}” is empty.")
    if length > MAX_FILE_BYTES:
        raise UploadError(413, "too_large",
                          f"“{shown}” is {human_size(length)}. Files can be up to {MAX_FILE_MB} MB.")
    uid = secrets.token_hex(8)
    folder = UPLOAD_DIR / uid
    try:
        UPLOAD_DIR.mkdir(parents=True, exist_ok=True, mode=0o700)
        folder.mkdir(mode=0o700)
    except OSError as exc:
        raise _disk_error(exc) from exc
    part = folder / ".part"
    try:
        received = 0
        with open(part, "wb") as fh:
            while received < length:
                try:
                    chunk = stream.read(min(1 << 16, length - received))
                except (OSError, ValueError) as exc:
                    raise UploadError(400, "interrupted", "The upload was interrupted. Try again.") from exc
                if not chunk:
                    break
                fh.write(chunk)
                received += len(chunk)
        if received < length:
            raise UploadError(400, "interrupted", "The upload was interrupted before it finished. Try again.")
        with open(part, "rb") as fh:
            head = fh.read(8192)
        kind, mime, name = _classify(filename, content_type, head)
        dest = folder / name
        os.replace(part, dest)
        meta: dict[str, Any] = {
            "id": uid, "name": name, "kind": kind, "mime": mime, "size": received,
            "created": time.time(), "sent": False,
        }
        if kind == "image":
            dims = display_dims(dest, mime)
            if dims:
                meta["width"], meta["height"] = dims
        _write_meta(folder, meta)
    except UploadError:
        shutil.rmtree(folder, ignore_errors=True)
        raise
    except OSError as exc:
        shutil.rmtree(folder, ignore_errors=True)
        raise _disk_error(exc) from exc
    meta["path"] = dest
    return public(meta)


def _disk_error(exc: OSError) -> UploadError:
    if exc.errno in (errno.ENOSPC, errno.EDQUOT):
        return UploadError(507, "disk_full", "The computer running Jarvis is out of disk space.")
    return UploadError(500, "write_failed",
                       f"Jarvis couldn’t save the file on the computer ({exc.strerror or exc}).")


def get(uid: str) -> dict[str, Any] | None:
    """Full record (``path`` included) of an upload or exposed image; None if gone."""
    uid = str(uid or "")
    if _EXPOSED_RE.match(uid):
        return _exposed_meta(uid)
    if not _ID_RE.match(uid):
        return None
    folder = UPLOAD_DIR / uid
    try:
        meta = json.loads((folder / _META).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(meta, dict) or meta.get("id") != uid:
        return None
    path = folder / str(meta.get("name") or "")
    if not meta.get("name") or not path.is_file():
        return None
    meta["path"] = path
    return meta


def public(meta: dict[str, Any]) -> dict[str, Any]:
    """What the page may know about a file — never its path."""
    out = {
        "id": meta["id"],
        "name": meta.get("name") or "file",
        "kind": meta.get("kind") or "file",
        "mime": meta.get("mime") or "application/octet-stream",
        "size": int(meta.get("size") or 0),
        "url": f"/api/media/{meta['id']}",
    }
    if meta.get("width") and meta.get("height"):
        out["width"], out["height"] = int(meta["width"]), int(meta["height"])
    return out


def remove(uid: str) -> bool:
    """Delete an upload that was never sent (the tray's ✕). Sent files stay —
    they belong to the conversation."""
    meta = get(uid)
    if meta is None or not _ID_RE.match(uid) or meta.get("sent"):
        return False
    shutil.rmtree(UPLOAD_DIR / uid, ignore_errors=True)
    return True


def resolve(ids: Any) -> list[dict[str, Any]]:
    """Records for the ids a prompt carries (order kept, repeats dropped)."""
    if not isinstance(ids, list):
        raise UploadError(400, "bad_attachments", "Attachments must be a list of file ids.")
    if len(ids) > MAX_FILES:
        raise UploadError(400, "too_many", f"Attach up to {MAX_FILES} files per message.")
    out: list[dict[str, Any]] = []
    seen: set[str] = set()
    for raw in ids:
        uid = str(raw.get("id") if isinstance(raw, dict) else raw or "")
        if uid in seen:
            continue
        seen.add(uid)
        meta = get(uid) if _ID_RE.match(uid) else None
        if meta is None:
            raise UploadError(410, "gone", "One of the files is no longer on the computer. Remove it and attach it again.")
        out.append(meta)
    return out


def mark_sent(metas: Iterable[dict[str, Any]]) -> None:
    now = time.time()
    for meta in metas:
        if meta.get("sent") or not _ID_RE.match(str(meta.get("id") or "")):
            continue
        meta["sent"], meta["sent_at"] = True, now
        try:
            _write_meta(UPLOAD_DIR / meta["id"], meta)
        except OSError:
            pass


def prune(now: float | None = None) -> int:
    """Remove unsent uploads after a day and sent ones after KEEP_DAYS."""
    now = time.time() if now is None else now
    removed = 0
    try:
        folders = list(UPLOAD_DIR.iterdir())
    except OSError:
        return 0
    for folder in folders:
        if not folder.is_dir() or not _ID_RE.match(folder.name):
            continue
        try:
            meta = json.loads((folder / _META).read_text(encoding="utf-8"))
            sent_at = float(meta.get("sent_at") or 0) if meta.get("sent") else 0.0
            created = float(meta.get("created") or 0)
        except (OSError, ValueError, TypeError):
            meta, sent_at = None, 0.0
            try:
                created = folder.stat().st_mtime
            except OSError:
                continue
        if sent_at:
            stale = now - sent_at > KEEP_DAYS * 86400
        else:
            stale = now - created > UNSENT_TTL
        if stale:
            shutil.rmtree(folder, ignore_errors=True)
            removed += 1
    return removed


def prune_in_background() -> None:
    threading.Thread(target=prune, daemon=True, name="jarvis-upload-prune").start()


# ─── Tool images (screenshots) shown on the web ──────────────────────────

_exposed: OrderedDict[str, Path] = OrderedDict()
_exposed_lock = threading.Lock()


def expose(path: str | Path) -> dict[str, Any] | None:
    """A memory-only id for an image a tool produced, so the page can show it."""
    p = Path(path)
    mime = _TYPES.get(p.suffix.lower(), ("", ""))
    if mime[0] != "image" or not p.is_file():
        return None
    uid = "s" + hashlib.sha1(str(p.resolve()).encode("utf-8")).hexdigest()[:15]
    with _exposed_lock:
        _exposed[uid] = p
        _exposed.move_to_end(uid)
        while len(_exposed) > 400:
            _exposed.popitem(last=False)
    meta = _exposed_meta(uid)
    return public(meta) if meta else None


def _exposed_meta(uid: str) -> dict[str, Any] | None:
    with _exposed_lock:
        p = _exposed.get(uid)
    if p is None or not p.is_file():
        return None
    try:
        size = p.stat().st_size
        with open(p, "rb") as fh:
            head = fh.read(4096)
    except OSError:
        return None
    mime = _sniff_image(head) or _TYPES.get(p.suffix.lower(), ("", "image/png"))[1]
    meta: dict[str, Any] = {"id": uid, "name": p.name, "kind": "image", "mime": mime,
                            "size": size, "path": p, "sent": True}
    dims = display_dims(p, mime)
    if dims:
        meta["width"], meta["height"] = dims
    return meta


def images_in_output(output: str) -> list[dict[str, Any]]:
    """Public records for the ``[[jarvis:image …]]`` markers in a tool's output."""
    if "[[jarvis:image " not in (output or ""):
        return []
    from .utils.tool_images import split_image_markers

    _text, paths = split_image_markers(output)
    out = []
    for p in paths:
        rec = expose(p)
        if rec:
            out.append(rec)
    return out


# ─── Serving ──────────────────────────────────────────────────────────────

def _variant_file(meta: dict[str, Any], variant: str) -> tuple[Path, str]:
    """``(path, mime)`` to serve for ``thumb`` / ``view``; the original if no
    smaller or browser-friendly copy is needed (or none can be made)."""
    src: Path = meta["path"]
    mime = meta.get("mime") or ""
    if variant not in ("thumb", "view") or meta.get("kind") != "image" or mime == "image/svg+xml":
        return src, mime
    edge = THUMB_EDGE if variant == "thumb" else VIEW_EDGE
    if mime in _BROWSER_IMAGES:
        if variant == "view" or mime == "image/gif":
            return src, mime  # full quality in the viewer; GIFs keep their animation
        dims = _raw_dims(src)
        if dims and max(dims) <= edge and int(meta.get("size") or 0) <= 400_000:
            return src, mime
    fmt = "png" if mime == "image/png" else "jpeg"
    out = src.parent / f".{variant}.{'png' if fmt == 'png' else 'jpg'}"
    with _lock_for(f"{meta['id']}:{variant}"):
        if out.is_file() and out.stat().st_mtime >= src.stat().st_mtime:
            return out, f"image/{fmt}"
        if _convert(src, out, max_edge=edge, fmt=fmt):
            return out, f"image/{fmt}"
    return src, mime


def media_file(uid: str, variant: str = "") -> tuple[Path, str, str] | None:
    """``(path, mime, download name)`` for ``/api/media/<id>``; None when unknown."""
    meta = get(uid)
    if meta is None:
        return None
    path, mime = _variant_file(meta, variant)
    if meta.get("kind") == "text" and mime.startswith("text/"):
        mime = "text/plain; charset=utf-8"  # never let a browser run what it shows
    return path, mime, str(meta.get("name") or path.name)


# ─── What the model gets ─────────────────────────────────────────────────

def _kind_label(meta: dict[str, Any]) -> str:
    return _KIND_LABEL.get(str(meta.get("kind") or ""), "file")


def note_text(metas: list[dict[str, Any]]) -> str:
    """The note under the prompt: every file, its size and where it is."""
    lines = [NOTE_HEAD]
    kinds = set()
    for m in metas:
        kinds.add(m.get("kind"))
        info = f"{_kind_label(m)}, {human_size(m.get('size') or 0)}"
        if m.get("width") and m.get("height"):
            info += f", {m['width']}×{m['height']}"
        lines.append(f"- {m.get('name')} ({info}): {m['path']}")
    hints = []
    if "image" in kinds:
        hints.append("images are included in this message when the current model can view them")
    if kinds & {"pdf", "document", "sheet", "slides"}:
        hints.append("open documents with read_document")
    if "text" in kinds:
        hints.append("text files with read_file")
    if kinds & {"video", "audio", "archive"}:
        hints.append("use shell tools (ffprobe/ffmpeg, unzip …) for media and archives")
    if hints:
        lines.append("(" + "; ".join(hints) + ")")
    return "\n".join(lines)


def _model_ready(meta: dict[str, Any]) -> bool:
    return meta.get("kind") == "image" and meta.get("mime") != "image/svg+xml"


def user_content(text: str, metas: list[dict[str, Any]]) -> str | list[dict[str, Any]]:
    """The user message ``content`` for a prompt with attachments."""
    note = note_text(metas)
    body = f"{text.strip()}\n\n{note}" if (text or "").strip() else note
    refs = [{"type": "image", "source": {"type": REF_SOURCE, "id": m["id"]}}
            for m in metas if _model_ready(m)]
    if not refs:
        return body
    # Images first: vision models answer best with the picture before the question.
    return [*refs, {"type": "text", "text": body}]


def model_image(meta: dict[str, Any]) -> tuple[Path, str] | None:
    """``(file, mime)`` the API accepts — upright, ≤ MODEL_EDGE, ≤ MODEL_MAX_BYTES."""
    src: Path = meta["path"]
    mime = meta.get("mime") or ""
    try:
        size = src.stat().st_size
    except OSError:
        return None
    dims = _raw_dims(src)
    rotated = mime == "image/jpeg" and (_exif_orientation(src) or (1,))[0] not in (1,)
    if (mime in _MODEL_NATIVE and dims and max(dims) <= MODEL_EDGE
            and size <= MODEL_MAX_BYTES and not rotated):
        return src, mime
    folder = src.parent
    with _lock_for(f"{meta['id']}:model"):
        for name, kind in ((".model.png", "image/png"), (".model.jpg", "image/jpeg")):
            cached = folder / name
            if cached.is_file() and cached.stat().st_mtime >= src.stat().st_mtime:
                return cached, kind
        if mime == "image/png" and not rotated:
            out = _convert(src, folder / ".model.png", max_edge=MODEL_EDGE, fmt="png")
            if out and out.stat().st_size <= PNG_TO_JPEG_BYTES:
                return out, "image/png"
            if out:
                out.unlink(missing_ok=True)
        out = _convert(src, folder / ".model.jpg", max_edge=MODEL_EDGE, fmt="jpeg")
        if out and out.stat().st_size <= MODEL_MAX_BYTES:
            return out, "image/jpeg"
    return None


_b64_cache: OrderedDict[tuple[str, float], tuple[str, str, int]] = OrderedDict()
_b64_lock = threading.Lock()


def _image_block(meta: dict[str, Any]) -> tuple[dict[str, Any] | None, int, str]:
    """``(base64 image block, raw bytes, why not)`` for one upload."""
    found = model_image(meta)
    if found is None:
        return None, 0, "couldn’t be converted for the model on this computer"
    path, mime = found
    try:
        key = (str(path), path.stat().st_mtime)
    except OSError:
        return None, 0, "is no longer on the computer"
    with _b64_lock:
        hit = _b64_cache.get(key)
        if hit:
            _b64_cache.move_to_end(key)
    if hit is None:
        try:
            raw = path.read_bytes()
        except OSError:
            return None, 0, "couldn’t be read"
        hit = (base64.standard_b64encode(raw).decode("ascii"), mime, len(raw))
        with _b64_lock:
            _b64_cache[key] = hit
            while len(_b64_cache) > 16:
                _b64_cache.popitem(last=False)
    data, mime, size = hit
    return {"type": "image", "source": {"type": "base64", "media_type": mime, "data": data}}, size, ""


def _is_ref(block: Any) -> bool:
    return (isinstance(block, dict) and block.get("type") == "image"
            and isinstance(block.get("source"), dict) and block["source"].get("type") == REF_SOURCE)


def materialize_uploads(messages: list[dict[str, Any]], *, vision: bool,
                        keep: int = KEEP_IMAGES) -> list[dict[str, Any]]:
    """Request-time copy of ``messages`` with upload references resolved.

    The newest ``keep`` images (within REQUEST_IMAGE_BYTES) become real image
    blocks; older ones — and all of them for a model without vision — become
    a short note (the file path is in the message's attachment note). Never
    mutates ``messages``; returns it as-is when there is nothing to resolve.
    """
    out = list(messages)
    sent = sent_bytes = 0
    for mi in range(len(messages) - 1, -1, -1):
        content = messages[mi].get("content")
        if not isinstance(content, list) or not any(_is_ref(b) for b in content):
            continue
        rebuilt: list[Any] = []
        for block in content:
            if not _is_ref(block):
                rebuilt.append(block)
                continue
            meta = get(str(block["source"].get("id") or ""))
            name = str(meta.get("name")) if meta else "image"
            if meta is None:
                note = f"[image {name} is no longer on the computer]"
            elif not vision:
                note = f"[image {name} not shown — the current model can’t view images]"
            elif sent >= keep:
                note = f"[earlier image {name} not re-sent, to save context]"
            else:
                blk, size, why = _image_block(meta)
                if blk is not None and sent_bytes + size <= REQUEST_IMAGE_BYTES:
                    rebuilt.append(blk)
                    sent += 1
                    sent_bytes += size
                    continue
                note = f"[image {name} {why}]" if blk is None else f"[earlier image {name} not re-sent, to save context]"
            rebuilt.append({"type": "text", "text": note})
        out[mi] = {**messages[mi], "content": rebuilt}
    return out


# ─── Reading attachments back (transcripts) ──────────────────────────────

def split_note(text: str) -> tuple[str, list[dict[str, Any]]]:
    """``(text without the note, [public records])`` for a sent prompt.

    A file that was pruned since comes back as ``{"missing": True}`` with the
    name from the note, so the transcript can say so instead of breaking.
    """
    at = (text or "").find(NOTE_HEAD)
    if at < 0:
        return text, []
    visible = text[:at].rstrip()
    files: list[dict[str, Any]] = []
    seen: set[str] = set()
    for line in text[at + len(NOTE_HEAD):].splitlines():
        line = line.strip()
        m = _NOTE_LINE_RE.match(line)
        if not m:
            continue
        found = _NOTE_ID_RE.search(m.group("path"))
        if not found or found.group(1) in seen:
            continue
        uid = found.group(1)
        seen.add(uid)
        meta = get(uid)
        if meta is not None:
            files.append(public(meta))
            continue
        name = os.path.basename(m.group("path").strip()) or m.group("name")
        kind = _TYPES.get(os.path.splitext(name)[1].lower(), ("file", ""))[0]
        files.append({"id": uid, "name": name, "kind": kind, "missing": True})
    return visible, files


def attachments_in(content: Any) -> tuple[str, list[dict[str, Any]]]:
    """Visible text + attached files of a user message ``content`` (str or blocks)."""
    if isinstance(content, str):
        return split_note(content.strip())
    if not isinstance(content, list):
        return "", []
    texts = []
    for raw in content:
        block = raw if isinstance(raw, dict) else getattr(raw, "__dict__", {})
        if block.get("type") == "text":
            texts.append(str(block.get("text") or ""))
    return split_note("\n\n".join(t for t in texts if t).strip())


def summary_line(files: list[dict[str, Any]], *, limit: int = 3) -> str:
    """``📎 photo.jpg · notes.pdf +2`` — for the terminal and queue chips."""
    if not files:
        return ""
    names = [str(f.get("name") or "file") for f in files[:limit]]
    more = len(files) - len(names)
    return "📎 " + " · ".join(names) + (f" +{more}" if more > 0 else "")


def queue_label(item: Any) -> str:
    """Text of a queued prompt (``state.prompt_queue`` entry), files noted."""
    if not isinstance(item, tuple):
        return str(item or "").strip()
    text = str(item[0] or "").strip()
    ids = item[2] if len(item) > 2 and isinstance(item[2], list) else []
    if not ids:
        return text
    files = [m for m in (get(i) for i in ids) if m]
    line = summary_line(files) or f"📎 {len(ids)} file{'s' if len(ids) != 1 else ''}"
    return f"{text} · {line}" if text else line
