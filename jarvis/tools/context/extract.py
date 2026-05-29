"""Source scanning + language extraction for the Connected Context Pack.

Pure, stateless helpers: filesystem walking, Python/JS-TS symbol & import
extraction, and the indexes used to resolve imports and find siblings/tests.
No module-level mutable state lives here — see ``graph`` for that.
"""
import ast
import os
import pathlib
import re
from collections import defaultdict
from typing import Dict, List, Optional, Set, Tuple

from ...constants import CWD
from ..dirs import SKIP_DIRS

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
_JS_EXTS = {".js", ".jsx", ".ts", ".tsx", ".mjs", ".cjs", ".mts", ".cts"}
_TEST_FILE_PATTERNS = re.compile(r"(?:^|[/\\])(?:test_|.*?_test|.*?\.spec|.*?\.test)\.", re.I)
_CONFIG_FILE_PATTERNS = re.compile(r"(?:^|[/\\])\.?(?:env|.*?config|\.env\..*|docker-compose|dockerfile)", re.I)
_ROUTE_FILE_PATTERNS = re.compile(r"(?:^|[/\\])(?:routes?|controllers?|endpoints?|views?|pages?|api)", re.I)

# Precompiled regexes for JS/TS extraction (hot path during graph build) —
# compiled once instead of re-parsed per file.
_JS_IMPORT_STRING_RE = re.compile(r"""['"]([./][^'"]+)['"]""")
_JS_REQUIRE_RE = re.compile(r"""require\s*\(\s*['"]([./][^'"]+)['"]\s*\)""")
_JS_FUNCTION_RE = re.compile(r"""(?:export\s+)?(?:async\s+)?function\s+(\w+)""")
_JS_CONST_FN_RE = re.compile(r"""(?:export\s+)?(?:const|let|var)\s+(\w+)\s*[:=]\s*(?:async\s*)?\(""")
_JS_ARROW_RE = re.compile(r"""(?:export\s+)?(?:const|let|var)\s+(\w+)\s*=\s*(?:async\s*)?=>""")
_JS_CLASS_RE = re.compile(r"""(?:export\s+)?(?:abstract\s+)?class\s+(\w+)""")
_JS_INTERFACE_RE = re.compile(r"""(?:export\s+)?interface\s+(\w+)""")
_JS_TYPE_RE = re.compile(r"""(?:export\s+)?type\s+(\w+)""")

# Content-keyword extraction
_KW_STRIP_RE = re.compile(r""""[^"]*"|'[^']*'|#[^\n]*""")
_KW_WORD_RE = re.compile(r'[A-Z][a-z]+(?=[A-Z]|$|[a-z])|[a-z]+|[A-Z][a-z]*')

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


# =============================================================================
#  FILE SCANNING & FILTERING
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


# =============================================================================
#  IMPORT RESOLUTION
# =============================================================================

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


# =============================================================================
#  INDEXING & SIBLING/TEST DISCOVERY
# =============================================================================

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


# =============================================================================
#  SYMBOL EXTRACTION (Python AST, JS/TS regex)
# =============================================================================

def _extract_annotation_name(node) -> Optional[str]:
    """Extract the top-level name from a type annotation node."""
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Subscript):
        return _extract_annotation_name(node.value)
    if isinstance(node, ast.Attribute):
        return node.attr
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
    for m in _JS_IMPORT_STRING_RE.finditer(source):
        resolved = _find_js_import(m.group(1), filepath)
        if resolved:
            imports.append(resolved)
    # require('...')
    for m in _JS_REQUIRE_RE.finditer(source):
        resolved = _find_js_import(m.group(1), filepath)
        if resolved:
            imports.append(resolved)
    return list(set(imports))


def _extract_js_symbols(source: str) -> Tuple[List[str], List[str]]:
    """Extract (symbols, types) from a JS/TS file using regex."""
    symbols: List[str] = []
    types: List[str] = []

    for m in _JS_FUNCTION_RE.finditer(source):
        symbols.append(m.group(1))
    for m in _JS_CONST_FN_RE.finditer(source):
        symbols.append(m.group(1))
    for m in _JS_ARROW_RE.finditer(source):
        symbols.append(m.group(1))
    for m in _JS_CLASS_RE.finditer(source):
        symbols.append(m.group(1))
    for m in _JS_INTERFACE_RE.finditer(source):
        types.append(m.group(1))
    for m in _JS_TYPE_RE.finditer(source):
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
    if ext in _JS_EXTS:
        imports = _extract_js_imports(source, f)
        symbols, types = _extract_js_symbols(source)
        return rel, imports, symbols, types
    return rel, [], [], []


def _extract_content_keywords(source: str, max_words: int = 30) -> List[str]:
    """Extract important keywords from source for matching."""
    # Remove strings and comments
    text = _KW_STRIP_RE.sub("", source)
    # Find camelCase/PascalCase words
    words = _KW_WORD_RE.findall(text)
    # Filter stopwords
    stopwords = {"the", "this", "that", "and", "for", "from", "import", "function",
                 "class", "const", "let", "var", "return", "export", "default",
                 "async", "await", "if", "else", "try", "catch", "new", "type",
                 "interface", "extends", "implements", "true", "false", "null",
                 "undefined", "void", "number", "string", "boolean", "any",
                 "never", "unknown", "object", "array", "tuple", "enum"}
    return [w for w in words if w.lower() not in stopwords and len(w) > 1][:max_words]
