"""Project graph — persistent map of source files, imports/exports, dependencies.

Builds a compact JSON file (.jarvis-graph.json) in the project root.
The model reads this at the start of coding tasks to navigate the project
without expensive search_code/glob_files calls, saving thousands of tokens.

Auto-updated after write_file/edit_file operations so the graph stays current
across the session without full rescans.
"""
from __future__ import annotations

import json
import os
import re
import time
import subprocess
from pathlib import Path

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
    ".jarvis-graph.json", "package-lock.json", "yarn.lock",
    "pnpm-lock.yaml", ".DS_Store",
}

GRAPH_FILE = ".jarvis-graph.json"

# Max number of files to scan (cap for large projects)
MAX_SCAN = 500

# ---------------------------------------------------------------------------
# Import / export parsers (regex-based, lightweight)
# ---------------------------------------------------------------------------

_TS_IMPORT = re.compile(
    r"""import\s+(?:\{[^}]*\}|[^;{]+)\s+from\s+['\"]([^'\"]+)['\"]""",
)
_TS_REQUIRE = re.compile(r"""require\s*\(\s*['\"]([^'\"]+)['\"]""")
_TS_EXPORT = re.compile(
    r"""export\s+(?:default\s+)?(?:function|class|const|let|var|interface|type|enum)\s+(\w+)""",
)
_TS_EXPORT_NAMED = re.compile(r"""export\s+\{([^}]+)\}""")
# Strip comments for cleaner parsing
_COMMENT_STRIP = re.compile(r"""//.*$|/\*.*?\*/""", re.MULTILINE | re.DOTALL)


def _scan_ts(text: str) -> tuple[list[str], list[str]]:
    clean = _COMMENT_STRIP.sub("", text)
    imports = []
    seen = set()
    for m in _TS_IMPORT.finditer(clean):
        p = m.group(1)
        if p not in seen:
            imports.append(p)
            seen.add(p)
    for m in _TS_REQUIRE.finditer(clean):
        p = m.group(1)
        if p not in seen:
            imports.append(p)
            seen.add(p)
    exports = []
    for m in _TS_EXPORT.finditer(clean):
        exports.append(m.group(1))
    for m in _TS_EXPORT_NAMED.finditer(clean):
        for name in m.group(1).split(","):
            name = name.strip().split(" as ")[0].strip()
            if name:
                exports.append(name)
    return imports[:12], exports[:8]  # cap


_PY_IMPORT = re.compile(r"""^\s*(?:from\s+(\S+)\s+)?import\s+(.+)$""", re.MULTILINE)
_PY_COMMENT = re.compile(r"""#.*$""", re.MULTILINE)


def _scan_py(text: str) -> tuple[list[str], list[str]]:
    # Strip comments first
    clean = _PY_COMMENT.sub("", text)
    imports = []
    seen = set()
    for m in _PY_IMPORT.finditer(clean):
        if m.group(1):
            p = m.group(1)
            if p not in seen:
                imports.append(p)
                seen.add(p)
        for name in m.group(2).split(","):
            name = name.strip().strip("(").strip()
            if name and name != "*" and name not in seen:
                imports.append(name)
                seen.add(name)
    exports = []
    _PY_DEF = re.compile(r"""^(?:async\s+)?(?:def|class)\s+(\w+)""", re.MULTILINE)
    for m in _PY_DEF.finditer(text):
        pos = m.start()
        line_start = text.rfind("\n", 0, pos)
        if line_start == -1 or not text[line_start + 1 : pos].strip():
            exports.append(m.group(1))
    return imports[:12], exports[:8]


_RS_USE = re.compile(r"""use\s+(\S+)""")
_RS_PUB = re.compile(r"""pub\s+(?:fn|struct|enum|trait|const|type|mod|use)\s+(\w+)""")


def _scan_rs(text: str) -> tuple[list[str], list[str]]:
    imports = [m.group(1) for m in _RS_USE.finditer(text)][:12]
    exports = [m.group(1) for m in _RS_PUB.finditer(text)][:8]
    return imports, exports


_GO_IMPORT = re.compile(r"""['"]([^'"]+)['"]""")
_GO_FUNC = re.compile(r"""^func\s+(\w+)""", re.MULTILINE)


def _scan_go(text: str) -> tuple[list[str], list[str]]:
    imports = []
    imports.extend(m.group(1) for m in _GO_IMPORT.finditer(text))

    imports = [i for i in imports if "/" in i or "." in i or i == "fmt"][:12]
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

