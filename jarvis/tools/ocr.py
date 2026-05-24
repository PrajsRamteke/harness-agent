"""OCR tools — Windows (Windows.Media.Ocr + optional Tesseract fallback)."""
from concurrent.futures import ThreadPoolExecutor
import asyncio
import pathlib
import shutil
import subprocess
import threading

from ..constants import (
    CWD, MAX_PARALLEL_TOOLS, OCR_MAX_FILES_DEFAULT, OCR_MAX_FILES_CAP,
    OCR_CHARS_PER_IMAGE, OCR_CHARS_PER_IMAGE_CAP, OCR_WORKER_MIN,
)
from ..path_resolve import robust_resolve

IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".heic", ".tif", ".tiff", ".bmp", ".webp"}

_scanned_directories: set = set()

IMPORTANT_TEXT_HINTS = (
    "aadhaar", "aadhar", "voter", "election", "identity", "identification",
    "driving", "driver", "license", "licence", "passport", "pan", "ssn",
    "social security", "date of birth", "dob", "government", "address",
    "resume", "curriculum vitae", "experience", "education", "skills",
)


def _resolve_path(path: str) -> pathlib.Path:
    return robust_resolve(path, CWD)


def _clamp_int(value, default: int, min_value: int, max_value: int) -> int:
    try:
        n = int(value)
    except (TypeError, ValueError):
        n = default
    return max(min_value, min(max_value, n))


async def _ocr_winrt_async(path: pathlib.Path) -> str:
    from winrt.windows.storage import StorageFile, FileAccessMode  # type: ignore
    from winrt.windows.graphics.imaging import BitmapDecoder  # type: ignore
    from winrt.windows.media.ocr import OcrEngine  # type: ignore

    file = await StorageFile.get_file_from_path_async(str(path.resolve()))
    stream = await file.open_async(FileAccessMode.READ)
    decoder = await BitmapDecoder.create_async(stream)
    bitmap = await decoder.get_software_bitmap_async()
    engine = OcrEngine.try_create_from_user_profile_languages()
    if engine is None:
        return "ERROR: Windows OCR engine unavailable for current language profile"
    result = await engine.recognize_async(bitmap)
    lines = [line.text for line in result.lines]
    return "\n".join(lines).strip()


def _ocr_tesseract(path: pathlib.Path) -> str:
    if not shutil.which("tesseract"):
        return "ERROR: Install Tesseract OCR (https://github.com/UB-Mannheim/tesseract/wiki) or use Windows 10+ with winrt packages"
    r = subprocess.run(
        ["tesseract", str(path), "stdout"],
        capture_output=True,
        text=True,
        timeout=60,
    )
    if r.returncode != 0:
        return f"ERROR: tesseract failed: {r.stderr.strip()}"
    return r.stdout.strip()


def read_image_text(path: str) -> str:
    """Extract text from an image using Windows OCR (on-device)."""
    p = _resolve_path(path)
    if not p.exists():
        return f"ERROR: {path} not found"
    if not p.is_file():
        return f"ERROR: {path} is not a file"

    try:
        output = asyncio.run(_ocr_winrt_async(p))
        if output:
            return output
    except ImportError:
        pass
    except Exception as e:
        err = str(e)
        if "winrt" not in err.lower():
            fallback = _ocr_tesseract(p)
            if not fallback.startswith("ERROR"):
                return fallback
            return f"ERROR: Windows OCR failed ({err}). {fallback}"

    fallback = _ocr_tesseract(p)
    if fallback and not fallback.startswith("ERROR"):
        return fallback
    if fallback.startswith("ERROR"):
        return fallback
    return "No text detected in image."


def _discover_images(directory: str, pattern: str) -> list[pathlib.Path]:
    root = _resolve_path(directory)
    if not root.exists():
        return []
    if root.is_file():
        return [root] if root.suffix.lower() in IMAGE_EXTENSIONS else []
    return sorted(
        p for p in root.glob(pattern)
        if p.is_file() and p.suffix.lower() in IMAGE_EXTENSIONS
    )


