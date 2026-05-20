"""Connected Context Pack — super-fast codebase understanding.

Instead of the model making 8-20 individual read_file/search_code calls to
understand a codebase, call resolve_context(task) ONCE and get ALL related
files in one bundle. Then edit directly with edit_file.

Architecture:
  RepoGraph   — scans repo, caches import/export/symbol relationships
  resolve_context — resolve task → target files → expand graph → bundle content
  read_bundle  — batch-read specific files by path (for when model already knows)

Graph is scoped to CWD and rebuilt automatically when source files change.
"""
import ast
import hashlib
import os
import pathlib
import pickle
import re
import time
import tokenize
import io
from collections import OrderedDict, defaultdict
from concurrent.futures import ThreadPoolExecutor
from typing import Dict, List, Optional, Set, Tuple

from ..constants import (
    CWD,
    CONFIG_DIR,
    CONTEXT_BUNDLE_MAX_CHARS,
    CONTEXT_BUNDLE_PER_FILE_MAX,
    BUNDLE_DEFAULT_MODE,
    BUNDLE_DEFAULT_MODE_READ,
    MAX_PARALLEL_TOOLS,
)
from ..path_resolve import robust_resolve
from .. import state
from .dirs import SKIP_DIRS

# ── skip dirs / exts ───────────────────────────────────────────────────────────

_SKIP_DIR_NAMES = SKIP_DIRS | {
    "__pycache__", ".next", "coverage", ".expo", "vendor",
    "android", "ios", ".rustup", ".cargo",
}

_SKIP_EXTS = {
    ".png", ".jpg", ".jpeg", ".gif", ".bmp", ".tiff", ".webp", ".heic",
    ".ico", ".icns", ".pdf", ".zip", ".tar", ".gz", ".tgz", ".bz2", ".xz",
    ".7z", ".rar", ".jar", ".war", ".class", ".exe", ".dll", ".so", ".dylib",
    ".o", ".a", ".obj", ".bin", ".dat", ".db", ".sqlite", ".sqlite3",
    ".pyc", ".pyo", ".pyd", ".whl", ".egg",
    ".mp3", ".mp4", ".mov", ".avi", ".mkv", ".wav", ".flac", ".ogg",
    ".ttf", ".otf", ".woff", ".woff2", ".eot",
    ".lock", ".svg",
}

_CODE_EXTS = {".py", ".js", ".jsx", ".ts", ".tsx", ".mjs", ".cjs", ".mts", ".cts"}
_TEST_FILE_PATTERNS = re.compile(r"(?:^|[/\\])(?:test_|.*?_test|.*?\.spec|.*?\.test)\.", re.I)
_CONFIG_FILE_PATTERNS = re.compile(r"(?:^|[/\\])\.?(?:env|.*?config|\.env\..*|docker-compose|dockerfile)", re.I)
_ROUTE_FILE_PATTERNS = re.compile(r"(?:^|[/\\])(?:routes?|controllers?|endpoints?|views?|pages?|api)", re.I)

# ── types ──────────────────────────────────────────────────────────────────────

FileRel = str  # relative path like "src/auth/login.ts"

FileGraph = Dict[FileRel, Dict[str, List[str]]]
"""
{
  "src/auth/login.ts": {
    "imports": ["src/auth/auth.service.ts", "src/common/jwt.ts"],
    "imported_by": ["src/routes/auth.ts"],
    "symbols": ["loginHandler", "validateLogin"],
    "types": ["LoginRequest", "LoginResponse"],
    "tests": ["tests/auth/login.test.ts"],
    "routes": [],
    "configs": [],
    "siblings": ["src/auth/register.ts", "src/auth/types.ts", "src/auth/index.ts"],
    "ext": ".ts"
  },
  ...
}
"""

# ── module-level cache ─────────────────────────────────────────────────────────

_graph: Optional[FileGraph] = None
_graph_mtimes: Dict[FileRel, float] = {}  # rel_path -> last mtime
_graph_root_mtime: float = 0.0

_GRAPH_CACHE_VERSION = 2
_GRAPH_CACHE_DIR = CONFIG_DIR / "graph-cache"


