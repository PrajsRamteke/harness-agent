"""File tools: read_file, write_file, edit_file."""
import ast
import difflib
import json
import pathlib
import re
import threading
import warnings
from collections import OrderedDict
from contextlib import contextmanager

from ..constants import (
    CWD, MAX_FILE_READ, MAX_FILE_SIZE_BYTES, MAX_FILE_CHUNK_BYTES,
    READ_MAX_LINES, READ_MAX_CHARS, READ_LINE_MAX_CHARS,
)
from .. import state
from .dirs import SKIP_DIRS
from ..path_resolve import project_scope_error, robust_resolve

# Extensions that are almost never useful to read as text.
_BINARY_EXTS = {
    ".png", ".jpg", ".jpeg", ".gif", ".bmp", ".tiff", ".tif", ".webp", ".heic",
    ".ico", ".icns", ".pdf", ".zip", ".tar", ".gz", ".tgz", ".bz2", ".xz",
    ".7z", ".rar", ".jar", ".war", ".class", ".exe", ".dll", ".so", ".dylib",
    ".o", ".a", ".obj", ".bin", ".dat", ".db", ".sqlite", ".sqlite3",
    ".pyc", ".pyo", ".pyd", ".whl", ".egg",
    ".mp3", ".mp4", ".mov", ".avi", ".mkv", ".wav", ".flac", ".ogg",
    ".ttf", ".otf", ".woff", ".woff2", ".eot",
    ".lock",  # yarn.lock / package-lock.json noise — usually not useful
}

# Known text/code extensions — skip the extra 4KB binary sniff on read.
_TEXT_EXTS = {
    ".py", ".pyi", ".js", ".jsx", ".ts", ".tsx", ".mjs", ".cjs",
    ".json", ".yaml", ".yml", ".toml", ".ini", ".cfg", ".conf",
    ".md", ".mdx", ".rst", ".txt", ".log", ".csv", ".tsv",
    ".html", ".htm", ".xml", ".css", ".scss", ".sass", ".less",
    ".sh", ".bash", ".zsh", ".fish", ".ps1", ".bat", ".cmd",
    ".sql", ".go", ".rs", ".java", ".kt", ".swift", ".rb", ".php",
    ".c", ".h", ".cpp", ".hpp", ".cc", ".cs", ".vue", ".svelte",
    ".dockerfile", ".env", ".gitignore", ".editorconfig",
}

_READ_CACHE_MAX = 96
_read_cache: "OrderedDict[str, tuple[float, int, str]]" = OrderedDict()
_path_locks: dict[str, threading.Lock] = {}
_path_locks_guard = threading.Lock()
_BACKUP_MAX_BYTES = 1_000_000


@contextmanager
def _path_lock(p: pathlib.Path):
    """Serialize read/write/edit on the same resolved path; different paths run in parallel."""
    key = str(p.resolve())
    with _path_locks_guard:
        lk = _path_locks.setdefault(key, threading.Lock())
    with lk:
        yield


def _cache_get(p: pathlib.Path) -> str | None:
    key = str(p.resolve())
    try:
        st = p.stat()
    except OSError:
        return None
    hit = _read_cache.get(key)
    if hit and hit[0] == st.st_mtime and hit[1] == st.st_size:
        _read_cache.move_to_end(key)
        return hit[2]
    return None


def _cache_put(p: pathlib.Path, content: str) -> None:
    key = str(p.resolve())
    try:
        st = p.stat()
    except OSError:
        return
    _read_cache[key] = (st.st_mtime, st.st_size, content)
    _read_cache.move_to_end(key)
    while len(_read_cache) > _READ_CACHE_MAX:
        _read_cache.popitem(last=False)


def _cache_invalidate(p: pathlib.Path) -> None:
    _read_cache.pop(str(p.resolve()), None)


# What the model last saw of each file: (mtime_ns, size) right after its last
# read_file / write / edit. A file that differs now was changed by someone else
# (the user, a formatter, a shell command) since the model looked at it.
_seen: dict[str, tuple[int, int]] = {}


