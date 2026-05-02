"""Project graph — persistent map of source files, imports/exports, dependencies.

Builds a compact JSON file (.project-graph.json) in the project root.
The model reads this ONCE at the start of coding tasks to navigate the project
without expensive search_code/glob_files/rank_files calls — saving thousands of tokens.

Auto-updated after write_file/edit_file operations via _auto_update_graph()
so the graph stays current across the session without full rescans.
"""
from __future__ import annotations

import json
import os
import re
import time
from pathlib import Path

from ..constants import CWD

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

PARSE_EXTS = {
    ".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs",
    ".py", ".rs", ".go", ".swift", ".kt", ".java",
    ".vue", ".svelte",
}

SKIP_DIRS = {
    "node_modules", ".git", "__pycache__", ".venv", "venv",
    "build", "dist", ".next", ".expo", "android", "ios",
    ".mypy_cache", ".pytest_cache", ".ruff_cache", ".cache",
    "coverage", ".turbo", ".nx", ".gradle", "Pods",
    "bundle", ".serverless", ".storybook",
}

SKIP_FILES = {
    ".project-graph.json", "package-lock.json", "yarn.lock",
    "pnpm-lock.yaml", ".DS_Store",
}

GRAPH_FILE = ".project-graph.json"

# Cap for large projects — keeps build fast and graph compact
MAX_SCAN = 1000

# Max files to stat-check for staleness (just mtime, no reading — very fast)
STALE_CHECK_CAP = 200

# Files that look like entry points
ENTRY_NAMES = {
    "index.ts", "index.tsx", "index.js", "App.tsx", "App.ts",
    "App.js", "main.py", "main.go", "main.rs", "main.ts",
}

# ---------------------------------------------------------------------------
# Parsers (regex-based, lightweight)
# ---------------------------------------------------------------------------

_TS_IMPORT = re.compile(
    r"""import\s+(?:\{[^}]*\}|[^;{]+)\s+from\s+['"]([^'"]+)['"]""",
)
_TS_REQUIRE = re.compile(r"""require\s*\(\s*['"]([^'"]+)['"]""")
_TS_EXPORT = re.compile(
    r"""export\s+(?:default\s+)?(?:function|class|const|let|var|interface|type|enum)\s+(\w+)""",
)
_TS_EXPORT_NAMED = re.compile(r"""export\s+\{([^}]+)\}""")
_COMMENT_STRIP = re.compile(r"""//.*$|/\*.*?\*/""", re.MULTILINE | re.DOTALL)

_PY_IMPORT = re.compile(r"""^\s*(?:from\s+(\S+)\s+)?import\s+(.+)$""", re.MULTILINE)
_PY_COMMENT = re.compile(r"""#.*$""", re.MULTILINE)

_RS_USE = re.compile(r"""^\s*use\s+([^;{]+)""", re.MULTILINE)
_RS_MOD = re.compile(r"""^\s*(?:pub\s+)?mod\s+(\w+)""", re.MULTILINE)
_RS_PUB_FN = re.compile(r"""^\s*pub\s+fn\s+(\w+)""", re.MULTILINE)
_RS_PUB_STRUCT = re.compile(r"""^\s*pub\s+struct\s+(\w+)""", re.MULTILINE)

_GO_IMPORT = re.compile(r"""['"](\S+)['"]""")
_GO_FUNC = re.compile(r"""^func\s+(\w+)""", re.MULTILINE)


def _scan_ts(text: str) -> tuple[list[str], list[str]]:
    clean = _COMMENT_STRIP.sub("", text)
    imports, seen = [], set()
    for m in _TS_IMPORT.finditer(clean):
        p = m.group(1)
        if p not in seen:
            imports.append(p); seen.add(p)
    for m in _TS_REQUIRE.finditer(clean):
        p = m.group(1)
        if p not in seen:
            imports.append(p); seen.add(p)
    exports = [m.group(1) for m in _TS_EXPORT.finditer(clean)]
    for m in _TS_EXPORT_NAMED.finditer(clean):
        for name in m.group(1).split(","):
            name = name.strip().split(" as ")[0].strip()
            if name: exports.append(name)
    return imports[:12], exports[:8]


def _scan_py(text: str) -> tuple[list[str], list[str]]:
    clean = _PY_COMMENT.sub("", text)
    imports, seen = [], set()
    for m in _PY_IMPORT.finditer(clean):
        p = m.group(1) or m.group(2).strip().split()[0]
        if p and p not in seen:
            imports.append(p); seen.add(p)
    # Python "exports" = top-level class/def names
    exports = []
    for m in re.finditer(r"""^\s*(?:async\s+)?(?:def|class)\s+(\w+)""", clean, re.MULTILINE):
        exports.append(m.group(1))
    return imports[:12], exports[:8]