# =============================================================================
#  REPO GRAPH BUILDER
# =============================================================================

def _should_skip(p: pathlib.Path) -> bool:
    if p.suffix.lower() in _SKIP_EXTS:
        return True
    for part in p.parts:
        if part in _SKIP_DIR_NAMES:
            return True
    return False


def _rel_path(p: pathlib.Path) -> str:
    """Return path relative to CWD. Falls back to str(p) on ValueError."""
    try:
        return str(p.relative_to(CWD))
    except ValueError:
        return str(p)


def _scan_source_files(root: pathlib.Path) -> List[pathlib.Path]:
    """Walk root and return all code/ config/ test files, skipping undesirables.

    Uses os.walk with in-place dir pruning so we skip heavy dirs at traversal
    time instead of iterating every file inside them (works on Python 3.10+).
    """
    files = []
    for root_dir, dirs, file_names in os.walk(root, topdown=True):
        # Prune skip dirs in-place so walk() never descends into them
        # (must match _SKIP_DIR_NAMES and SKIP_DIRS from dirs.py)
        dirs[:] = [d for d in dirs if d not in _SKIP_DIR_NAMES]

        for name in file_names:
            p = pathlib.Path(root_dir) / name
            ext = p.suffix.lower()
            if ext in _SKIP_EXTS:
                continue
            if ext in _CODE_EXTS:
                files.append(p)
            elif _CONFIG_FILE_PATTERNS.search(name):
                files.append(p)
    return files


def _resolve_local_import(
    import_name: str,
    source_file: pathlib.Path,
    module_index: Optional[Dict[str, str]] = None,
    path_index: Optional[Dict[str, str]] = None,
) -> Optional[str]:
    """Resolve a Python import name to a relative file path within the project.

    e.g. "jarvis.tools.context" -> "jarvis/tools/context.py"
         ".tools.context"       -> resolved relative to source_file
    """
    if module_index is not None and not import_name.startswith("."):
        hit = module_index.get(import_name)
        if hit:
            return hit

    if path_index is not None and import_name.startswith("."):
        hit = _resolve_relative_import_indexed(import_name, source_file, path_index)
        if hit:
            return hit

    # Absolute import relative to project root
    if not import_name.startswith("."):
        # Try as module path relative to CWD
        as_path = import_name.replace(".", "/")
        for ext in (".py",):
            candidate = CWD / f"{as_path}{ext}"
            if candidate.exists():
                return _rel_path(candidate)
            # Also try __init__.py
            init = CWD / as_path / "__init__.py"
            if init.exists():
                return _rel_path(init)
        return None

    # Relative import
    level = 0
    while import_name.startswith("."):
        level += 1
        import_name = import_name[1:]

    base = source_file.parent
    for _ in range(level - 1):
        base = base.parent

    if not import_name:
        # from . import foo — resolve to __init__.py
        init = base / "__init__.py"
        if init.exists():
            return _rel_path(init)
        return None

    as_path = import_name.replace(".", "/")
    candidate = base / f"{as_path}.py"
    if candidate.exists():
        return _rel_path(candidate)
    init = base / as_path / "__init__.py"
    if init.exists():
        return _rel_path(init)
    return None


def _resolve_relative_import_indexed(
    import_name: str,
    source_file: pathlib.Path,
    path_index: Dict[str, str],
) -> Optional[str]:
    """Resolve a relative import using a pre-built path index (no stat calls)."""
    level = 0
    mod = import_name
    while mod.startswith("."):
        level += 1
        mod = mod[1:]

    base = source_file.parent
    for _ in range(level - 1):
        base = base.parent

    try:
        base_rel = base.relative_to(CWD)
    except ValueError:
        return None

    base_key = str(base_rel).replace("\\", "/")
    if base_key == ".":
        base_key = ""

    if not mod:
        return path_index.get(base_key)

    rel_path = f"{base_key}/{mod.replace('.', '/')}" if base_key else mod.replace(".", "/")
    hit = path_index.get(rel_path)
    if hit:
        return hit

    # Package directory (mod may point at a package __init__)
    return path_index.get(f"{rel_path}/__init__".replace("/__init__/__init__", "/__init__"))