def _stat_key(p: pathlib.Path) -> tuple[int, int] | None:
    try:
        st = p.stat()
    except OSError:
        return None
    return (st.st_mtime_ns, st.st_size)


def _remember_seen(p: pathlib.Path) -> None:
    key = _stat_key(p)
    if key is not None:
        _seen[str(p.resolve())] = key


def _changed_since_seen(p: pathlib.Path) -> bool:
    """True only when the model saw this file before and it differs now."""
    seen = _seen.get(str(p.resolve()))
    return seen is not None and _stat_key(p) != seen


_warn_lock = threading.Lock()


def _syntax_problem(p: pathlib.Path, text: str) -> str | None:
    """A one-line parse error for Python / JSON / TOML / YAML text, else None."""
    ext = p.suffix.lower()
    try:
        if ext in (".py", ".pyi"):
            with _warn_lock, warnings.catch_warnings():
                warnings.simplefilter("ignore")
                ast.parse(text, filename=p.name)
        elif ext == ".json":
            if text.strip():
                json.loads(text)
        elif ext == ".toml":
            try:
                import tomllib
            except ImportError:
                return None
            tomllib.loads(text)
        elif ext in (".yaml", ".yml"):
            try:
                import yaml
            except ImportError:
                return None
            list(yaml.safe_load_all(text))
        else:
            return None
    except SyntaxError as e:
        where = f" at line {e.lineno}" if e.lineno else ""
        bad = (e.text or "").strip()
        return f"SyntaxError{where}: {e.msg}" + (f" → {bad[:120]}" if bad else "")
    except json.JSONDecodeError as e:
        return f"invalid JSON at line {e.lineno} col {e.colno}: {e.msg}"
    except Exception as e:  # tomllib / yaml errors
        return f"{type(e).__name__}: {str(e).splitlines()[0][:200] if str(e) else 'parse error'}"
    return None


def _syntax_note(p: pathlib.Path, before: str | None, after: str) -> str:
    """Warn when a write/edit leaves a file unparseable that parsed before
    (a new file counts as parsing before). Files already broken stay quiet."""
    try:
        if before is not None and _syntax_problem(p, before):
            return ""
        problem = _syntax_problem(p, after)
    except Exception:
        return ""
    if not problem:
        return ""
    return f"\n[warning: {p.name} no longer parses — {problem}. Fix this before moving on.]"


def _emit_diff(path: str, before: str, after: str, *, action: str) -> None:
    """Render a live diff for a write/edit. Lazy import avoids a tools→repl cycle."""
    try:
        from ..repl.file_diffs import emit_file_diff
        emit_file_diff(path, before, after, action=action)
    except Exception:
        pass


def _save_backup(p: pathlib.Path):
    if not p.exists():
        return
    try:
        if p.stat().st_size > _BACKUP_MAX_BYTES:
            return
        state.backups.append((str(p), p.read_text(errors="ignore")))
    except OSError:
        pass


def _in_skip_dir(p: pathlib.Path) -> str | None:
    """Return the offending skip-dir name if p is inside one, else None."""
    for part in p.parts:
        if part in SKIP_DIRS:
            return part
    return None


def _looks_binary(p: pathlib.Path) -> bool:
    """Cheap binary sniff: read up to 4KB and look for NUL bytes or very
    low printable-ratio."""
    try:
        with p.open("rb") as fh:
            chunk = fh.read(MAX_FILE_CHUNK_BYTES)
    except Exception:
        return False
    if not chunk:
        return False
    if b"\x00" in chunk:
        return True
    # count printable bytes (tab, newline, carriage-return, 0x20-0x7e + utf-8 high bits)
    printable = sum(1 for b in chunk if b in (9, 10, 13) or 32 <= b <= 126 or b >= 128)
    return (printable / len(chunk)) < 0.85


def _read_lines_slice(p: pathlib.Path, offset: int, limit: int) -> str:
    """Read only the requested line range — avoids loading huge files whole."""
    end = offset + limit if limit else None
    out: list[str] = []
    with p.open("r", encoding="utf-8", errors="ignore") as fh:
        for i, line in enumerate(fh):
            if i < offset:
                continue
            if end is not None and i >= end:
                break
            out.append(f"{i + 1}\t{line.rstrip(chr(10) + chr(13))}")
    return "\n".join(out)


