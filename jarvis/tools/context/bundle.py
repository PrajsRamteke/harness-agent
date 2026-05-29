"""Task → context bundle: target resolution, graph traversal, budget-aware
rendering, and the public ``resolve_context`` / ``read_bundle`` entry points.
"""
import hashlib
import re
from collections import OrderedDict, defaultdict
from concurrent.futures import ThreadPoolExecutor
from typing import Dict, List, Optional, Set, Tuple

from ...constants import (
    CONTEXT_BUNDLE_MAX_CHARS,
    CONTEXT_BUNDLE_PER_FILE_MAX,
    BUNDLE_DEFAULT_MODE,
    BUNDLE_DEFAULT_MODE_READ,
    MAX_PARALLEL_TOOLS,
)
from ...path_resolve import robust_resolve
from .extract import FileGraph
from . import graph as _graphmod

# Token tokenizer for tasks
_TASK_TOKEN_RE = re.compile(r'[a-zA-Z_][a-zA-Z0-9_]{1,}')


# =============================================================================
#  TARGET RESOLVER
# =============================================================================

def _tokenize_task(task: str) -> List[str]:
    """Split task into meaningful query tokens."""
    # Extract key terms — file names, symbols, action words
    tokens = _TASK_TOKEN_RE.findall(task)
    stopwords = {"the", "this", "that", "and", "for", "with", "from", "to",
                 "in", "on", "at", "by", "is", "are", "was", "be", "been",
                 "fix", "add", "update", "change", "remove", "delete", "create",
                 "make", "get", "set", "put", "do", "need", "want", "have",
                 "implement", "refactor", "improve", "simplify", "move"}
    return [t.lower() for t in tokens if t.lower() not in stopwords and len(t) > 2]


def _score_file_for_task(rel: str, info: dict, tokens: List[str], graph: FileGraph) -> float:
    """Score how relevant a file is to a task query."""
    score = 0.0
    hay = rel.lower()

    for token in tokens:
        # Filename match (high weight)
        if token in hay:
            score += 10.0

        # Symbol match (high weight)
        for sym in info.get("symbols", []):
            if token in sym.lower():
                score += 8.0

        # Type match
        for typ in info.get("types", []):
            if token in typ.lower():
                score += 7.0

        # Imported by / imports — connected files get boosted
        for imp in info.get("imports", []):
            if token in imp.lower():
                score += 4.0

        for importer in info.get("imported_by", []):
            if token in importer.lower():
                score += 3.0

    return score


def _resolve_target_files(task: str, graph: FileGraph, max_targets: int = 5) -> List[str]:
    """Resolve task to root file paths using filename + symbol + content scoring."""
    tokens = _tokenize_task(task)
    if not tokens:
        # No meaningful tokens — return entry points (main, app, index)
        return [rel for rel in graph if any(
            name in rel for name in ("main", "app", "index", "__init__")
        )][:max_targets]

    scored = []
    for rel, info in graph.items():
        score = _score_file_for_task(rel, info, tokens, graph)
        if score > 0:
            scored.append((score, rel))

    scored.sort(key=lambda x: -x[0])
    return [rel for _, rel in scored[:max_targets]]


# =============================================================================
#  CONNECTED CONTEXT COLLECTOR
# =============================================================================

_COLLECTOR_DEFAULTS = {
    "max_depth": 2,
    "max_files": 25,
}


def _collect_connected_files(
    root_files: List[str],
    graph: FileGraph,
    max_depth: int = 2,
    max_files: int = 25,
) -> Dict[str, str]:
    """Collect root files + connected files (imports, importers, siblings, tests, types).

    Returns dict of {rel_path: relation_label}.
    """
    collected: Dict[str, str] = {}  # rel -> relation description
    seen: Set[str] = set(root_files)
    queue: List[Tuple[str, int]] = [(f, 0) for f in root_files]

    # Add root files first
    for f in root_files:
        rel = f
        if rel not in collected:
            collected[rel] = "root_target"

    while queue and len(collected) < max_files:
        current, depth = queue.pop(0)
        if depth >= max_depth:
            continue

        info = graph.get(current)
        if not info:
            continue

        # Imports (depth + 1)
        for imp in info.get("imports", []):
            if imp not in seen and imp in graph and len(collected) < max_files:
                seen.add(imp)
                collected[imp] = f"imported_by_{current}"
                queue.append((imp, depth + 1))

        # Imported-by (who uses this file)
        for importer in info.get("imported_by", []):
            if importer not in seen and importer in graph and len(collected) < max_files:
                seen.add(importer)
                collected[importer] = f"importer_of_{current}"
                queue.append((importer, depth + 1))

        # Siblings (same folder) — depth + 1
        if depth + 1 <= max_depth:
            for sibling in info.get("siblings", []):
                if sibling not in seen and sibling in graph and len(collected) < max_files:
                    seen.add(sibling)
                    collected[sibling] = f"sibling_of_{current}"
                    queue.append((sibling, depth + 1))

        # Tests (always include if found)
        for test in info.get("tests", []):
            if test not in seen and test in graph and len(collected) < max_files:
                seen.add(test)
                collected[test] = f"test_for_{current}"

        # Configs
        for cfg in info.get("configs", []):
            if cfg not in seen and cfg in graph and len(collected) < max_files:
                seen.add(cfg)
                collected[cfg] = f"config_related"

        # Routes
        for route in info.get("routes", []):
            if route not in seen and route in graph and len(collected) < max_files:
                seen.add(route)
                collected[route] = f"route_entry"

    return collected


