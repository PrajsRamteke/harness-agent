"""Connected Context Pack — super-fast codebase understanding.

Instead of the model making 8-20 individual read_file/search_code calls to
understand a codebase, call resolve_context(task) ONCE and get ALL related
files in one bundle. Then edit directly with edit_file.

Architecture (feature-segmented):
  extract — repo scanning + Python/JS symbol & import extraction (stateless)
  graph   — import/symbol graph build + on-disk cache + staleness detection
  bundle  — task → target files → graph expansion → budget-aware bundle

Graph is scoped to CWD and rebuilt automatically when source files change.

This package preserves the public surface of the former single-module
``tools/context.py``; both the public API and the internal helpers used by
tests remain importable as ``context.<name>``.
"""

# ── stateless extraction layer ──────────────────────────────────────────────
from .extract import (  # noqa: F401
    FileRel,
    FileGraph,
    _SKIP_DIR_NAMES,
    _SKIP_EXTS,
    _CODE_EXTS,
    _TEST_FILE_PATTERNS,
    _CONFIG_FILE_PATTERNS,
    _ROUTE_FILE_PATTERNS,
    _should_skip,
    _rel_path,
    _scan_source_files,
    _resolve_local_import,
    _resolve_relative_import_indexed,
    _build_python_module_index,
    _build_file_indexes,
    _find_tests_indexed,
    _find_siblings_indexed,
    _find_js_import,
    _extract_python_info,
    _extract_python_imports,
    _extract_python_symbols,
    _extract_js_imports,
    _extract_annotation_name,
    _extract_js_symbols,
    _parse_file_for_graph,
    _extract_content_keywords,
)

# ── graph build + cache layer ───────────────────────────────────────────────
from .graph import (  # noqa: F401
    build_graph,
    _get_or_build_graph,
    _graph_cache_path,
    _load_graph_cache,
    _save_graph_cache,
    _mtimes_from_scan,
    _graph_is_stale,
    _graph_fingerprint,
    _GRAPH_CACHE_VERSION,
    _GRAPH_CACHE_DIR,
)

# ── task → bundle layer + public API ────────────────────────────────────────
from .bundle import (  # noqa: F401
    resolve_context,
    read_bundle,
    BUNDLE_MARKER,
    _BUNDLE_MODES,
    _CACHE_MAX,
    _COLLECTOR_DEFAULTS,
    _tokenize_task,
    _score_file_for_task,
    _resolve_target_files,
    _collect_connected_files,
    _normalize_mode,
    _relation_weight,
    _allocate_char_budget,
    _file_size,
    _read_capped,
    _skeleton_block,
    _manifest_line,
    _cache_get,
    _cache_put,
    _render_file_entry,
    _build_bundle,
    _sort_connected_items,
)

__all__ = ["resolve_context", "read_bundle", "build_graph", "BUNDLE_MARKER"]