def read_file(path: str, offset: int = 0, limit: int = 0, force: bool = False) -> str:
    p = robust_resolve(path)
    if not p.exists():
        return f"ERROR: {path} not found"
    if p.is_dir():
        return f"ERROR: {path} is a directory"

    with _path_lock(p):
        try:
            st = p.stat()
        except OSError:
            st = None

        if not force:
            scope_err = project_scope_error(p, "read_file")
            if scope_err:
                return scope_err

            skip = _in_skip_dir(p)
            if skip:
                return (
                    f"ERROR: refused to read '{path}' — inside '{skip}/' "
                    f"(node_modules, build artifacts, caches are blocked). "
                    f"Pass force=true only if the user explicitly asked for this file."
                )

            if p.suffix.lower() in _BINARY_EXTS:
                return (
                    f"ERROR: refused to read '{path}' — binary/non-text extension "
                    f"'{p.suffix}'. Use `read_document` for PDF, images, CSV, "
                    f"Excel, JSON, etc., or pass force=true if the user explicitly asked."
                )

            if st and st.st_size > MAX_FILE_SIZE_BYTES:
                if not (offset or limit):
                    return (
                        f"ERROR: '{path}' is {st.st_size} bytes "
                        f"(>{MAX_FILE_SIZE_BYTES:,} bytes). "
                        f"Use offset/limit to page through it, or pass force=true."
                    )

            if p.suffix.lower() not in _TEXT_EXTS and _looks_binary(p):
                return (
                    f"ERROR: '{path}' appears to be a binary file. "
                    f"Pass force=true only if you are sure it is text."
                )

        if offset or limit:
            return _read_lines_slice(p, offset, limit)

        cached = _cache_get(p)
        if cached is not None:
            return cached[:MAX_FILE_READ]

        txt = p.read_text(errors="ignore")
        _cache_put(p, txt)
        return txt[:MAX_FILE_READ]


def _as_int(value, default: int = 0) -> int:
    try:
        return max(0, int(value or 0))
    except (TypeError, ValueError):
        return default


def _file_lines(text: str) -> list[str]:
    """Lines as an editor numbers them (split on \\n only, CR dropped)."""
    if not text:
        return []
    lines = text.split("\n")
    if lines[-1] == "":
        lines.pop()
    return [ln[:-1] if ln.endswith("\r") else ln for ln in lines]


def _clip_line(line: str) -> str:
    if len(line) > READ_LINE_MAX_CHARS:
        return line[:READ_LINE_MAX_CHARS] + f" … [line truncated: {len(line):,} chars]"
    return line


def read_file_tool(path: str, offset: int = 0, limit: int = 0, force: bool = False) -> str:
    """``read_file`` as the model calls it: numbered lines (``N<TAB>text``),
    bounded to READ_MAX_LINES / READ_MAX_CHARS, with a closing note saying
    where to continue whenever it stops before the end. ``read_file`` itself
    stays the raw reader other code relies on."""
    offset, limit = _as_int(offset), _as_int(limit)
    res = read_file(path, offset=offset, limit=limit, force=force)
    if res.startswith("ERROR"):
        return res
    p = robust_resolve(path)
    try:
        size = p.stat().st_size
    except OSError:
        size = 0

    if size > MAX_FILE_SIZE_BYTES and (offset or limit):
        # Huge file paged with offset/limit: read_file streamed just that
        # slice (already numbered) — bound it, total line count unknown.
        rows = res.split("\n") if res else []
        total = None
        start = offset
        numbered = [r if len(r) <= READ_LINE_MAX_CHARS else _clip_line(r) for r in rows]
        wanted = len(rows)
    else:
        text = _cache_get(p)
        if text is None:
            try:
                text = p.read_text(errors="ignore")
            except OSError as e:
                return f"ERROR: {path}: {e}"
            _cache_put(p, text)
        lines = _file_lines(text)
        total = len(lines)
        start = offset
        if total == 0:
            _remember_seen(p)
            return "[empty file]"
        if start >= total:
            return f"[offset {offset} is past the end — {path} has {total} lines]"
        end = min(total, start + limit) if limit else total
        numbered = [f"{i + 1}\t{_clip_line(lines[i])}" for i in range(start, end)]
        wanted = end - start

    out: list[str] = []
    used = 0
    for row in numbered:
        if len(out) >= READ_MAX_LINES or (out and used + len(row) + 1 > READ_MAX_CHARS):
            break
        out.append(row)
        used += len(row) + 1
    _remember_seen(p)
    if len(out) < wanted:
        shown_end = start + len(out)
        of_total = f" of {total}" if total is not None else ""
        out.append(
            f"[showing lines {start + 1}–{shown_end}{of_total} — call read_file "
            f"with offset={shown_end} to continue]"
        )
    return "\n".join(out)