# =============================================================================
#  BUNDLE RENDERING & CACHING
# =============================================================================

# Trim.py preserves tool results that start with this marker (never stubbed).
BUNDLE_MARKER = "=== Connected Context Pack ==="
_BUNDLE_MODES = frozenset({"full", "skeleton", "manifest"})
_CONTEXT_ANALYSIS_CACHE: "OrderedDict[str, str]" = OrderedDict()
_PATH_BUNDLE_CACHE: "OrderedDict[str, str]" = OrderedDict()
_CACHE_MAX = 16


def _normalize_mode(mode: str, default: str) -> str:
    m = (mode or default or "skeleton").strip().lower()
    return m if m in _BUNDLE_MODES else default


def _relation_weight(relation: str) -> float:
    if relation == "root_target":
        return 4.0
    if relation.startswith("imported_by"):
        return 2.5
    if relation.startswith("importer_of"):
        return 2.5
    if relation.startswith("test_for"):
        return 2.0
    if relation in ("route_entry", "config_related"):
        return 1.5
    if relation == "requested":
        return 3.5
    return 1.0


def _allocate_char_budget(
    items: List[Tuple[str, str]],
    max_chars: int,
    per_file_max: int,
) -> Dict[str, int]:
    """Split bundle budget across files by relation priority."""
    if not items:
        return {}
    weights = {rel: _relation_weight(rel_type) for rel, rel_type in items}
    total_w = sum(weights.values()) or 1.0
    budgets: Dict[str, int] = {}
    for rel, w in weights.items():
        share = int(max_chars * w / total_w)
        budgets[rel] = min(per_file_max, max(400, share))

    total = sum(budgets.values())
    if total <= max_chars:
        return budgets

    scale = max_chars / total
    scaled = {rel: max(300, int(b * scale)) for rel, b in budgets.items()}
    while sum(scaled.values()) > max_chars:
        rel = max(scaled, key=scaled.get)
        scaled[rel] = max(300, scaled[rel] - 200)
    return scaled


def _file_size(rel: str) -> int:
    try:
        p = robust_resolve(rel)
        if p.is_file():
            return p.stat().st_size
    except OSError:
        pass
    return 0


