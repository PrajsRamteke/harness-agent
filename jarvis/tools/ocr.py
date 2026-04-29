"""OCR tools backed by macOS Vision framework."""
from concurrent.futures import ThreadPoolExecutor
import pathlib
import subprocess
import threading

from ..constants import (
    CWD, MAX_PARALLEL_TOOLS, OCR_MAX_FILES_DEFAULT, OCR_MAX_FILES_CAP,
    OCR_CHARS_PER_IMAGE, OCR_CHARS_PER_IMAGE_CAP, OCR_SCAN_CHARS, OCR_WORKER_MIN,
)
from ..path_resolve import robust_resolve

IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".heic", ".tif", ".tiff", ".bmp"}
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


def read_image_text(path: str) -> str:
    """Extract all text from an image file using macOS Vision framework (on-device OCR)."""
    p = _resolve_path(path)
    if not p.exists():
        return f"ERROR: {path} not found"
    if not p.is_file():
        return f"ERROR: {path} is not a file"

    swift_code = f'''
import Vision
import Foundation

let url = URL(fileURLWithPath: CommandLine.arguments[1])
let request = VNRecognizeTextRequest {{ req, err in
    guard let obs = req.results as? [VNRecognizedTextObservation] else {{ return }}
    for o in obs {{
        if let top = o.topCandidates(1).first {{
            print(top.string)
        }}
    }}
}}
request.recognitionLevel = .accurate
let handler = VNImageRequestHandler(url: url, options: [:])
try? handler.perform([request])
'''

    result = subprocess.run(
        ["swift", "-e", swift_code, str(p)],
        capture_output=True,
        text=True,
        timeout=30,
    )

    output = result.stdout.strip()
    if not output:
        stderr = result.stderr.strip()
        return f"ERROR: No text found in image. stderr: {stderr}" if stderr else "No text detected in image."
    return output


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
    worker_count = _clamp_int(max_workers, min(20, MAX_PARALLEL_TOOLS), 1, MAX_PARALLEL_TOOLS)

    if paths:
        images = []
        for raw in paths[:max_files]:
            p = _resolve_path(raw)
            if p.is_file() and p.suffix.lower() in IMAGE_EXTENSIONS:
                images.append(p)
    else:
        images = _discover_images(directory, pattern)[:max_files]

    if not images:
        return "No image files found. Supported: PNG, JPG, JPEG, HEIC, TIFF, BMP."

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
                or clean.startswith("ERROR: No text found")
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
    return header + ("\n\n" + "\n\n".join(row for _, _, row in rows) if rows else "\nNo text detected in scanned images.")