def _build_python_module_index(
    all_files: List[pathlib.Path],
) -> Tuple[Dict[str, str], Dict[str, str]]:
    """Build dotted-module and slash-path indexes for O(1) import resolution."""
    module_index: Dict[str, str] = {}
    path_index: Dict[str, str] = {}

    for f in all_files:
        if f.suffix.lower() != ".py":
            continue
        rel = _rel_path(f)
        p = pathlib.Path(rel)
        if p.name == "__init__.py":
            pkg_key = str(p.parent).replace("\\", "/")
            if pkg_key == ".":
                pkg_key = ""
            mod_key = pkg_key.replace("/", ".")
            if mod_key:
                module_index[mod_key] = rel
            path_index[pkg_key] = rel
        else:
            path_key = str(p.with_suffix("")).replace("\\", "/")
            mod_key = path_key.replace("/", ".")
            module_index[mod_key] = rel
            path_index[path_key] = rel

    return module_index, path_index


def _build_file_indexes(
    all_files: List[pathlib.Path],
) -> Tuple[Dict[str, List[str]], Dict[str, List[str]]]:
    """Pre-index files by parent directory and stem for O(1) sibling/test lookup."""
    by_parent: Dict[str, List[str]] = defaultdict(list)
    by_stem: Dict[str, List[str]] = defaultdict(list)

    for f in all_files:
        rel = _rel_path(f)
        parent = str(pathlib.Path(rel).parent).replace("\\", "/")
        if parent == ".":
            parent = ""
        by_parent[parent].append(rel)
        by_stem[f.stem].append(rel)

    return by_parent, by_stem


def _find_tests_indexed(rel: str, by_stem: Dict[str, List[str]]) -> List[str]:
    """Find test files for *rel* using a stem index."""
    stem = pathlib.Path(rel).stem
    tests: List[str] = []
    seen: Set[str] = set()

    def _add(candidate: str) -> None:
        if candidate != rel and candidate not in seen:
            seen.add(candidate)
            tests.append(candidate)

    for variant in (f"test_{stem}", f"{stem}_test", f"{stem}.spec", f"{stem}.test"):
        for candidate in by_stem.get(variant, []):
            _add(candidate)

    for candidate in by_stem.get(stem, []):
        parts = pathlib.Path(candidate).parts
        if "__tests__" in parts or "tests" in parts:
            _add(candidate)

    return tests


def _find_siblings_indexed(rel: str, by_parent: Dict[str, List[str]]) -> List[str]:
    """Find same-folder files excluding *rel* using a parent index."""
    parent = str(pathlib.Path(rel).parent).replace("\\", "/")
    if parent == ".":
        parent = ""
    return [r for r in by_parent.get(parent, []) if r != rel]


def _find_js_import(import_path: str, source_file: pathlib.Path) -> Optional[str]:
    """Resolve a JS/TS import path to a project file."""
    if import_path.startswith(".") or import_path.startswith("/"):
        if import_path.startswith("/"):
            # Absolute project path
            base = CWD
            clean = import_path.lstrip("/")
        else:
            base = source_file.parent
            clean = import_path

        for ext in ("", ".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs", "/index.ts",
                     "/index.tsx", "/index.js", "/index.jsx", "/index.mjs"):
            candidate = (base / f"{clean}{ext}").resolve()
            try:
                candidate = candidate.relative_to(CWD)
            except ValueError:
                continue
            full = CWD / candidate
            if full.exists():
                return _rel_path(full)
    return None