def _read_capped(rel: str, max_chars: int) -> Tuple[str, bool, int]:
    """Read at most max_chars. Returns (text, partial, approx_disk_chars)."""
    from ..files import read_file

    if max_chars <= 0:
        return "", False, 0

    size = _file_size(rel)
    if size and size <= max_chars:
        txt = read_file(rel)
        return txt, False, len(txt)

    line_limit = max(25, max_chars // 80)
    partial = read_file(rel, offset=0, limit=line_limit)
    if partial.startswith("ERROR:"):
        txt = read_file(rel)
        if len(txt) <= max_chars:
            return txt, False, len(txt)
        return (
            txt[:max_chars]
            + f"\n\n[truncated at {max_chars} chars — use read_file offset/limit for more]",
            True,
            max_chars,
        )

    note = (
        f"\n\n[PARTIAL: first ~{line_limit} lines"
        + (f"; file ~{size:,} bytes" if size else "")
        + "; use read_file for more]"
    )
    out = partial + note
    if len(out) > max_chars:
        out = out[:max_chars] + "\n[…]"
    return out, True, len(out)


def _skeleton_block(rel: str, relation: str, info: Optional[dict]) -> str:
    lines = [f"\n--- {rel} (RELATION: {relation}) [skeleton] ---"]
    if info:
        syms = (info.get("symbols") or [])[:40]
        types = (info.get("types") or [])[:25]
        imps = (info.get("imports") or [])[:12]
        if syms:
            lines.append(f"symbols: {', '.join(syms)}")
        if types:
            lines.append(f"types: {', '.join(types)}")
        if imps:
            lines.append(f"imports: {', '.join(imps)}")
    sz = _file_size(rel)
    if sz:
        lines.append(f"size: {sz:,} bytes")
    lines.append("(body omitted — read_file or read_bundle mode=full for full text)")
    return "\n".join(lines)


def _manifest_line(rel: str, relation: str, info: Optional[dict]) -> str:
    bits = [rel, f"({relation})"]
    if info:
        ns = len(info.get("symbols") or [])
        if ns:
            bits.append(f"{ns} symbols")
    sz = _file_size(rel)
    if sz:
        bits.append(f"{sz:,}B")
    return "  · ".join(bits)


def _cache_get(cache: "OrderedDict[str, str]", key: str) -> Optional[str]:
    if key in cache:
        cache.move_to_end(key)
        return cache[key]
    return None


def _cache_put(cache: "OrderedDict[str, str]", key: str, value: str) -> None:
    cache[key] = value
    cache.move_to_end(key)
    while len(cache) > _CACHE_MAX:
        cache.popitem(last=False)


def _render_file_entry(
    rel: str,
    relation: str,
    mode: str,
    budget: int,
    graph: Optional[FileGraph],
) -> Tuple[str, int, int, str]:
    """Returns (section_text, emitted_chars, disk_read_chars, kind full|skeleton|manifest|skip)."""
    info = (graph or {}).get(rel)
    use_skeleton = mode == "manifest" or (
        mode == "skeleton" and relation != "root_target" and relation != "requested"
    )

    if use_skeleton:
        text = _skeleton_block(rel, relation, info) if mode != "manifest" else _manifest_line(rel, relation, info)
        kind = "manifest" if mode == "manifest" else "skeleton"
        if mode == "manifest":
            return text + "\n", len(text) + 1, 0, kind
        return text, len(text), 0, kind

    if budget <= 0:
        return _manifest_line(rel, relation, info) + "\n", len(rel) + 20, 0, "skip"

    body, partial, disk = _read_capped(rel, budget)
    header = f"\n--- {rel} (RELATION: {relation})"
    if partial:
        header += " [partial]"
    header += " ---\n"
    text = header + body
    return text, len(text), disk, "partial" if partial else "full"


def _build_bundle(
    items: List[Tuple[str, str]],
    *,
    mode: str,
    max_chars: int,
    per_file_max: int,
    header_lines: List[str],
    graph: Optional[FileGraph] = None,
) -> str:
    """Assemble a budget-aware context bundle."""
    mode = _normalize_mode(mode, "skeleton")
    max_chars = max(4000, max_chars)
    per_file_max = min(per_file_max, max_chars)

    lines: List[str] = [BUNDLE_MARKER, *header_lines, f"Mode: {mode}", ""]

    if not items:
        lines.append("(no files)")
        return "\n".join(lines)

    budgets = _allocate_char_budget(items, max_chars, per_file_max)
    emitted_total = len("\n".join(lines))
    disk_total = 0
    kinds: Dict[str, int] = defaultdict(int)

    def _work(item: Tuple[str, str]) -> Tuple[str, int, int, str]:
        rel, relation = item
        return _render_file_entry(rel, relation, mode, budgets.get(rel, 0), graph)

    workers = min(MAX_PARALLEL_TOOLS, len(items))
    sections: List[Tuple[str, int, int, str]] = []
    if workers <= 1:
        for item in items:
            sections.append(_work(item))
    else:
        with ThreadPoolExecutor(max_workers=workers) as ex:
            sections = list(ex.map(_work, items))

    for (rel, _), (text, em, disk, kind) in zip(items, sections):
        if emitted_total + em > max_chars:
            lines.append(f"\n--- {rel} (SKIPPED: bundle char limit) ---")
            kinds["skipped"] += 1
            continue
        lines.append(text)
        emitted_total += em
        disk_total += disk
        kinds[kind] += 1

    if graph and mode != "manifest":
        lines.append("\n\n=== Relationships ===")
        for rel, relation in items:
            if relation != "root_target" and relation != "requested":
                lines.append(f"  {rel}  ←  {relation}")

    if graph:
        lines.append(f"\n=== Graph: {len(graph)} files indexed ===")

    lines.append(
        f"\n=== Bundle stats: mode={mode}, files={len(items)}, "
        f"full={kinds['full']}, partial={kinds['partial']}, "
        f"skeleton={kinds['skeleton']}, manifest={kinds['manifest']}, "
        f"skipped={kinds['skipped']}, emitted≈{emitted_total:,} chars "
        f"(limit {max_chars:,}), disk_read≈{disk_total:,} chars ==="
    )
    return "\n".join(lines)


def _sort_connected_items(connected: Dict[str, str]) -> List[Tuple[str, str]]:
    def _sort_key(item: Tuple[str, str]) -> Tuple[int, str]:
        rel, relation = item
        if relation == "root_target":
            return (0, rel)
        if relation.startswith("imported_by"):
            return (1, rel)
        if relation.startswith("importer_of"):
            return (2, rel)
        if relation.startswith("test_for"):
            return (3, rel)
        if relation == "route_entry":
            return (4, rel)
        if relation == "config_related":
            return (5, rel)
        if relation.startswith("sibling_of"):
            return (6, rel)
        return (9, rel)

    return sorted(connected.items(), key=_sort_key)


# =============================================================================
#  PUBLIC API
# =============================================================================

def resolve_context(task: str, mode: str = "", max_chars: int = 0) -> str:
    """Resolve a coding task and return a budget-aware Connected Context Pack.

    Default mode is ``skeleton``: root targets are read (capped per file); related
    files show symbols/imports only. Use ``mode=full`` for all bodies, or
    ``mode=manifest`` for a path list only (then ``read_bundle`` on key paths).
    """
    mode = _normalize_mode(mode, BUNDLE_DEFAULT_MODE)
    cap = max_chars if max_chars > 0 else CONTEXT_BUNDLE_MAX_CHARS

    cache_key = hashlib.sha256(
        f"resolve|{task}|{mode}|{cap}|{_graphmod._graph_fingerprint()}".encode()
    ).hexdigest()[:20]
    hit = _cache_get(_CONTEXT_ANALYSIS_CACHE, cache_key)
    if hit is not None:
        return hit

    try:
        graph = _graphmod._get_or_build_graph()
    except Exception as e:
        return f"Error building repo graph: {e}"

    try:
        targets = _resolve_target_files(task, graph)
    except Exception:
        targets = []

    if not targets:
        for key in ("main", "app", "index", "cli", "router", "__init__"):
            matches = [rel for rel in graph if key in rel.lower()]
            if matches:
                targets = matches[:3]
                break
        if not targets:
            targets = [rel for rel in sorted(graph.keys()) if "/" not in rel][:5]

    if not targets:
        return "No relevant files found. The repo graph may be empty or the task too vague."

    try:
        connected = _collect_connected_files(targets, graph, **_COLLECTOR_DEFAULTS)
    except Exception:
        connected = {t: "root_target" for t in targets}

    items = _sort_connected_items(connected)
    header = [
        f"Task: {task}",
        f"Root files: {', '.join(targets)}",
        f"Connected files: {len(connected)} total",
    ]
    result = _build_bundle(
        items,
        mode=mode,
        max_chars=cap,
        per_file_max=CONTEXT_BUNDLE_PER_FILE_MAX,
        header_lines=header,
        graph=graph,
    )
    _cache_put(_CONTEXT_ANALYSIS_CACHE, cache_key, result)
    return result


def read_bundle(paths: List[str], mode: str = "", max_chars: int = 0) -> str:
    """Batch-read paths into one budget-aware bundle (parallel I/O, read cache).

    Default mode is ``full``. After ``resolve_context(..., mode='manifest')``,
    call ``read_bundle`` on the 3–8 paths you need with ``mode='full'``.
    """
    if not paths:
        return "ERROR: no paths provided"

    mode = _normalize_mode(mode, BUNDLE_DEFAULT_MODE_READ)
    cap = max_chars if max_chars > 0 else CONTEXT_BUNDLE_MAX_CHARS
    paths = list(dict.fromkeys(paths))[:20]

    path_sig = []
    for p in paths:
        try:
            rp = robust_resolve(p)
            path_sig.append((p, rp.stat().st_mtime, rp.stat().st_size))
        except OSError:
            path_sig.append((p, 0, 0))
    cache_key = hashlib.sha256(
        repr((path_sig, mode, cap)).encode()
    ).hexdigest()[:20]
    hit = _cache_get(_PATH_BUNDLE_CACHE, cache_key)
    if hit is not None:
        return hit

    items = [(p, "requested") for p in paths]
    result = _build_bundle(
        items,
        mode=mode,
        max_chars=cap,
        per_file_max=CONTEXT_BUNDLE_PER_FILE_MAX,
        header_lines=[f"Paths: {', '.join(paths)}"],
        graph=_graphmod._graph,
    )
    _cache_put(_PATH_BUNDLE_CACHE, cache_key, result)
    return result
