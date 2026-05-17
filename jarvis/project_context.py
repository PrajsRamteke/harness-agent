"""Project instruction file discovery and coding-project detection.

Discovery records the file path only. Content is loaded on demand through the
normal read_file tool when repository instructions are relevant.

Coding-project detection checks for tell-tale files in the current directory
that indicate a software project (package.json, src/, etc.).
"""
from __future__ import annotations

import pathlib
from pathlib import Path

from . import state


PROJECT_CONTEXT_FILES = ("AGENTS.md", "AGENT.md", "CLAUDE.md", "JARVIS.md")

# Single file that strongly indicates a coding project.
_CODING_STRONG = frozenset({
    "package.json",       # Node / JS / TS
    "pyproject.toml",     # Python (PEP 621)
    "go.mod",             # Go
    "Cargo.toml",         # Rust
    "Gemfile",            # Ruby
    "composer.json",      # PHP
    "pubspec.yaml",       # Dart / Flutter
    "build.gradle",       # Gradle (Java / Kotlin)
    "build.gradle.kts",   # Gradle Kotlin DSL
    "pom.xml",            # Maven (Java)
    "CMakeLists.txt",     # C / C++ (CMake)
    "Makefile",           # Generic build
    "Dockerfile",         # Containerised project
    "stack.yaml",         # Haskell Stack
    "mix.exs",            # Elixir
    "Project.toml",       # Julia
    "Cabal",              # Haskell
    "*.csproj",           # C# / .NET (checked specially)
    "*.xcodeproj",        # Xcode (checked specially)
    "*.xcworkspace",      # Xcode workspace
})

# Files / dirs that suggest a coding project but are less definitive alone.
_CODING_MODERATE = frozenset({
    "src",                # source directory
    "lib",                # library directory
    "index.html",         # web entry
    "requirements.txt",   # Python deps
    "setup.py",           # Python package
    "setup.cfg",          # Python config
    "tsconfig.json",      # TypeScript config
    "eslint.config.js",   # ESLint flat config
    ".eslintrc.js",       # ESLint (legacy)
    ".eslintrc.json",
    "vite.config.ts",     # Vite
    "vite.config.js",
    "next.config.js",     # Next.js
    "next.config.ts",
    "tailwind.config.js",  # Tailwind
    "tailwind.config.ts",
    "webpack.config.js",  # Webpack
    ".prettierrc",        # Prettier
    ".prettierrc.json",
    ".gitignore",         # Git-tracked project
    "go.sum",             # Go deps lock
    "yarn.lock",          # Yarn lock
    "pnpm-lock.yaml",     # PNPM lock
    "bun.lock",           # Bun lock
    "runtime.toml",       # Deno
    "deno.json",
    ".node-version",      # Node version pin
    ".nvmrc",
    "app.json",           # Expo / React Native
})


def detect_project_context(cwd: str | Path | None = None) -> bool:
    """Detect a project context file without reading its content."""
    root = Path(cwd or Path.cwd())
    for filename in PROJECT_CONTEXT_FILES:
        path = root / filename
        if path.is_file():
            state.project_context_file = filename
            state.project_context_path = str(path)
            state.project_context_content = ""
            return True

    state.project_context_file = ""
    state.project_context_path = ""
    state.project_context_content = ""
    return False


def detect_coding_project(cwd: str | pathlib.Path | None = None) -> bool:
    """Check whether *cwd* (or the current working directory) looks like a
    software/coding project by scanning for well-known project files.

    Returns ``True`` if at least one **strong** indicator is found, or at
    least **two** moderate indicators are found.
    """
    root = Path(cwd or Path.cwd())
    if not root.is_dir():
        return False

    strong_hits = 0
    moderate_hits = 0

    # Prepare glob-style checks for patterns like *.csproj
    try:
        for entry in root.iterdir():
            name = entry.name

            if name in _CODING_STRONG:
                strong_hits += 1
                continue

            if name in _CODING_MODERATE:
                moderate_hits += 1
                continue

            # Extension-based strong checks
            if entry.is_file():
                if name.endswith(".csproj") or name.endswith(".xcodeproj") or name.endswith(".xcworkspace"):
                    strong_hits += 1
                    continue

    except PermissionError:
        return False

    return strong_hits >= 1 or moderate_hits >= 2