def _detect_lang(all_files: list) -> str:
    exts = {f.suffix for f in all_files if hasattr(f, "suffix")}
    if not exts and isinstance(all_files, dict):
        # tree dict instead of Path list
        exts = {Path(k).suffix for k in all_files}
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
    dirs = {d.name for d in root.iterdir() if d.is_dir()}

    # Read package.json for node projects
    deps: dict = {}
    pkg = root / "package.json"
    if pkg.exists():
        try:
            data = json.loads(pkg.read_text())
            deps = {**data.get("dependencies", {}), **data.get("devDependencies", {})}
        except Exception:
            pass

    if "expo" in deps or "expo-env.d.ts" in files:
        return "expo"
    if "react-native" in deps or "react-native.config.js" in files or "metro.config.js" in files:
        return "react-native"
    if "next" in deps or "next.config.js" in files or "next.config.mjs" in files:
        return "next.js"
    if "nest" in deps or "nest-cli.json" in files:
        return "nestjs"
    if "django" in deps or (root / "manage.py").exists():
        return "django"
    if "flask" in deps:
        return "flask"
    if (root / "Cargo.toml").exists():
        return "rust"
    if (root / "go.mod").exists():
        return "go"
    return "unknown"


# ---------------------------------------------------------------------------
# Clean import paths: strip node_modules, shorten absolute paths
# ---------------------------------------------------------------------------

def _clean_import_path(imp: str) -> str:
    """Strip unnecessary prefix cruft from import paths."""
    imp = imp.strip()
    # Strip node_modules/ prefix
    if imp.startswith("node_modules/"):
        imp = imp[len("node_modules/"):]
    # Strip workspace root prefixes
    for prefix in ("@/", "~/", "src/"):
        if imp.startswith(prefix):
            break  # these are fine, keep them
    return imp


# ---------------------------------------------------------------------------
# Main build
# ---------------------------------------------------------------------------

def build(project_root: str | Path | None = None) -> dict:
    """Full project scan — build graph from scratch.

    Returns the graph dict (also written to .jarvis-graph.json).
    """
    root = Path(project_root or os.getcwd()).resolve()
    if not root.exists():
        return {"error": f"project root {root} does not exist"}

    total_files = 0
    scanned_files: dict[str, dict] = {}
    dir_tree: dict[str, list[str]] = {}

    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS and not d.startswith(".")]

        rel_dir = Path(dirpath).relative_to(root).as_posix()
        if rel_dir == ".":
            rel_dir = ""

        # Record directory entries
        if rel_dir:
            parts = rel_dir.split("/")
            for i in range(len(parts)):
                parent = "/".join(parts[:i]) if i > 0 else ""
                child = parts[i]
                if child not in dir_tree.setdefault(parent, []):
                    dir_tree[parent].append(child)
        else:
            # Root dir — collect top-level entries
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

            # Record file in dir tree
            if rel_dir not in dir_tree:
                dir_tree[rel_dir] = []
            if fn not in dir_tree[rel_dir]:
                dir_tree[rel_dir].append(fn)

            # Parse source files
            ext = fp.suffix
            if ext in PARSE_EXTS:
                imports, exports = _scan_file(fp)
                if imports or exports:
                    entry = {}
                    if exports:
                        entry["e"] = exports
                    if imports:
                        # shorten import paths
                        entry["i"] = sorted(set(_clean_import_path(p) for p in imports))
                    scanned_files[relpath] = entry

        if total_files > MAX_SCAN:
            break

    lang = _detect_lang(list(scanned_files.keys()))
    fw = _detect_framework(root)

    # Find entry points
    entry_names = {"index.ts", "index.tsx", "index.js", "App.tsx", "App.ts",
                   "App.js", "main.py", "main.go", "main.rs", "main.ts"}
    entries = sorted(
        relpath for relpath in scanned_files
        if Path(relpath).name in entry_names
    )

    # Package dependencies (top 20)
    pkg_deps = {}
    pkg = root / "package.json"
    if pkg.exists():
        try:
            data = json.loads(pkg.read_text())
            all_deps = {}
            all_deps.update(data.get("dependencies", {}))
            all_deps.update(data.get("devDependencies", {}))
            # Only keep top 20 most relevant
            for k in list(all_deps.keys())[:20]:
                pkg_deps[k] = all_deps[k]
        except Exception:
            pass

    graph: dict = {
        "name": root.name,
        "lang": lang,
        "fw": fw,
        "files": total_files,
        "scanned": len(scanned_files),
        "entries": entries,
        "dirs": dict(sorted(dir_tree.items())),
        "tree": dict(sorted(scanned_files.items())),
        "deps": pkg_deps,
        "ts": int(time.time()),
    }
    return graph


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
    """Return existing graph or build a new one."""
    if not rebuild:
        existing = read(root)
        if existing:
            return existing
    g = build(root)
    write(g, root)
    return g


