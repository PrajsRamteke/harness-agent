"""Repo graph construction, caching, and staleness detection.

Owns the module-level graph cache (``_graph``, ``_graph_mtimes``,
``_graph_root_mtime``). Other modules must read these through this module
(e.g. ``graph._graph``) so they always see the live value rather than a
stale import-time binding.
"""
import hashlib
import pathlib
import pickle
import time
from concurrent.futures import ThreadPoolExecutor
from typing import Dict, List, Optional, Tuple

from ...constants import CWD, CONFIG_DIR, MAX_PARALLEL_TOOLS
from .extract import (
    FileGraph,
    FileRel,
    _CONFIG_FILE_PATTERNS,
    _ROUTE_FILE_PATTERNS,
    _build_file_indexes,
    _build_python_module_index,
    _find_siblings_indexed,
    _find_tests_indexed,
    _parse_file_for_graph,
    _rel_path,
    _scan_source_files,
)

# ── module-level cache ─────────────────────────────────────────────────────────

_graph: Optional[FileGraph] = None
_graph_mtimes: Dict[FileRel, float] = {}  # rel_path -> last mtime
_graph_root_mtime: float = 0.0

_GRAPH_CACHE_VERSION = 2
_GRAPH_CACHE_DIR = CONFIG_DIR / "graph-cache"


def _graph_cache_path() -> pathlib.Path:
    key = hashlib.sha256(str(CWD.resolve()).encode()).hexdigest()[:24]
    return _GRAPH_CACHE_DIR / f"{key}.pkl"


def _load_graph_cache() -> Optional[Tuple[FileGraph, Dict[FileRel, float]]]:
    path = _graph_cache_path()
    if not path.is_file():
        return None
    try:
        with open(path, "rb") as fh:
            payload = pickle.load(fh)
        if payload.get("version") != _GRAPH_CACHE_VERSION:
            return None
        if payload.get("cwd") != str(CWD.resolve()):
            return None
        graph = payload.get("graph")
        mtimes = payload.get("mtimes")
        if not isinstance(graph, dict) or not isinstance(mtimes, dict):
            return None
        return graph, mtimes
    except Exception:
        return None


def _save_graph_cache(graph: FileGraph, mtimes: Dict[FileRel, float]) -> None:
    try:
        _GRAPH_CACHE_DIR.mkdir(parents=True, exist_ok=True)
        payload = {
            "version": _GRAPH_CACHE_VERSION,
            "cwd": str(CWD.resolve()),
            "graph": graph,
            "mtimes": mtimes,
        }
        tmp = _graph_cache_path().with_suffix(".tmp")
        with open(tmp, "wb") as fh:
            pickle.dump(payload, fh, protocol=pickle.HIGHEST_PROTOCOL)
        tmp.replace(_graph_cache_path())
    except Exception:
        pass


def _mtimes_from_scan(files: List[pathlib.Path]) -> Dict[FileRel, float]:
    mtimes: Dict[FileRel, float] = {}
    for f in files:
        rel = _rel_path(f)
        try:
            mtimes[rel] = f.stat().st_mtime
        except OSError:
            mtimes[rel] = 0.0
    return mtimes


def _graph_is_stale(
    cached_mtimes: Dict[FileRel, float],
    files: List[pathlib.Path],
    current_mtimes: Dict[FileRel, float],
) -> bool:
    """True if cached graph does not match the current file set or mtimes."""
    if len(files) != len(cached_mtimes):
        return True
    if set(current_mtimes.keys()) != set(cached_mtimes.keys()):
        return True
    for rel, cur in current_mtimes.items():
        prev = cached_mtimes.get(rel)
        if prev is None or abs(cur - prev) > 0.001:
            return True
    return False


