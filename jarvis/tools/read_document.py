"""Read PDF, CSV/TSV, JSON, HTML, XLSX, images (OCR), and text in one tool."""

from __future__ import annotations

import csv
import io
import json
import pathlib

from ..constants import (
    CWD, MAX_FILE_READ,
    DOC_MAX_FILES_DEFAULT, DOC_MAX_FILES_CAP,
    DOC_MAX_CHARS_PER_FILE_DEFAULT, DOC_CSV_MAX_ROWS_DEFAULT,
)
from ..path_resolve import project_scope_error, robust_resolve
from .dirs import SKIP_DIRS
from ..utils.html_clean import _strip_html

# Keep in sync with `ocr.IMAGE_EXTENSIONS` (avoid importing ocr at load time).
_IMAGE_EXTS = frozenset(
    (".png", ".jpg", ".jpeg", ".heic", ".tif", ".tiff", ".bmp", ".gif", ".webp")
)


# File types to include when scanning a directory (plus generic text).
_SCAN_EXTS = frozenset(
    _IMAGE_EXTS
    | {
        ".pdf", ".xlsx", ".csv", ".tsv", ".json", ".html", ".htm", ".xml",
        ".yaml", ".yml", ".md", ".txt", ".rst", ".log", ".ini", ".cfg", ".toml",
        ".py", ".rs", ".go", ".java", ".ts", ".tsx", ".js", ".jsx", ".css",
        ".sh", ".sql", ".swift", ".rb", ".php", ".c", ".h", ".cpp", ".hpp",
    }
)


def _skip_dir_reason(p: pathlib.Path) -> str | None:
    for part in p.parts:
        if part in SKIP_DIRS:
            return part
    return None


def _clamp(n: object, default: int, lo: int, hi: int) -> int:
    try:
        v = int(n)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        v = default
    return max(lo, min(hi, v))


def _truncate(s: str, max_chars: int) -> str:
    s = s.strip()
    if len(s) <= max_chars:
        return s
    return s[: max_chars - 20].rstrip() + "\n… [truncated]"


def _read_pdf(path: pathlib.Path, max_chars: int) -> str:
    try:
        from pypdf import PdfReader
    except ImportError:
        return "ERROR: PDF support requires `pypdf` (pip install pypdf)."
    try:
        reader = PdfReader(str(path))
    except Exception as e:
        return f"ERROR: could not open PDF: {e}"
    parts: list[str] = []
    total = 0
    for i, page in enumerate(reader.pages):
        if total >= max_chars:
            parts.append(f"... [stopped: {max_chars} char cap; page {i + 1}+ omitted]")
            break
        try:
            text = page.extract_text() or ""
        except Exception as e:
            text = f"[page {i + 1} extract error: {e}]"
        parts.append(text)
        total += len(text)
    out = "\n\n".join(parts)
    return _truncate(out, max_chars)


def _read_csvish(path: pathlib.Path, delimiter: str, max_rows: int, max_chars: int) -> str:
    try:
        raw = path.read_bytes()
    except OSError as e:
        return f"ERROR: {e}"
    enc = "utf-8-sig" if delimiter == "," else "utf-8"
    try:
        text = raw.decode(enc)
    except UnicodeDecodeError:
        text = raw.decode("latin-1", errors="replace")
    buf = io.StringIO(text)
    try:
        rows = list(csv.reader(buf, delimiter=delimiter))
    except Exception as e:
        return f"ERROR: CSV parse failed: {e}"
    if not rows:
        return "(empty table)"
    header = rows[0]
    width = max(1, len(header))
    lines = ["\t".join(str(c) for c in header)]
    for row in rows[1 : max_rows + 1]:
        cells = [str(c) if c is not None else "" for c in row]
        while len(cells) < width:
            cells.append("")
        lines.append("\t".join(cells[:width]))
    if len(rows) - 1 > max_rows:
        lines.append(f"... [{len(rows) - 1 - max_rows} more rows omitted; max_rows={max_rows}]")
    return _truncate("\n".join(lines), max_chars)


def _read_json(path: pathlib.Path, max_chars: int) -> str:
    try:
        raw = path.read_text(encoding="utf-8", errors="replace")
    except OSError as e:
        return f"ERROR: {e}"
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as e:
        return f"ERROR: invalid JSON: {e}"
    try:
        pretty = json.dumps(data, ensure_ascii=False, indent=2)
    except Exception as e:
        return f"ERROR: could not serialize JSON: {e}"
    return _truncate(pretty, max_chars)


def _read_html(path: pathlib.Path, max_chars: int) -> str:
    try:
        raw = path.read_text(encoding="utf-8", errors="replace")
    except OSError as e:
        return f"ERROR: {e}"
    return _truncate(_strip_html(raw), max_chars)


def _read_xlsx(path: pathlib.Path, max_chars: int, max_rows: int) -> str:
    try:
        import openpyxl
    except ImportError:
        return "ERROR: Excel support requires `openpyxl` (pip install openpyxl)."
    wb = None
    try:
        wb = openpyxl.load_workbook(path, read_only=True, data_only=True)
        ws = wb.active
        lines: list[str] = []
        n = 0
        for row in ws.iter_rows(values_only=True):
            n += 1
            if n > max_rows:
                lines.append(f"... [{max_rows} row cap; more rows omitted]")
                break
            lines.append("\t".join("" if c is None else str(c) for c in row))
    except Exception as e:
        return f"ERROR: could not read spreadsheet: {e}"
    finally:
        if wb is not None:
            wb.close()
    return _truncate("\n".join(lines), max_chars)