def _extract_python_info(
    source: str,
    filepath: pathlib.Path,
    module_index: Optional[Dict[str, str]] = None,
    path_index: Optional[Dict[str, str]] = None,
) -> Tuple[List[str], List[str], List[str]]:
    """Single-pass AST extract: imports, symbols, types."""
    imports: List[str] = []
    symbols: List[str] = []
    types: List[str] = []
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return imports, symbols, types

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                resolved = _resolve_local_import(
                    alias.name, filepath, module_index, path_index,
                )
                if resolved:
                    imports.append(resolved)
        elif isinstance(node, ast.ImportFrom):
            if node.module:
                resolved = _resolve_local_import(
                    node.module, filepath, module_index, path_index,
                )
                if resolved:
                    imports.append(resolved)
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            symbols.append(node.name)
            for arg in node.args.args + node.args.kwonlyargs + node.args.posonlyargs:
                if arg.annotation:
                    ann = _extract_annotation_name(arg.annotation)
                    if ann and ann[0].isupper():
                        types.append(ann)
            if node.returns:
                ann = _extract_annotation_name(node.returns)
                if ann and ann[0].isupper():
                    types.append(ann)
        elif isinstance(node, ast.ClassDef):
            symbols.append(node.name)
            for base in node.bases:
                if isinstance(base, ast.Name) and base.id in {"TypedDict", "NamedTuple", "Protocol"}:
                    types.append(node.name)
                    break
        elif isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name):
                    val = node.value
                    if isinstance(val, ast.Call):
                        if isinstance(val.func, ast.Name) and val.func.id in {
                            "TypeVar", "NewType", "TypeAlias",
                        }:
                            types.append(target.id)
                        elif isinstance(val.func, ast.Attribute):
                            if val.func.attr in {"TypeVar", "NewType"}:
                                types.append(target.id)
        elif isinstance(node, ast.AnnAssign):
            if isinstance(node.target, ast.Name) and node.annotation:
                ann = _extract_annotation_name(node.annotation)
                if ann and ann[0].isupper():
                    types.append(node.target.id)

    return imports, list(set(symbols)), list(set(types))


def _extract_python_imports(
    source: str,
    filepath: pathlib.Path,
    module_index: Optional[Dict[str, str]] = None,
    path_index: Optional[Dict[str, str]] = None,
) -> List[str]:
    """Extract local imports from a Python file using AST."""
    imports, _, _ = _extract_python_info(source, filepath, module_index, path_index)
    return imports


def _extract_python_symbols(source: str) -> Tuple[List[str], List[str]]:
    """Extract (symbols, types) from a Python file using AST."""
    _, symbols, types = _extract_python_info(source, pathlib.Path("__dummy__.py"))
    return symbols, types


def _extract_js_imports(source: str, filepath: pathlib.Path) -> List[str]:
    """Extract local imports from a JS/TS file using regex."""
    imports: List[str] = []
    # import X from '...'
    for m in re.finditer(r"""['"]([./][^'"]+)['"]""", source):
        path = m.group(1)
        resolved = _find_js_import(path, filepath)
        if resolved:
            imports.append(resolved)
    # require('...')
    for m in re.finditer(r"""require\s*\(\s*['"]([./][^'"]+)['"]\s*\)""", source):
        path = m.group(1)
        resolved = _find_js_import(path, filepath)
        if resolved:
            imports.append(resolved)
    return list(set(imports))


def _extract_annotation_name(node) -> Optional[str]:
    """Extract the top-level name from a type annotation node."""
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Subscript):
        return _extract_annotation_name(node.value)
    if isinstance(node, ast.Attribute):
        return node.attr
    return None


def _extract_js_symbols(source: str) -> Tuple[List[str], List[str]]:
    """Extract (symbols, types) from a JS/TS file using regex."""
    symbols: List[str] = []
    types: List[str] = []

    # function / async function
    for m in re.finditer(r"""(?:export\s+)?(?:async\s+)?function\s+(\w+)""", source):
        symbols.append(m.group(1))
    # const/let/var functions
    for m in re.finditer(r"""(?:export\s+)?(?:const|let|var)\s+(\w+)\s*[:=]\s*(?:async\s*)?\(""", source):
        symbols.append(m.group(1))
    # arrow functions assigned to exports
    for m in re.finditer(r"""(?:export\s+)?(?:const|let|var)\s+(\w+)\s*=\s*(?:async\s*)?=>""", source):
        symbols.append(m.group(1))
    # class
    for m in re.finditer(r"""(?:export\s+)?(?:abstract\s+)?class\s+(\w+)""", source):
        symbols.append(m.group(1))
    # interface / type
    for m in re.finditer(r"""(?:export\s+)?interface\s+(\w+)""", source):
        types.append(m.group(1))
    for m in re.finditer(r"""(?:export\s+)?type\s+(\w+)""", source):
        types.append(m.group(1))

    return list(set(symbols)), list(set(types))