def build_graph(
    root: Optional[pathlib.Path] = None,
    source_files: Optional[List[pathlib.Path]] = None,
    mtimes: Optional[Dict[FileRel, float]] = None,
) -> FileGraph:
    """Build (or rebuild) the repo graph by scanning all source files.

    Results are cached globally and persisted to disk for fast cold starts.
    Pass pre-scanned ``source_files`` and ``mtimes`` to avoid a redundant
    re-scan when the caller already has them (e.g. from _get_or_build_graph).
    """
    global _graph, _graph_mtimes, _graph_root_mtime

    root = root or CWD
    graph: FileGraph = {}

    if source_files is not None:
        all_files = source_files
    else:
        all_files = _scan_source_files(root)

    if mtimes is not None:
        _mtimes = mtimes
    else:
        _mtimes = _mtimes_from_scan(all_files)

    module_index, path_index = _build_python_module_index(all_files)
    by_parent, by_stem = _build_file_indexes(all_files)

    for f in all_files:
        rel = _rel_path(f)
        ext = f.suffix.lower()
        graph[rel] = {
            "imports": [],
            "imported_by": [],
            "exports": [],
            "symbols": [],
            "types": [],
            "tests": [],
            "routes": [],
            "configs": [],
            "siblings": [],
            "ext": ext,
        }

    # Pass 2: extract imports and symbols (parallel when worthwhile)
    parse_args = [(f, module_index, path_index) for f in all_files]
    workers = min(MAX_PARALLEL_TOOLS, len(all_files), 32)

    def _work(args: Tuple[pathlib.Path, Dict[str, str], Dict[str, str]]):
        f, mod_idx, pth_idx = args
        return _parse_file_for_graph(f, mod_idx, pth_idx)

    parsed: List[Tuple[str, List[str], List[str], List[str]]] = []
    if workers <= 1 or len(all_files) <= 3:
        for args in parse_args:
            parsed.append(_work(args))
    else:
        with ThreadPoolExecutor(max_workers=workers) as ex:
            parsed = list(ex.map(_work, parse_args))

    for rel, imports, symbols, types in parsed:
        if rel not in graph:
            continue
        graph[rel]["imports"] = imports
        graph[rel]["symbols"] = symbols
        graph[rel]["types"] = types

    # Pass 3: populate imported_by (reverse imports)
    for rel, info in graph.items():
        for imp in info["imports"]:
            if imp in graph:
                if rel not in graph[imp]["imported_by"]:
                    graph[imp]["imported_by"].append(rel)

    # Pass 4: tests, siblings, configs, routes (indexed — O(n))
    for rel in graph:
        graph[rel]["tests"] = _find_tests_indexed(rel, by_stem)
        graph[rel]["siblings"] = _find_siblings_indexed(rel, by_parent)

        if _ROUTE_FILE_PATTERNS.search(rel):
            graph[rel]["routes"] = [rel]

        if _CONFIG_FILE_PATTERNS.search(rel):
            graph[rel]["configs"] = [rel]

    _graph = graph
    _graph_mtimes = _mtimes
    _graph_root_mtime = time.time()
    _save_graph_cache(graph, _mtimes)
    return graph


def _get_or_build_graph() -> FileGraph:
    """Return cached graph, rebuilding if stale (single scan even on rebuild)."""
    global _graph, _graph_mtimes

    files = _scan_source_files(CWD)
    current_mtimes = _mtimes_from_scan(files)

    if _graph is not None:
        if not _graph_is_stale(_graph_mtimes, files, current_mtimes):
            return _graph  # type: ignore
        return build_graph(source_files=files, mtimes=current_mtimes)

    cached = _load_graph_cache()
    if cached is not None:
        graph, mtimes = cached
        if not _graph_is_stale(mtimes, files, current_mtimes):
            _graph = graph
            _graph_mtimes = mtimes
            return graph

    return build_graph(source_files=files, mtimes=current_mtimes)


def _graph_fingerprint() -> str:
    if not _graph_mtimes:
        return "empty"
    items = sorted(_graph_mtimes.items())
    raw = f"{len(items)}|{repr(items)}".encode("utf-8", errors="replace")
    return hashlib.sha256(raw).hexdigest()[:12]
