"""Code search via ripgrep/grep."""
import shlex, subprocess
from .shell import run_bash


_SKIP_GLOBS = [
    "node_modules", ".venv", "venv", "__pycache__", ".git", "dist", "build",
    ".next", ".mypy_cache", ".pytest_cache", ".ruff_cache",
]


def search_code(pattern: str, path: str = ".") -> str:
    has_rg = subprocess.run("which rg", shell=True, capture_output=True).returncode == 0
    if has_rg:
        # ripgrep respects .gitignore by default; add explicit globs for safety.
        skips = " ".join(f"-g '!{d}'" for d in _SKIP_GLOBS)
        cmd = f"rg -n --max-count 50 {skips} {shlex.quote(pattern)} {shlex.quote(path)}"
    else:
        excludes = " ".join(f"--exclude-dir={d}" for d in _SKIP_GLOBS)
        cmd = f"grep -rn --max-count=50 {excludes} {shlex.quote(pattern)} {shlex.quote(path)}"
    return run_bash(cmd, 20)