def write_file(path: str, content: str, allow_outside_project: bool = False) -> str:
    p = robust_resolve(path)
    if not allow_outside_project:
        scope_err = project_scope_error(p, "write_file", "allow_outside_project=true")
        if scope_err:
            return scope_err
    with _path_lock(p):
        existed = p.exists()
        if existed and _changed_since_seen(p):
            return (
                f"ERROR: {path} changed on disk since you last read or wrote it "
                "(edited by the user, a formatter or a command). Read it again "
                "before overwriting it, so those changes aren't lost."
            )
        before = p.read_text(errors="ignore") if existed else ""
        _save_backup(p)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content)
        _cache_invalidate(p)
        _remember_seen(p)
    _emit_diff(str(p), before, content, action="write" if existed else "create")
    return f"WROTE {p} ({len(content)} bytes)" + _syntax_note(p, before if existed else None, content)


# Invisible characters that commonly differ between model output and file
# content (non-breaking / narrow spaces pasted from rendered text).
_INVISIBLE_SPACE_CHARS = ("\u00a0", "\u202f", "\u2007")  # NBSP, narrow NBSP, figure space


def _norm_match_line(line: str) -> str:
    for ch in _INVISIBLE_SPACE_CHARS:
        line = line.replace(ch, " ")
    return line.rstrip()


def _whitespace_tolerant_spans(txt: str, old_str: str) -> list[tuple[int, int]]:
    """Find old_str in txt comparing line-by-line, ignoring trailing
    whitespace, CRLF vs LF, and non-breaking-space differences.

    Returns non-overlapping (start, end) character spans covering the exact
    file text that corresponds to old_str, so replacement preserves the rest
    of the file byte-for-byte.
    """
    old_lines = old_str.splitlines()
    if not old_lines:
        return []
    file_lines = txt.splitlines(keepends=True)
    old_norm = [_norm_match_line(line) for line in old_lines]
    file_norm = [_norm_match_line(line) for line in file_lines]
    n = len(old_norm)

    offsets: list[int] = []
    pos = 0
    for line in file_lines:
        offsets.append(pos)
        pos += len(line)

    spans: list[tuple[int, int]] = []
    i = 0
    while i <= len(file_lines) - n:
        if file_norm[i:i + n] == old_norm:
            start = offsets[i]
            last_line = file_lines[i + n - 1]
            end = offsets[i + n - 1] + len(last_line)
            # old_str without a trailing newline must not consume the file's
            if not old_str.endswith(("\n", "\r")):
                end -= len(last_line) - len(last_line.rstrip("\r\n"))
            spans.append((start, end))
            i += n
        else:
            i += 1
    return spans


_LINE_NO_RE = re.compile(r"^ *(\d+)(?:\t|→)")


def _looks_line_numbered(text: str) -> bool:
    """Every line starts with read_file's ``N<TAB>`` prefix (or ``N→``), numbered
    consecutively — i.e. the model pasted read_file output, prefixes and all."""
    lines = _file_lines(text)
    if not lines:
        return False
    prev = None
    for line in lines:
        m = _LINE_NO_RE.match(line)
        if not m:
            return False
        n = int(m.group(1))
        if prev is not None and n != prev + 1:
            return False
        prev = n
    return True