def _display_path(path: pathlib.Path) -> str:
    try:
        return str(path.relative_to(CWD))
    except ValueError:
        return str(path)


def _score_text(text: str, keywords: list[str] | None) -> int:
    haystack = text.lower()
    terms = [k.lower() for k in (keywords or []) if k] or list(IMPORTANT_TEXT_HINTS)
    score = sum(4 for term in terms if term in haystack)
    score += min(6, sum(1 for token in ("id", "no", "number", "name") if token in haystack))
    return score


def read_images_text(
    paths: list[str] | None = None,
    directory: str = ".",
    pattern: str = "**/*",
    max_files: int = 80,
    max_workers: int | None = None,
    max_chars_per_image: int = 800,
    include_empty: bool = False,
    keywords: list[str] | None = None,
) -> str:
    """OCR many images concurrently and return compact per-file text previews."""
    max_files = _clamp_int(max_files, OCR_MAX_FILES_DEFAULT, 1, OCR_MAX_FILES_CAP)
    max_chars_per_image = _clamp_int(max_chars_per_image, OCR_CHARS_PER_IMAGE, 80, OCR_CHARS_PER_IMAGE_CAP)
    worker_count = _clamp_int(max_workers, min(20, MAX_PARALLEL_TOOLS), OCR_WORKER_MIN, MAX_PARALLEL_TOOLS)

    if paths:
        images = []
        for raw in paths[:max_files]:
            p = _resolve_path(raw)
            if p.is_file() and p.suffix.lower() in IMAGE_EXTENSIONS:
                images.append(p)
    else:
        images = _discover_images(directory, pattern)[:max_files]

    if not images:
        return "No image files found. Supported: PNG, JPG, JPEG, WEBP, TIFF, BMP."

    if not paths and directory not in ("", ".", "./"):
        scan_key = str(_resolve_path(directory)) + "::" + pattern
        if scan_key in _scanned_directories:
            file_list = "\n".join(f"  • {_display_path(p)}" for p in images[:10])
            more = f"  … and {len(images) - 10} more" if len(images) > 10 else ""
            return (
                f"[DEDUP — already scanned] All {len(images)} images in "
                f"{directory} were already processed in a previous call.\n"
                f"Use read_image_text('<path>') for individual files if you "
                f"need full text.\n"
                f"Previously scanned files:\n{file_list}{more}"
            )

    total = len(images)
    lock = threading.Lock()
    prog = {"done": 0}

    def ocr_one_tracked(path: pathlib.Path) -> tuple[pathlib.Path, str]:
        from ..repl.turn_progress import report_turn_phase

        text = read_image_text(str(path))
        with lock:
            prog["done"] += 1
            report_turn_phase(f"OCR: {prog['done']}/{total} — {_display_path(path)}")

        return path, text

    rows = []
    with ThreadPoolExecutor(max_workers=min(worker_count, len(images))) as ex:
        for index, (path, text) in enumerate(ex.map(ocr_one_tracked, images)):
            clean = " ".join(text.split())
            if not include_empty and (
                clean == "No text detected in image."
                or clean.startswith("ERROR:")
            ):
                continue
            score = _score_text(clean, keywords)
            if len(clean) > max_chars_per_image:
                clean = clean[:max_chars_per_image].rstrip() + "..."
            label = "LIKELY IMPORTANT" if score else "TEXT"
            rows.append((score, index, f"FILE: {_display_path(path)}\n{label}: {clean}"))

    skipped = len(images) - len(rows)
    rows.sort(key=lambda row: (-row[0], row[1]))
    important = sum(1 for score, _, _ in rows if score > 0)
    header = (
        f"OCR scanned {len(images)} image(s) with {min(worker_count, len(images))} worker(s)."
        + (f" Prioritized {important} likely important result(s)." if important else "")
        + (f" Suppressed {skipped} empty/no-text result(s)." if skipped else "")
    )
    result = header + ("\n\n" + "\n\n".join(row for _, _, row in rows) if rows else "\nNo text detected in scanned images.")

    if not paths and directory not in ("", ".", "./"):
        _scanned_directories.add(str(_resolve_path(directory)) + "::" + pattern)

    return result