# ---------------------------------------------------------------------------
# Incremental update after file changes
# ---------------------------------------------------------------------------

def update_after_change(changed_files: list[str], root: Path | str | None = None) -> dict:
    """Fast partial update — only rescans changed files, leaves the rest intact."""
    root = root or Path.cwd()
    graph = read(root) or build(root)

    for relpath in changed_files:
        fp = root / relpath
        if fp.exists() and fp.suffix in PARSE_EXTS:
            imports, exports = _scan_file(fp)
            if imports or exports:
                entry: dict = {}
                if exports:
                    entry["e"] = exports
                if imports:
                    entry["i"] = sorted(set(_clean_import_path(p) for p in imports))
                graph.setdefault("tree", {})[relpath] = entry
            elif relpath in graph.get("tree", {}):
                del graph["tree"][relpath]
        elif relpath in graph.get("tree", {}):
            del graph["tree"][relpath]

    graph["ts"] = int(time.time())
    write(graph, root)
    return graph


# ---------------------------------------------------------------------------
# Tool-callable functions
# ---------------------------------------------------------------------------

# Cache the project root so we don't recompute it on every tool call
_project_root: str | None = None


def _get_root() -> Path:
    global _project_root
    if _project_root is None:
        _project_root = str(Path.cwd().resolve())
    return Path(_project_root)


def tool_read_project_graph(rebuild: bool = False) -> str:
    """Read or build the project graph. Returns formatted output for the model."""
    root = _get_root()
    graph = ensure(root, rebuild=rebuild)
    if "error" in graph:
        return f"Error: {graph['error']}"

    # Format as compact text for the model (more token-efficient than raw JSON)
    lines = [
        f"📁 {graph['name']}  |  {graph['lang']}  |  {graph['fw']}",
        f"   {graph['scanned']} source files scanned (of {graph['files']} total)",
    ]

    # Entry points
    if graph.get("entries"):
        lines.append(f"   entry: {', '.join(graph['entries'])}")

    # Directory structure
    lines.append("")
    lines.append("── dirs ──")
    dirs = graph.get("dirs", {})
    # Build a tree-like view from flat dir dict
    for parent in sorted(dirs, key=lambda x: (x.count("/"), x)):
        children = dirs[parent]
        indent = "  " * (parent.count("/") + 1) if parent else ""
        label = parent.split("/")[-1] if parent else "."
        files_only = [c for c in children if "." in c]
        dirs_only = [c for c in children if "/" in c or "." not in c]
        parts = []
        if dirs_only:
            parts.append(f"{len(dirs_only)} subdirs")
        if files_only:
            parts.append(f"{len(files_only)} files")
        lines.append(f"{indent}{label}/  ({'; '.join(parts)})")

    # Scanned files with exports/imports
    tree = graph.get("tree", {})
    if tree:
        lines.append("")
        lines.append("── key files ──")
        # Show files in directory order
        for dirname in sorted(dirs, key=lambda x: (x.count("/"), x)):
            for fname in sorted(dirs.get(dirname, [])):
                if "." not in fname:
                    continue  # skip subdirs
                relpath = f"{dirname}/{fname}" if dirname else fname
                entry = tree.get(relpath)
                if entry:
                    indent = "  " * (dirname.count("/") + 1)
                    parts = []
                    if entry.get("e"):
                        parts.append(f"→ {', '.join(entry['e'][:4])}")
                    if entry.get("i"):
                        parts.append(f"from {', '.join(entry['i'][:4])}")
                    lines.append(f"{indent}{fname}  {'  '.join(parts)}")

    # Key dependencies
    if graph.get("deps"):
        lines.append("")
        lines.append(f"── deps ({len(graph['deps'])}) ──")
        deps_list = sorted(graph["deps"].keys())[:12]
        lines.append(f"   {', '.join(deps_list)}")

    return "\n".join(lines)


def tool_update_project_graph(files: list[str] | None = None) -> str:
    """Update graph after file changes. Pass list of changed file paths."""
    root = _get_root()
    if files:
        graph = update_after_change(files, root)
        return f"Graph updated for {len(files)} changed file(s). {graph.get('scanned', 0)} files tracked."
    else:
        # Full rebuild
        graph = build(root)
        write(graph, root)
        return f"Graph rebuilt. {graph.get('scanned', 0)} source files scanned."