def _read_plain(path: pathlib.Path, max_chars: int) -> str:
    try:
        txt = path.read_text(encoding="utf-8", errors="replace")
    except OSError as e:
        return f"ERROR: {e}"
    return _truncate(txt, max_chars)


def _read_image(path: pathlib.Path, max_chars: int) -> str:
    from .ocr import read_image_text

    out = read_image_text(str(path))
    if out.startswith("ERROR:"):
        return out
    return _truncate(out, max_chars)


def _kind_for_path(p: pathlib.Path) -> str:
    s = p.suffix.lower()
    if s == ".pdf":
        return "pdf"
    if s in (".csv",):
        return "csv"
    if s in (".tsv",):
        return "tsv"
    if s == ".json":
        return "json"
    if s in (".html", ".htm"):
        return "html"
    if s in (".xlsx",):
        return "xlsx"
    if s in _IMAGE_EXTS:
        return "image"
    return "text"


def _read_one(
    path: pathlib.Path,
    max_chars: int,
    csv_max_rows: int,
    force: bool,
) -> str:
    if not path.exists():
        return f"ERROR: not found: {path}"
    if path.is_dir():
        return f"ERROR: is a directory: {path}"
    if not force:
        scope_err = project_scope_error(path, "read_document")
        if scope_err:
            return scope_err

        reason = _skip_dir_reason(path)
        if reason:
            return (
                f"ERROR: path is under blocked dir ({reason}/). "
                f"Pass force=true if the user explicitly asked for this file."
            )
        try:
            if path.stat().st_size > 15_000_000:
                return (
                    f"ERROR: file is {path.stat().st_size} bytes (>15MB). "
                    f"Pass force=true to read anyway."
                )
        except OSError:
            pass

    kind = _kind_for_path(path)
    if kind == "pdf":
        return _read_pdf(path, max_chars)
    if kind == "csv":
        return _read_csvish(path, ",", csv_max_rows, max_chars)
    if kind == "tsv":
        return _read_csvish(path, "\t", csv_max_rows, max_chars)
    if kind == "json":
        return _read_json(path, max_chars)
    if kind == "html":
        return _read_html(path, max_chars)
    if kind == "xlsx":
        return _read_xlsx(path, max_chars, csv_max_rows)
    if kind == "image":
        return _read_image(path, max_chars)
    # text: allow former "binary" code-like extensions that read_file blocks
    return _read_plain(path, max_chars)


def _collect_paths(
    path: str | None,
    paths: list[str] | None,
    directory: str | None,
    pattern: str,
    max_files: int,
    force: bool,
) -> tuple[list[pathlib.Path], str | None]:
    """Returns (files, error_message)."""
    out: list[pathlib.Path] = []
    if path and str(path).strip():
        out.append(robust_resolve(str(path).strip(), CWD))
        return out, None
    if paths:
        for raw in paths[:max_files]:
            if not raw or not str(raw).strip():
                continue
            out.append(robust_resolve(str(raw).strip(), CWD))
        if not out:
            return [], "ERROR: paths was empty or invalid."
        return out, None
    if directory is not None and str(directory).strip() != "":
        root = robust_resolve(str(directory).strip(), CWD)
        if not root.exists():
            return [], f"ERROR: directory not found: {directory}"
        if not root.is_dir():
            return [], f"ERROR: not a directory: {directory}"
        scope_err = None if force else project_scope_error(root, "read_document")
        if scope_err:
            return [], scope_err
        pat = pattern or "**/*"
        seen: list[pathlib.Path] = []
        for p in sorted(root.glob(pat)):
            if not p.is_file():
                continue
            s = p.suffix.lower()
            if s in _SCAN_EXTS:
                seen.append(p)
        return seen[:max_files], None
    return (
        [],
        "ERROR: pass `path` (one file), `paths` (list), or `directory` (with optional pattern) to read documents.",
    )


def read_document(
    path: str | None = None,
    paths: list[str] | None = None,
    directory: str | None = None,
    pattern: str = "**/*",
    max_files: int = DOC_MAX_FILES_DEFAULT,
    max_chars_per_file: int = DOC_MAX_CHARS_PER_FILE_DEFAULT,
    force: bool = False,
    csv_max_rows: int = DOC_CSV_MAX_ROWS_DEFAULT,
) -> str:
    """Read PDF, images (OCR), CSV/TSV, JSON, HTML, XLSX, or text; single or bulk.

    Prefer this over `read_file` for non-plain-text office/data/media files.
    """
    max_files = _clamp(max_files, DOC_MAX_FILES_DEFAULT, 1, DOC_MAX_FILES_CAP)
    max_chars = _clamp(max_chars_per_file, DOC_MAX_CHARS_PER_FILE_DEFAULT, 2_000, min(MAX_FILE_READ, 200_000))
    csv_max_rows = _clamp(csv_max_rows, DOC_CSV_MAX_ROWS_DEFAULT, 10, 5_000)

    collected, err = _collect_paths(path, paths, directory, pattern, max_files, force)
    if err:
        return err
    if not collected:
        return "ERROR: no files matched."

    parts: list[str] = []
    for p in collected:
        rel = str(p)
        try:
            rel = str(p.relative_to(CWD))
        except ValueError:
            pass
        kind = _kind_for_path(p)
        body = _read_one(p, max_chars, csv_max_rows, force)
        parts.append(f"==== FILE: {rel} ({kind}) ====\n{body}")

    return "\n\n".join(parts)
