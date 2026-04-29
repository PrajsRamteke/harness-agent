"""Directory listing, glob, and lightweight file ranking."""
import os, pathlib, re, shutil, subprocess
from ..constants import CWD, OCR_SCAN_CHARS
from ..path_resolve import project_scope_error, robust_resolve

SKIP_DIRS = {
    ".git", ".hg", ".svn", "node_modules", ".venv", "venv", "__pycache__",
    ".pytest_cache", ".mypy_cache", ".ruff_cache", "dist", "build", ".next",
}
TEXT_EXTS = {
    ".py", ".js", ".jsx", ".ts", ".tsx", ".json", ".md", ".txt", ".toml",
    ".yaml", ".yml", ".css", ".scss", ".html", ".sh", ".sql", ".prisma",
    ".env", ".ini", ".cfg", ".rs", ".go", ".java", ".kt", ".swift",
}
DOC_EXTS = {".pdf", ".doc", ".docx", ".rtf"}
IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".heic", ".webp", ".tif", ".tiff", ".bmp"}


def list_dir(path: str = ".", show_all: bool = False, allow_outside_project: bool = False) -> str:
    p = robust_resolve(path)
    if not p.exists(): return f"ERROR: {path} not found"
    if not allow_outside_project:
        scope_err = project_scope_error(p, "list_dir", "allow_outside_project=true")
        if scope_err:
            return scope_err
    items = sorted(p.iterdir(), key=lambda x: (not x.is_dir(), x.name.lower()))
    if not show_all:
        items = [x for x in items if x.name not in SKIP_DIRS]
    hidden = 0
    if not show_all:
        hidden = sum(1 for x in p.iterdir() if x.name in SKIP_DIRS)
    items = items[:300]
    lines = [f"{'d' if x.is_dir() else 'f'} {x.name}" for x in items]
    if hidden:
        lines.append(f"… ({hidden} entries hidden: node_modules/build/caches — pass show_all=true to include)")
    return "\n".join(lines)


def glob_files(
    pattern: str,
    path: str = ".",
    max_results: int = 200,
    allow_outside_project: bool = False,
) -> str:
    root = robust_resolve(path)
    if not root.exists():
        return f"ERROR: {path} not found"
    if not root.is_dir():
        return f"ERROR: {path} is not a directory"
    if not allow_outside_project:
        scope_err = project_scope_error(root, "glob_files", "allow_outside_project=true")
        if scope_err:
            return scope_err
    try:
        max_results = max(1, min(1000, int(max_results)))
    except (TypeError, ValueError):
        max_results = 200

    pat = (pattern or "").strip()
    if not pat:
        return "ERROR: pattern is required"
    if pathlib.Path(pat).is_absolute():
        return "ERROR: pass absolute search roots via `path` and keep `pattern` relative."

    matches = []
    for p in sorted(root.glob(pat)):
        if len(matches) >= max_results:
            break
        if not p.is_file() or (not allow_outside_project and _is_skipped(p)):
            continue
        try:
            label = str(p.relative_to(CWD))
        except ValueError:
            try:
                label = str(p.relative_to(root))
            except ValueError:
                label = str(p)
        matches.append(label)

    if not matches:
        try:
            searched = str(root.relative_to(CWD)) or "."
        except ValueError:
            searched = str(root)
        return f"no matches under {searched} for {pat}"
    return "\n".join(matches)


def fast_find(
    query: str,
    path: str = "",
    kind: str = "any",
    max_results: int = 50,
    ext: str = "",
) -> str:
    """Fast file/folder search across the Mac using Spotlight (mdfind) or fd.

    - query: filename/substring to search for (e.g. 'harness', 'resume.pdf', 'qr').
    - path: optional folder to scope the search (e.g. '~/Desktop'). Empty = whole Mac.
    - kind: 'any' | 'file' | 'folder'.
    - max_results: cap on returned entries (default 50, max 500).
    - ext: optional extension filter, e.g. '.png' or 'png,jpg' — applied after the
      index query (so 'qr' + ext='png' finds all QR png files in milliseconds).
    """
    try:
        max_results = max(1, min(500, int(max_results)))
    except (TypeError, ValueError):
        max_results = 50

    q = (query or "").strip()
    if not q:
        return "ERROR: query is required"

    scope = os.path.expanduser(path).strip() if path else ""
    if scope and not os.path.isabs(scope):
        scope = str((CWD / scope).resolve())
    if scope and not os.path.isdir(scope):
        return f"ERROR: scope not found: {path}"

    # Normalise extension filter into a set like {'.png', '.jpg'}.
    exts: set[str] = set()
    for piece in (ext or "").replace(" ", "").split(","):
        if not piece:
            continue
        exts.add(piece if piece.startswith(".") else f".{piece.lower()}")

    def _ext_ok(line: str) -> bool:
        if not exts:
            return True
        return os.path.splitext(line)[1].lower() in exts

    results: list[str] = []

    # 1) Spotlight via mdfind (instant, indexed) — macOS
    if shutil.which("mdfind"):
        cmd = ["mdfind", "-name", q]
        if scope:
            cmd += ["-onlyin", scope]
        try:
            out = subprocess.run(cmd, capture_output=True, text=True, timeout=10)
            for line in out.stdout.splitlines():
                if not line:
                    continue
                if kind == "file" and not os.path.isfile(line):
                    continue
                if kind == "folder" and not os.path.isdir(line):
                    continue
                if not _ext_ok(line):
                    continue
                results.append(line)
                if len(results) >= max_results:
                    break
        except Exception:
            pass

    # 2) Fallback to fd if nothing found and fd is installed
    if not results and shutil.which("fd"):
        cmd = ["fd", "--hidden", "--no-ignore", q]
        if kind == "file":
            cmd += ["-t", "f"]
        elif kind == "folder":
            cmd += ["-t", "d"]
        for e in exts:
            cmd += ["-e", e.lstrip(".")]
        if scope:
            cmd.append(scope)
        try:
            out = subprocess.run(cmd, capture_output=True, text=True, timeout=15)
            results = [l for l in out.stdout.splitlines() if l][:max_results]
        except Exception:
            pass

    if not results:
        where = scope or "whole Mac"
        extra = f" (ext filter: {sorted(exts)})" if exts else ""
        return f"No matches for '{q}'{extra} in {where}"
    header = f"Found {len(results)} match(es) for '{q}'" + (f" in {scope}" if scope else "")
    return header + "\n" + "\n".join(results)


