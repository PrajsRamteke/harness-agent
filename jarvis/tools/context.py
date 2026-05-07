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
import pathlib
import re
import time
import tokenize
import io
from collections import defaultdict
from typing import Dict, List, Optional, Set, Tuple

from ..constants import CWD, CONTEXT_BUNDLE_MAX_CHARS
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

    Uses Path.walk() (Python 3.12+) instead of rglob so we can PRUNE
    hidden/skip dirs at traversal time instead of iterating every file inside them.
    """
    files = []
    for root_dir, dirs, file_names in root.walk():
        # Prune skip dirs in-place so walk() never descends into them
        # (must match _SKIP_DIR_NAMES and SKIP_DIRS from dirs.py)
        pruned = []
        for d in dirs:
            if d in _SKIP_DIR_NAMES:
                continue
            pruned.append(d)
        dirs[:] = pruned

        for name in file_names:
            p = root_dir / name
            ext = p.suffix.lower()
            if ext in _SKIP_EXTS:
                continue
            if ext in _CODE_EXTS:
                files.append(p)
            elif _CONFIG_FILE_PATTERNS.search(name):
                files.append(p)
    return files


def _resolve_local_import(import_name: str, source_file: pathlib.Path) -> Optional[str]:
    """Resolve a Python import name to a relative file path within the project.

    e.g. "jarvis.tools.context" -> "jarvis/tools/context.py"
         ".tools.context"       -> resolved relative to source_file
    """
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


def _extract_python_imports(source: str, filepath: pathlib.Path) -> List[str]:
    """Extract local imports from a Python file using AST."""
    imports: List[str] = []
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return imports

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                resolved = _resolve_local_import(alias.name, filepath)
                if resolved:
                    imports.append(resolved)
        elif isinstance(node, ast.ImportFrom):
            if node.module:
                resolved = _resolve_local_import(node.module, filepath)
                if resolved:
                    imports.append(resolved)
    return imports


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


def _extract_python_symbols(source: str) -> Tuple[List[str], List[str]]:
    """Extract (symbols, types) from a Python file using AST."""
    symbols: List[str] = []
    types: List[str] = []
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return symbols, types

    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            symbols.append(node.name)
        elif isinstance(node, ast.ClassDef):
            symbols.append(node.name)
            # Check for TypeVar, TypedDict, etc.
            for base in node.bases:
                if isinstance(base, ast.Name) and base.id in {"TypedDict", "NamedTuple", "Protocol"}:
                    types.append(node.name)
                    break
        elif isinstance(node, ast.Assign):
            # type aliases: X = TypeVar('X'), X = Type[...]
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
                    elif isinstance(val, ast.Subscript):
                        # x: Type[X] — covered by annotations
                        pass

    # Collect type annotations from function signatures and assignments
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            for arg in node.args.args + node.args.kwonlyargs + node.args.posonlyargs:
                if arg.annotation:
                    ann = _extract_annotation_name(arg.annotation)
                    if ann and ann[0].isupper():
                        types.append(ann)
            if node.returns:
                ann = _extract_annotation_name(node.returns)
                if ann and ann[0].isupper():
                    types.append(ann)
        elif isinstance(node, ast.AnnAssign):
            if isinstance(node.target, ast.Name) and node.annotation:
                ann = _extract_annotation_name(node.annotation)
                if ann and ann[0].isupper():
                    types.append(node.target.id)

    return list(set(symbols)), list(set(types))


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


def _find_tests_for_file(rel: str, all_files: List[pathlib.Path]) -> List[str]:
    """Find test files that correspond to a given source file.

    Rules:
      - same basename but in a test/ or __tests__/ dir
      - test_ prefix or _test suffix
      - spec file with same basename
    """
    p = pathlib.Path(rel)
    stem = p.stem  # without extension
    tests: List[str] = []

    for f in all_files:
        f_rel = _rel_path(f)
        f_stem = f.stem
        # test_<name> or <name>_test
        if f_stem == f"test_{stem}" or f_stem == f"{stem}_test":
            tests.append(f_rel)
        elif f_stem == f"{stem}.spec" or f_stem == f"{stem}.test":
            tests.append(f_rel)
        # Same name in __tests__/ or tests/ directory
        parts = set(f.parts)
        if stem == f_stem and ("__tests__" in parts or "tests" in parts):
            if f_rel != rel:
                tests.append(f_rel)
    return tests


def _find_siblings(rel: str, all_files: List[pathlib.Path]) -> List[str]:
    """Find same-folder files excluding self."""
    parent = pathlib.Path(rel).parent
    siblings = []
    for f in all_files:
        f_rel = _rel_path(f)
        if f_rel == rel:
            continue
        if pathlib.Path(f_rel).parent == parent:
            siblings.append(f_rel)
    return siblings


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

    Results are cached globally.
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
        _mtimes = {}
        for f in all_files:
            rel = _rel_path(f)
            try:
                _mtimes[rel] = f.stat().st_mtime
            except OSError:
                _mtimes[rel] = 0.0

    for f in all_files:
        rel = _rel_path(f)
        # If mtimes was passed in, skip re-stat (already done by caller)
        if mtimes is not None and rel in _mtimes:
            pass
        elif rel not in _mtimes:
            try:
                _mtimes[rel] = f.stat().st_mtime
            except OSError:
                _mtimes[rel] = 0.0

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

    # Pass 2: extract imports and symbols per file
    for f in all_files:
        rel = _rel_path(f)
        ext = f.suffix.lower()
        try:
            source = f.read_text(errors="ignore")
        except Exception:
            continue

        if ext == ".py":
            imports = _extract_python_imports(source, f)
            symbols, types = _extract_python_symbols(source)
        elif ext in {".js", ".jsx", ".ts", ".tsx", ".mjs", ".cjs", ".mts", ".cts"}:
            imports = _extract_js_imports(source, f)
            symbols, types = _extract_js_symbols(source)
        else:
            imports, symbols, types = [], [], []

        graph[rel]["imports"] = imports
        graph[rel]["symbols"] = symbols
        graph[rel]["types"] = types

    # Pass 3: populate imported_by (reverse imports)
    for rel, info in graph.items():
        for imp in info["imports"]:
            if imp in graph:
                if rel not in graph[imp]["imported_by"]:
                    graph[imp]["imported_by"].append(rel)

    # Pass 4: tests, siblings, configs, routes
    for rel in list(graph.keys()):
        graph[rel]["tests"] = _find_tests_for_file(rel, all_files)
        graph[rel]["siblings"] = _find_siblings(rel, all_files)

        # Route detection
        if _ROUTE_FILE_PATTERNS.search(rel):
            graph[rel]["routes"] = [rel]

        # Config detection
        if _CONFIG_FILE_PATTERNS.search(rel):
            graph[rel]["configs"] = [rel]

    _graph = graph
    _graph_mtimes = _mtimes
    _graph_root_mtime = time.time()
    return graph


def _get_or_build_graph() -> FileGraph:
    """Return cached graph, rebuilding if stale (single scan even on rebuild)."""
    global _graph, _graph_mtimes

    if _graph is None:
        return build_graph()

    # Scan once. If mtimes match, reuse cache. Otherwise rebuild from same scan.
    files = _scan_source_files(CWD)
    mtimes: Dict[FileRel, float] = {}
    stale = False
    for f in files:
        rel = _rel_path(f)
        try:
            cur = f.stat().st_mtime
        except OSError:
            continue
        mtimes[rel] = cur
        prev = _graph_mtimes.get(rel)
        if prev is None or abs(cur - prev) > 0.001:
            stale = True

    if not stale:
        return _graph  # type: ignore

    # Rebuild from already-scanned file list (avoids a second scan)
    return build_graph(source_files=files, mtimes=mtimes)


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


_CONTEXT_ANALYSIS_CACHE: Dict[str, str] = {}  # task_hash -> bundle_text


def _file_content_safe(rel: str) -> str:
    """Read file content with error handling."""
    full = CWD / rel
    try:
        if not full.exists():
            return "// file not found"
        text = full.read_text(errors="ignore")
        return text
    except Exception as e:
        return f"// error reading file: {e}"


def resolve_context(task: str) -> str:
    """Main tool: resolve a coding task and return ALL connected file contents.

    Call this ONCE instead of making 5-20 separate read_file/search_code calls.
    After receiving the bundle, you have ALL the context you need to plan and edit.

    Args:
        task: Natural-language description of what you need to do.
              Be specific about the feature, component, or bug.
              Examples:
                - "Fix login bug — JWT token not refreshing"
                - "Add user profile edit page"
                - "Create API endpoint for product search"
                - "Refactor auth service to use new middleware"

    Returns:
        Connected Context Pack — a structured bundle of all relevant files.
    """
    # Build graph if needed
    try:
        graph = _get_or_build_graph()
    except Exception as e:
        return f"Error building repo graph: {e}"

    # Resolve target files
    try:
        targets = _resolve_target_files(task, graph)
    except Exception as e:
        targets = []

    if not targets:
        # Fall back to scanning a few key entry points
        for key in ("main", "app", "index", "cli", "router", "__init__"):
            matches = [rel for rel in graph if key in rel.lower()]
            if matches:
                targets = matches[:3]
                break
        if not targets:
            # Last resort: pick up to 5 top-level files
            targets = [rel for rel in sorted(graph.keys()) if "/" not in rel][:5]

    if not targets:
        return "No relevant files found. The repo graph may be empty or the task too vague."

    # Collect connected files
    try:
        connected = _collect_connected_files(targets, graph, **_COLLECTOR_DEFAULTS)
    except Exception as e:
        connected = {t: "root_target" for t in targets}

    # Read all file contents
    lines: List[str] = []
    lines.append("=== Connected Context Pack ===")
    lines.append(f"Task: {task}")
    lines.append(f"Root files: {', '.join(targets)}")
    lines.append(f"Connected files: {len(connected)} total")
    lines.append("")

    # Sort: root targets first, then by relation type
    def _sort_key(item):
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

    sorted_files = sorted(connected.items(), key=_sort_key)

    total_chars = 0
    for rel, relation in sorted_files:
        content = _file_content_safe(rel)
        # Estimate: header + content
        estimated = len(rel) + len(content) + 200
        if total_chars + estimated > CONTEXT_BUNDLE_MAX_CHARS:
            lines.append(f"\n--- {rel} (TRUNCATED: bundle size limit reached) ---")
            break

        lines.append(f"\n--- {rel} (RELATION: {relation}) ---")
        lines.append(content)
        total_chars += estimated

    # Add relationship summary
    lines.append("\n\n=== Relationships ===")
    for rel, relation in sorted_files:
        if relation == "root_target":
            continue
        lines.append(f"  {rel}  ←  {relation}")

    # Add graph stats
    lines.append(f"\n=== Graph: {len(graph)} files indexed ===")
    lines.append(f"=== Bundle: {len(connected)} files, {total_chars} chars (limit: {CONTEXT_BUNDLE_MAX_CHARS}) ===")

    result = "\n".join(lines)
    return result


def read_bundle(paths: List[str]) -> str:
    """Batch-read multiple files at once and return their contents.

    Use this when you already know the exact files you need (from a previous
    resolve_context call or from explicit user mention). Provide up to 20 paths.

    Args:
        paths: List of file paths relative to the project root.

    Returns:
        Contents of all requested files, each prefixed with its path.
    """
    if not paths:
        return "ERROR: no paths provided"

    paths = paths[:20]  # cap at 20
    lines = []
    total_chars = 0

    for path in paths:
        content = _file_content_safe(path)
        estimated = len(path) + len(content) + 100
        if total_chars + estimated > CONTEXT_BUNDLE_MAX_CHARS:
            lines.append(f"\n--- {path} (TRUNCATED: size limit) ---")
            break
        lines.append(f"\n--- {path} ---")
        lines.append(content)
        total_chars += estimated

    return "\n".join(lines)