def _strip_line_numbers(text: str) -> str:
    out = [_LINE_NO_RE.sub("", line, count=1) for line in text.split("\n")]
    return "\n".join(out)


def _leading_ws(line: str) -> str:
    return line[: len(line) - len(line.lstrip(" \t"))]


def _indent_shift_edit(txt: str, old_str: str, new_str: str) -> tuple[str | None, str | None]:
    """Match old_str ignoring a consistent indentation shift (the model added
    or dropped the same leading whitespace on every line) and re-indent
    new_str by that same shift. Returns (new_text, error) — (None, None) when
    there is no such match. Only a single, unambiguous match is applied."""
    old_lines = old_str.split("\n")
    trailing_nl = old_lines[-1] == ""
    if trailing_nl:
        old_lines.pop()
    keys = [_norm_match_line(ln).strip() for ln in old_lines]
    if not any(keys):
        return None, None
    file_lines = txt.splitlines(keepends=True)
    file_keys = [_norm_match_line(ln.rstrip("\r\n")).strip() for ln in file_lines]
    n = len(keys)
    hits = [i for i in range(len(file_lines) - n + 1) if file_keys[i:i + n] == keys]
    if not hits:
        return None, None
    if len(hits) > 1:
        return None, (
            f"old_str not found exactly; ignoring indentation it matches "
            f"{len(hits)} locations — add more context"
        )
    i = hits[0]
    window = [ln.rstrip("\r\n") for ln in file_lines[i:i + n]]
    pairs = [(_leading_ws(o), _leading_ws(f)) for o, f, k in zip(old_lines, window, keys) if k]
    o0, f0 = pairs[0]
    if f0.endswith(o0) and len(f0) > len(o0):
        mode, delta = "add", f0[: len(f0) - len(o0)]
    elif o0.endswith(f0) and len(o0) > len(f0):
        mode, delta = "remove", o0[: len(o0) - len(f0)]
    else:
        return None, None
    for o, f in pairs:
        if (mode == "add" and f != delta + o) or (mode == "remove" and o != delta + f):
            return None, None
    shifted: list[str] = []
    for line in new_str.split("\n"):
        if not line.strip():
            shifted.append(line)
        elif mode == "add":
            shifted.append(delta + line)
        elif line.startswith(delta):
            shifted.append(line[len(delta):])
        else:
            return None, None
    new_block = "\n".join(shifted)
    start = sum(len(ln) for ln in file_lines[:i])
    last = file_lines[i + n - 1]
    end = start + sum(len(ln) for ln in file_lines[i:i + n])
    if not trailing_nl:
        end -= len(last) - len(last.rstrip("\r\n"))
    return txt[:start] + new_block + txt[end:], None


def _closest_match_hint(txt: str, old_str: str) -> str:
    """Show the region of the file most like old_str, numbered, so the model
    can fix its old_str without re-reading the whole file."""
    try:
        if len(txt) > 2_000_000 or not old_str.strip():
            return ""
        file_lines = _file_lines(txt)
        old_lines = _file_lines(old_str) or [old_str]
        n = len(old_lines)
        stripped = [ln.strip() for ln in file_lines]
        anchors = sorted(
            ((k, ln.strip()) for k, ln in enumerate(old_lines) if len(ln.strip()) >= 4),
            key=lambda kv: -len(kv[1]),
        )[:5]
        starts: set[int] = set()
        for k, a in anchors:
            for i, fl in enumerate(stripped):
                if fl == a:
                    starts.add(i - k)
        if not starts and anchors:
            k, a = anchors[0]
            for m in difflib.get_close_matches(a, stripped, n=3, cutoff=0.6):
                for i, fl in enumerate(stripped):
                    if fl == m:
                        starts.add(i - k)
        best, best_ratio = None, 0.0
        for c in sorted(starts)[:40]:
            c = max(0, c)
            window = "\n".join(file_lines[c:c + n])
            r = difflib.SequenceMatcher(None, window, old_str, autojunk=False).ratio()
            if r > best_ratio:
                best, best_ratio = c, r
        if best is None or best_ratio < 0.5:
            return ""
        lo, hi = max(0, best - 2), min(len(file_lines), best + n + 2)
        hi = min(hi, lo + 40)
        snippet = "\n".join(f"{j + 1}\t{_clip_line(file_lines[j])}" for j in range(lo, hi))
        return (
            f"\nClosest match (lines {best + 1}–{min(best + n, len(file_lines))}, "
            f"{int(best_ratio * 100)}% similar) — copy old_str from here, without "
            f"the line-number prefixes:\n{snippet}"
        )
    except Exception:
        return ""