def _scan_rs(text: str) -> tuple[list[str], list[str]]:
    imports = [m.group(1).strip() for m in _RS_USE.finditer(text)][:12]
    exports = []
    exports.extend(m.group(1) for m in _RS_PUB_FN.finditer(text))
    exports.extend(m.group(1) for m in _RS_PUB_STRUCT.finditer(text))
    return imports, exports[:8]


def _scan_go(text: str) -> tuple[list[str], list[str]]:
    imports = [m.group(1) for m in _GO_IMPORT.finditer(text) if "/" in m.group(1) or "." in m.group(1) or m.group(1) == "fmt"][:12]
    exports = [m.group(1) for m in _GO_FUNC.finditer(text) if m.group(1)[0].isupper()][:8]
    return imports, exports


def _scan_file(path: Path) -> tuple[list[str], list[str]]:
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except Exception:
        return [], []
    ext = path.suffix
    if ext in (".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs", ".vue", ".svelte"):
        return _scan_ts(text)
    if ext == ".py":
        return _scan_py(text)
    if ext == ".rs":
        return _scan_rs(text)
    if ext == ".go":
        return _scan_go(text)
    return [], []


# ---------------------------------------------------------------------------
# Framework / language detection
# ---------------------------------------------------------------------------

def _detect_lang(exts: set[str]) -> str:
    if ".tsx" in exts or ".ts" in exts: return "TypeScript"
    if ".py" in exts: return "Python"
    if ".jsx" in exts or ".js" in exts: return "JavaScript"
    if ".rs" in exts: return "Rust"
    if ".go" in exts: return "Go"
    if ".swift" in exts: return "Swift"
    if ".kt" in exts: return "Kotlin"
    if ".java" in exts: return "Java"
    return "Unknown"


def _detect_framework(root: Path) -> str:
    files = {f.name for f in root.iterdir() if f.is_file()}
    deps: dict = {}
    pkg = root / "package.json"
    if pkg.exists():
        try:
            data = json.loads(pkg.read_text())
            deps = {**data.get("dependencies", {}), **data.get("devDependencies", {})}
        except Exception:
            pass
    if "expo" in deps or "expo-env.d.ts" in files: return "expo"
    if "react-native" in deps or "metro.config.js" in files: return "react-native"
    if "next" in deps or "next.config.js" in files or "next.config.mjs" in files: return "next.js"
    if "nest" in deps or "nest-cli.json" in files: return "nestjs"
    if (root / "manage.py").exists(): return "django"
    if (root / "Cargo.toml").exists(): return "rust"
    if (root / "go.mod").exists(): return "go"
    return "unknown"


def _clean_import_path(imp: str) -> str:
    imp = imp.strip()
    if imp.startswith("node_modules/"):
        imp = imp[len("node_modules/"):]
    return imp


# ---------------------------------------------------------------------------
# Build
# ---------------------------------------------------------------------------

def build(project_root: str | Path | None = None) -> dict:
    """Full project scan — build graph from scratch. Returns graph dict."""
    root = Path(project_root or os.getcwd()).resolve()
    if not root.exists():
        return {"error": f"project root {root} does not exist"}

    total_files = 0
    scanned_files: dict[str, dict] = {}
    dir_tree: dict[str, list[str]] = {}
    exts: set[str] = set()
    scanned_count = 0

    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS and not d.startswith(".")]
        rel_dir = Path(dirpath).relative_to(root).as_posix()
        if rel_dir == ".":
            rel_dir = ""

        # Register directories
        if rel_dir:
            parts = rel_dir.split("/")
            for i in range(len(parts)):
                parent = "/".join(parts[:i]) if i > 0 else ""
                child = parts[i]
                if child not in dir_tree.setdefault(parent, []):
                    dir_tree[parent].append(child)
        else:
            for d in dirnames:
                if d not in dir_tree.setdefault("", []):
                    dir_tree[""].append(f"{d}/")

        for fn in filenames:
            if fn in SKIP_FILES:
                continue
            total_files += 1
            if total_files > MAX_SCAN:
                break

            fp = Path(dirpath) / fn
            relpath = f"{rel_dir}/{fn}" if rel_dir else fn
            ext = fp.suffix
            exts.add(ext)

            # Record in dir tree
            if rel_dir not in dir_tree:
                dir_tree[rel_dir] = []
            if fn not in dir_tree[rel_dir]:
                dir_tree[rel_dir].append(fn)

            if ext in PARSE_EXTS:
                imports, exports = _scan_file(fp)
                if imports or exports:
                    entry: dict = {}
                    if exports:
                        entry["e"] = exports
                    if imports:
                        entry["i"] = sorted(set(_clean_import_path(p) for p in imports))
                    scanned_files[relpath] = entry
                    scanned_count += 1

        if total_files > MAX_SCAN:
            break

    lang = _detect_lang(exts)
    fw = _detect_framework(root)

    # Entry points
    entries = sorted(
        relpath for relpath in scanned_files
        if Path(relpath).name in ENTRY_NAMES
    )

    # Package deps (top 20)
    pkg_deps = {}
    pkg = root / "package.json"
    if pkg.exists():
        try:
            data = json.loads(pkg.read_text())
            all_deps = {**data.get("dependencies", {}), **data.get("devDependencies", {})}
            for k in list(all_deps.keys())[:20]:
                pkg_deps[k] = all_deps[k]
        except Exception:
            pass

    return {
        "name": root.name,
        "lang": lang,
        "fw": fw,
        "files": total_files,
        "scanned": scanned_count,
        "entries": entries,
        "dirs": dict(sorted(dir_tree.items())),
        "tree": dict(sorted(scanned_files.items())),
        "deps": pkg_deps,
        "ts": int(time.time()),
    }