def _resolve(path: str) -> pathlib.Path:
    return robust_resolve(path)


def _terms(query: str) -> list[str]:
    return [t.lower() for t in re.findall(r"[a-zA-Z0-9_./-]{2,}", query or "")]


def _is_skipped(path: pathlib.Path) -> bool:
    return any(part in SKIP_DIRS for part in path.parts)


def _safe_rel(path: pathlib.Path) -> str:
    try:
        return str(path.relative_to(CWD))
    except ValueError:
        return str(path)


def _snippet(path: pathlib.Path, terms: list[str], max_chars: int) -> str:
    if path.suffix.lower() not in TEXT_EXTS and path.name not in {".env", "Dockerfile"}:
        return ""
    try:
        text = path.read_text(errors="ignore")[:12000]
    except Exception:
        return ""
    lower = text.lower()
    hit = min((lower.find(t) for t in terms if t in lower), default=-1)
    if hit < 0:
        hit = 0
    start = max(0, hit - max_chars // 3)
    return " ".join(text[start:start + max_chars].split())


def rank_files(
    query: str,
    path: str = ".",
    pattern: str = "**/*",
    max_files: int = 30,
    scan_limit: int = 700,
    include_snippets: bool = False,
    max_snippet_chars: int = 240,
    allow_outside_project: bool = False,
) -> str:
    """Rank likely relevant files before expensive reads/OCR/PDF extraction."""
    root = _resolve(path)
    if not root.exists():
        return f"ERROR: {path} not found"
    if not allow_outside_project:
        scope_err = project_scope_error(root, "rank_files", "allow_outside_project=true")
        if scope_err:
            return scope_err
    try:
        max_files = max(1, min(100, int(max_files)))
        scan_limit = max(20, min(3000, int(scan_limit)))
        max_snippet_chars = max(80, min(800, int(max_snippet_chars)))
    except (TypeError, ValueError):
        max_files, scan_limit, max_snippet_chars = 30, 700, 240

    terms = _terms(query)
    if root.is_file():
        candidates = [root]
    else:
        candidates = []
        for p in root.glob(pattern):
            if len(candidates) >= scan_limit:
                break
            if p.is_file() and not _is_skipped(p):
                candidates.append(p)

    ranked = []
    for p in candidates:
        rel = _safe_rel(p)
        hay = rel.lower()
        score = 0
        reasons = []
        for term in terms:
            if term in hay:
                score += 8
                reasons.append(f"name:{term}")
        ext = p.suffix.lower()
        if ext in TEXT_EXTS:
            score += 2
        elif ext in DOC_EXTS:
            score += 3
            if any(t in {"resume", "cv", "candidate", "hr", "job"} for t in terms):
                score += 8
                reasons.append("doc")
        elif ext in IMAGE_EXTS:
            score += 2
            if any(t in {"screenshot", "image", "photo", "id", "license", "voter"} for t in terms):
                score += 8
                reasons.append("image")
        if any(part.lower() in {"test", "tests", "spec", "__tests__"} for part in p.parts):
            if any(t in {"test", "tests", "spec"} for t in terms):
                score += 6
            else:
                score -= 1
        try:
            size = p.stat().st_size
            if size > 1_000_000 and ext not in DOC_EXTS | IMAGE_EXTS:
                score -= 4
            mtime = p.stat().st_mtime
        except OSError:
            size, mtime = 0, 0
        if include_snippets and terms:
            snip = _snippet(p, terms, max_snippet_chars)
            snip_lower = snip.lower()
            text_hits = [t for t in terms if t in snip_lower]
            if text_hits:
                score += 5 * len(text_hits)
                reasons.extend(f"text:{t}" for t in text_hits[:3])
        else:
            snip = ""
        if score > 0 or not terms:
            ranked.append((score, mtime, rel, size, reasons[:4], snip))

    ranked.sort(key=lambda row: (-row[0], -row[1], row[2]))
    if not ranked:
        return f"No likely file matches in {path} for: {query}"

    lines = [f"Ranked {min(len(ranked), max_files)} of {len(candidates)} scanned file(s) for: {query}"]
    for score, _, rel, size, reasons, snip in ranked[:max_files]:
        reason = f" ({', '.join(reasons)})" if reasons else ""
        lines.append(f"{score:>3}  {rel}  {size} bytes{reason}")
        if snip:
            lines.append(f"     snippet: {snip}")
    return "\n".join(lines)