def _apply_text_edit(
    txt: str,
    old_str: str,
    new_str: str,
    replace_all: bool = False,
    _numbers_stripped: bool = False,
) -> tuple[str | None, str | None, int, str | None]:
    """Return (new_text, error_message, match_count, note).

    Matching is exact first; if that fails, falls back to (in order) a
    whitespace-tolerant line match (trailing whitespace / CRLF / non-breaking
    spaces), old_str pasted with read_file's line-number prefixes, and a
    consistent indentation shift — each reported via *note*. When nothing
    matches, the error carries the closest region of the file.
    """
    if not old_str:
        return None, "old_str is empty — provide the exact text to replace", 0, None
    if old_str == new_str:
        return None, "old_str and new_str are identical — nothing would change", 0, None
    n = txt.count(old_str)
    if n == 0:
        spans = _whitespace_tolerant_spans(txt, old_str)
        if not spans:
            if not _numbers_stripped and _looks_line_numbered(old_str):
                plain_new = _strip_line_numbers(new_str) if _looks_line_numbered(new_str) else new_str
                res = _apply_text_edit(
                    txt, _strip_line_numbers(old_str), plain_new, replace_all, _numbers_stripped=True,
                )
                if res[1] is None:
                    note = "removed read_file line-number prefixes from old_str"
                    if res[3]:
                        note += "; " + res[3]
                    return res[0], None, res[2], note
                return res
            shifted, shift_err = _indent_shift_edit(txt, old_str, new_str)
            if shifted is not None:
                return (
                    shifted, None, 1,
                    "matched with a consistent indentation shift — new_str was "
                    "re-indented to match the file",
                )
            if shift_err:
                return None, shift_err, 0, None
            return (
                None,
                "old_str not found — re-read the file and copy the exact text "
                "(check indentation, tabs vs spaces, and line endings)"
                + _closest_match_hint(txt, old_str),
                0,
                None,
            )
        if len(spans) > 1 and not replace_all:
            return (
                None,
                f"old_str not found exactly; a whitespace-tolerant match hits "
                f"{len(spans)} locations — add more context or pass replace_all=true",
                len(spans),
                None,
            )
        targets = spans if replace_all else spans[:1]
        parts: list[str] = []
        prev = 0
        for start, end in targets:
            parts.append(txt[prev:start])
            parts.append(new_str)
            prev = end
        parts.append(txt[prev:])
        note = (
            "matched via whitespace-tolerant fallback — old_str differed in "
            "trailing whitespace or line endings"
        )
        return "".join(parts), None, len(targets), note
    if n > 1 and not replace_all:
        return (
            None,
            f"old_str matches {n} times; pass replace_all=true or add more context",
            n,
            None,
        )
    new_txt = txt.replace(old_str, new_str) if replace_all else txt.replace(old_str, new_str, 1)
    replacements = n if replace_all else 1
    return new_txt, None, replacements, None


def edit_file(
    path: str,
    old_str: str,
    new_str: str,
    replace_all: bool = False,
    allow_outside_project: bool = False,
) -> str:
    p = robust_resolve(path)
    if not allow_outside_project:
        scope_err = project_scope_error(p, "edit_file", "allow_outside_project=true")
        if scope_err:
            return scope_err
    with _path_lock(p):
        if not p.exists():
            return f"ERROR: {path} not found"
        stale = _changed_since_seen(p)
        txt = p.read_text(errors="ignore")
        new_txt, err, n, note = _apply_text_edit(txt, old_str, new_str, replace_all)
        if err:
            return f"ERROR: {err}"
        _save_backup(p)
        p.write_text(new_txt)
        _cache_invalidate(p)
        _remember_seen(p)
    _emit_diff(str(p), txt, new_txt, action="edit")
    msg = f"EDITED {p} ({n} replacement{'s' if n > 1 else ''})"
    if note:
        msg += f"\n[{note}]"
    if stale:
        msg += (
            "\n[note: the file had changed on disk since you last read it — "
            "re-read it if later edits depend on the parts that changed]"
        )
    return msg + _syntax_note(p, txt, new_txt)