# ---------------------------------------------------------------------------
# Staleness check — auto-rebuild if source files changed externally
# ---------------------------------------------------------------------------

def _is_stale(graph: dict, root: Path) -> bool:
    """Quick mtime check — if any source file is newer than graph timestamp, rebuild."""
    graph_ts = graph.get("ts", 0)
    checked = 0
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS and not d.startswith(".")]
        for fn in filenames:
            if fn in SKIP_FILES:
                continue
            ext = Path(fn).suffix
            if ext not in PARSE_EXTS:
                continue
            checked += 1
            fp = Path(dirpath) / fn
            try:
                if fp.stat().st_mtime > graph_ts:
                    return True
            except OSError:
                continue
            if checked >= STALE_CHECK_CAP:
                return False  # Hit cap without finding newer file — assume fresh
    return False


# ---------------------------------------------------------------------------
# Read / Write / Ensure
# ---------------------------------------------------------------------------

def _graph_path(root: Path | str | None = None) -> Path:
    r = Path(root) if root else Path.cwd()
    return r.resolve() / GRAPH_FILE


def write(graph: dict, root: Path | str | None = None) -> Path:
    path = _graph_path(root)
    path.write_text(json.dumps(graph, indent=2, ensure_ascii=False))
    return path


def read(root: Path | str | None = None) -> dict | None:
    path = _graph_path(root)
    if path.exists():
        try:
            return json.loads(path.read_text())
        except Exception:
            return None
    return None


def ensure(root: Path | str | None = None, rebuild: bool = False) -> dict:
    """Return existing graph or build & write a new one.

    If a graph exists but source files have been modified externally (e.g. user
    edited in VSCode), it auto-rebuilds — no stale data returned.
    """
    if not rebuild:
        existing = read(root)
        r = Path(root or os.getcwd()).resolve()
        if existing and not _is_stale(existing, r):
            return existing
    g = build(root)
    write(g, root)
    return g


# ---------------------------------------------------------------------------
# Incremental update after file changes
# ---------------------------------------------------------------------------

def update_after_change(changed_files: list[str], root: Path | str | None = None) -> dict:
    """Fast partial update — only rescans changed files, leaves the rest intact.

    Handles new files (adds to dir tree + scans), deletions (removes from both
    tree and dir tree), and modifications (rescans, updates entry).
    """
    root_path = Path(root or os.getcwd()).resolve()
    graph = read(root_path) or build(root_path)

    tree = graph.setdefault("tree", {})
    dirs = graph.setdefault("dirs", {})
    changed = False

    for relpath in changed_files:
        fp = root_path / relpath
        rel_dir = str(Path(relpath).parent)
        if rel_dir == ".":
            rel_dir = ""
        fname = Path(relpath).name

        if fp.exists():
            # New or modified file
            # Ensure directory exists in dir tree
            if rel_dir not in dirs:
                # Add all parent dirs
                if rel_dir:
                    parts = rel_dir.split("/")
                    for i in range(len(parts)):
                        parent = "/".join(parts[:i]) if i > 0 else ""
                        child = parts[i]
                        if child not in dirs.setdefault(parent, []):
                            dirs[parent].append(child)
                else:
                    if "" not in dirs:
                        dirs[""] = []
                if rel_dir not in dirs:
                    dirs[rel_dir] = []
            if fname not in dirs.get(rel_dir, []):
                dirs.setdefault(rel_dir, []).append(fname)
                changed = True

            # Rescan if it's a parseable source file
            if fp.suffix in PARSE_EXTS:
                imports, exports = _scan_file(fp)
                if imports or exports:
                    entry: dict = {}
                    if exports:
                        entry["e"] = exports
                    if imports:
                        entry["i"] = sorted(set(_clean_import_path(p) for p in imports))
                    tree[relpath] = entry
                elif relpath in tree:
                    del tree[relpath]
                changed = True
        else:
            # Deleted file
            if relpath in tree:
                del tree[relpath]
                changed = True
            # Clean up dir tree
            if rel_dir in dirs and fname in dirs[rel_dir]:
                dirs[rel_dir].remove(fname)
                changed = True

    if changed:
        graph["ts"] = int(time.time())
        write(graph, root_path)

    return graph