def _parse_file_for_graph(
    f: pathlib.Path,
    module_index: Dict[str, str],
    path_index: Dict[str, str],
) -> Tuple[str, List[str], List[str], List[str]]:
    """Read and parse one source file for graph construction."""
    rel = _rel_path(f)
    ext = f.suffix.lower()
    try:
        source = f.read_text(errors="ignore")
    except Exception:
        return rel, [], [], []

    if ext == ".py":
        return (rel, *_extract_python_info(source, f, module_index, path_index))
    if ext in {".js", ".jsx", ".ts", ".tsx", ".mjs", ".cjs", ".mts", ".cts"}:
        imports = _extract_js_imports(source, f)
        symbols, types = _extract_js_symbols(source)
        return rel, imports, symbols, types
    return rel, [], [], []


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


def _extract_content_keywords(source: str, max_words: int = 30) -> List[str]:
    """Extract important keywords from source for matching."""
    # Remove strings and comments
    text = re.sub(r""""[^"]*"|'[^']*'|#[^\n]*""", "", source)
    # Find camelCase/PascalCase words
    words = re.findall(r'[A-Z][a-z]+(?=[A-Z]|$|[a-z])|[a-z]+|[A-Z][a-z]*', text)
    # Filter stopwords
    stopwords = {"the", "this", "that", "and", "for", "from", "import", "function",
                 "class", "const", "let", "var", "return", "export", "default",
                 "async", "await", "if", "else", "try", "catch", "new", "type",
                 "interface", "extends", "implements", "true", "false", "null",
                 "undefined", "void", "number", "string", "boolean", "any",
                 "never", "unknown", "object", "array", "tuple", "enum"}
    return [w for w in words if w.lower() not in stopwords and len(w) > 1][:max_words]


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


# =============================================================================
#  TARGET RESOLVER
# =============================================================================

def _tokenize_task(task: str) -> List[str]:
    """Split task into meaningful query tokens."""
    # Extract key terms — file names, symbols, action words
    tokens = re.findall(r'[a-zA-Z_][a-zA-Z0-9_]{1,}', task)
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


# Trim.py preserves tool results that start with this marker (never stubbed).
BUNDLE_MARKER = "=== Connected Context Pack ==="
_BUNDLE_MODES = frozenset({"full", "skeleton", "manifest"})
_CONTEXT_ANALYSIS_CACHE: "OrderedDict[str, str]" = OrderedDict()
_PATH_BUNDLE_CACHE: "OrderedDict[str, str]" = OrderedDict()
_CACHE_MAX = 16


def _normalize_mode(mode: str, default: str) -> str:
    m = (mode or default or "skeleton").strip().lower()
    return m if m in _BUNDLE_MODES else default


def _graph_fingerprint() -> str:
    if not _graph_mtimes:
        return "empty"
    items = sorted(_graph_mtimes.items())
    raw = f"{len(items)}|{repr(items)}".encode("utf-8", errors="replace")
    return hashlib.sha256(raw).hexdigest()[:12]


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
    from .files import read_file

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


def resolve_context(task: str, mode: str = "", max_chars: int = 0) -> str:
    """Resolve a coding task and return a budget-aware Connected Context Pack.

    Default mode is ``skeleton``: root targets are read (capped per file); related
    files show symbols/imports only. Use ``mode=full`` for all bodies, or
    ``mode=manifest`` for a path list only (then ``read_bundle`` on key paths).
    """
    mode = _normalize_mode(mode, BUNDLE_DEFAULT_MODE)
    cap = max_chars if max_chars > 0 else CONTEXT_BUNDLE_MAX_CHARS

    cache_key = hashlib.sha256(
        f"resolve|{task}|{mode}|{cap}|{_graph_fingerprint()}".encode()
    ).hexdigest()[:20]
    hit = _cache_get(_CONTEXT_ANALYSIS_CACHE, cache_key)
    if hit is not None:
        return hit

    try:
        graph = _get_or_build_graph()
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
        graph=_graph,
    )
    _cache_put(_PATH_BUNDLE_CACHE, cache_key, result)
    return result