_MULTI_EDIT_MAX = 30


def multi_edit(
    edits: list | None = None,
    allow_outside_project: bool = False,
) -> str:
    """Apply multiple search-replace edits in one call (same rules as edit_file)."""
    if not edits:
        return "ERROR: no edits provided"
    if not isinstance(edits, list):
        return "ERROR: edits must be an array"
    if len(edits) > _MULTI_EDIT_MAX:
        return f"ERROR: max {_MULTI_EDIT_MAX} edits per call (got {len(edits)})"

    lines: list[str] = []
    ok = fail = 0
    i = 0
    while i < len(edits):
        raw = edits[i]
        if not isinstance(raw, dict):
            fail += 1
            lines.append(f"{i + 1}/{len(edits)} ERROR: edit must be an object")
            i += 1
            continue

        path = str(raw.get("path") or "").strip()
        if not path:
            fail += 1
            lines.append(f"{i + 1}/{len(edits)} ERROR: path is required")
            i += 1
            continue

        block: list[dict] = [raw]
        j = i + 1
        while j < len(edits) and isinstance(edits[j], dict) and str(edits[j].get("path") or "").strip() == path:
            block.append(edits[j])
            j += 1

        p = robust_resolve(path)
        # The flag is honored both top-level and per edit item — models often
        # attach it to the edit object (mirroring edit_file's schema).
        allow_block = allow_outside_project or any(
            bool(e.get("allow_outside_project")) for e in block
        )
        if not allow_block:
            scope_err = project_scope_error(p, "multi_edit", "allow_outside_project=true")
            if scope_err:
                for k, _ in enumerate(block):
                    fail += 1
                    lines.append(f"{i + k + 1}/{len(edits)} ERROR {path}: {scope_err}")
                i = j
                continue

        with _path_lock(p):
            if not p.exists():
                for k, _ in enumerate(block):
                    fail += 1
                    lines.append(f"{i + k + 1}/{len(edits)} ERROR {path}: not found")
                i = j
                continue

            txt = p.read_text(errors="ignore")
            before_txt = txt
            stale = _changed_since_seen(p)
            backed_up = False
            for k, edit in enumerate(block):
                idx = i + k + 1
                old_str = edit.get("old_str")
                new_str = edit.get("new_str")
                if old_str is None or new_str is None:
                    fail += 1
                    lines.append(f"{idx}/{len(edits)} ERROR {path}: old_str and new_str are required")
                    continue

                new_txt, err, n, note = _apply_text_edit(
                    txt,
                    str(old_str),
                    str(new_str),
                    bool(edit.get("replace_all")),
                )
                if err:
                    fail += 1
                    lines.append(f"{idx}/{len(edits)} ERROR {path}: {err}")
                    continue

                if not backed_up:
                    _save_backup(p)
                    backed_up = True
                txt = new_txt
                ok += 1
                lines.append(
                    f"{idx}/{len(edits)} EDITED {path} ({n} replacement{'s' if n > 1 else ''})"
                    + (f" [{note}]" if note else "")
                )

            if backed_up:
                p.write_text(txt)
                _cache_invalidate(p)
                _remember_seen(p)

        if backed_up:
            _emit_diff(str(p), before_txt, txt, action="edit")
            if stale:
                lines.append(
                    f"[note: {path} had changed on disk since you last read it — "
                    "re-read it if later edits depend on the parts that changed]"
                )
            warn = _syntax_note(p, before_txt, txt)
            if warn:
                lines.append(warn.strip())

        i = j

    summary = f"{ok} succeeded, {fail} failed"
    return summary + ("\n" + "\n".join(lines) if lines else "")