# ---------------------------------------------------------------------------
# Tool-callable functions
# ---------------------------------------------------------------------------

def _get_root() -> Path:
    """Return the project root — uses the runtime CWD."""
    return Path(os.getcwd()).resolve()


def tool_read_project_graph(rebuild: bool = False) -> str:
    """Read or rebuild the project graph. Returns compact formatted text."""
    root = _get_root()
    graph = ensure(root, rebuild=rebuild)
    if "error" in graph:
        return f"Error: {graph['error']}"

    lines = [
        f"📁 {graph['name']}  ({graph['lang']} / {graph['fw']})  |  {graph['scanned']} source files (of {graph['files']} total)",
    ]

    if graph.get("entries"):
        lines.append(f"   entries: {', '.join(graph['entries'])}")

    # Directory tree — compact one-line per directory
    lines.append("")
    lines.append("── dirs ──")
    dirs = graph.get("dirs", {})
    for parent in sorted(dirs, key=lambda x: (x.count("/"), x)):
        children = dirs[parent]
        indent = "  " * (parent.count("/") + 1) if parent else ""
        label = parent.split("/")[-1] if parent else "."
        files_only = [c for c in children if "." in c]
        subdirs_only = [c for c in children if "/" in c or "." not in c]
        parts = []
        if subdirs_only:
            parts.append(f"{len(subdirs_only)} dirs")
        if files_only:
            parts.append(f"{len(files_only)} files")
        if parts:
            lines.append(f"{indent}{label}/  ({'; '.join(parts)})")

    # Key files with exports/imports — grouped by directory, compact
    tree = graph.get("tree", {})
    if tree:
        lines.append("")
        lines.append("── files ──")
        for dirname in sorted(dirs, key=lambda x: (x.count("/"), x)):
            for fname in sorted(dirs.get(dirname, [])):
                if "." not in fname:
                    continue
                relpath = f"{dirname}/{fname}" if dirname else fname
                entry = tree.get(relpath)
                if entry:
                    indent = "  " * (dirname.count("/") + 1) if dirname else ""
                    parts = []
                    if entry.get("e"):
                        parts.append(f"→ {', '.join(entry['e'][:4])}")
                    if entry.get("i"):
                        parts.append(f"<- {', '.join(entry['i'][:4])}")
                    lines.append(f"{indent}{fname}  {'  '.join(parts)}")

    # Dependencies
    if graph.get("deps"):
        deps = sorted(graph["deps"].keys())[:12]
        lines.append("")
        lines.append(f"── deps ({len(graph['deps'])}) ──")
        lines.append(f"   {', '.join(deps)}")

    return "\n".join(lines)


def tool_update_project_graph(files: list[str] | None = None) -> str:
    """Update graph after file changes. Pass list of changed file paths."""
    root = _get_root()
    if files:
        graph = update_after_change(files, root)
        return f"Graph updated for {len(files)} file(s). {graph.get('scanned', 0)} files tracked."
    else:
        graph = build(root)
        write(graph, root)
        return f"Graph rebuilt. {graph.get('scanned', 0)} files tracked."


# ---------------------------------------------------------------------------
# Auto-update hook — called from files.py after write/edit
# ---------------------------------------------------------------------------

def _auto_update_graph(file_path: str, root: Path | None = None) -> None:
    """Silently update the graph after a write/edit operation.

    Called by write_file() and edit_file() in files.py.
    Single-file incremental update — fast, no full rescan.
    If no graph exists yet, does nothing (graph will be built on first read).
    """
    try:
        r = root or _get_root()
        graph = read(r)
        if graph is None:
            return  # No graph yet — read_project_graph will build it later
        update_after_change([file_path], r)
    except Exception:
        pass  # Never break the write/edit flow
