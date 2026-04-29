"""Code search via ripgrep/grep."""
import shlex, subprocess
from ..constants import SEARCH_MATCH_CAP
from ..path_resolve import project_scope_error, robust_resolve
from .shell import run_bash


_SKIP_GLOBS = [
    "node_modules", ".venv", "venv", "__pycache__", ".git", "dist", "build",
    ".next", ".mypy_cache", ".pytest_cache", ".ruff_cache",
]


def search_code(pattern: str, path: str = ".", allow_outside_project: bool = False) -> str:
    root = robust_resolve(path)
    if not allow_outside_project:
        scope_err = project_scope_error(root, "search_code", "allow_outside_project=true")
        if scope_err:
            return scope_err
    try:
        cmd_path = str(root.relative_to(robust_resolve("."))) or "."
    except ValueError:
        cmd_path = str(root)
    has_rg = subprocess.run("which rg", shell=True, capture_output=True).returncode == 0
    if has_rg:
        # ripgrep respects .gitignore by default; add explicit globs for safety.
        skips = " ".join(f"-g '!{d}'" for d in _SKIP_GLOBS)
        cmd = f"rg -n --max-count {SEARCH_MATCH_CAP} {skips} {shlex.quote(pattern)} {shlex.quote(cmd_path)}"
    else:
        excludes = " ".join(f"--exclude-dir={d}" for d in _SKIP_GLOBS)
        cmd = f"grep -rn --max-count={SEARCH_MATCH_CAP} {excludes} {shlex.quote(pattern)} {shlex.quote(cmd_path)}"
    return run_bash(cmd, 20)
