"""Auto-update: silently pull new commits from origin on startup.

Runs in a background thread started by cli.main(). Result is stored in
state.update_result and consumed by repl/banners.py welcome_banner().
"""
from __future__ import annotations

import pathlib
import subprocess

from .install_sync import pip_install_repo


def _git(*args: str, cwd: pathlib.Path, timeout: int = 10) -> tuple[int, str]:
    try:
        r = subprocess.run(
            ["git", *args],
            cwd=cwd,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        return r.returncode, r.stdout.strip()
    except Exception:
        return 1, ""


def _find_git_root() -> pathlib.Path | None:
    p = pathlib.Path(__file__).resolve().parent
    for _ in range(10):
        if (p / ".git").exists():
            return p
        p = p.parent
    return None


def check_and_update() -> None:
    """Fetch + pull if behind. Stores result in jarvis.state.update_result."""
    try:
        root = _find_git_root()
        if root is None:
            return

        code, old_head = _git("rev-parse", "HEAD", cwd=root, timeout=5)
        if code != 0 or not old_head:
            return

        code, _ = _git("fetch", "origin", cwd=root, timeout=15)
        if code != 0:
            return

        # Resolve the upstream tracking branch.
        code, upstream = _git(
            "rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{u}",
            cwd=root, timeout=5,
        )
        if code != 0 or not upstream:
            for candidate in ("origin/main", "origin/master"):
                c, _ = _git("rev-parse", candidate, cwd=root, timeout=5)
                if c == 0:
                    upstream = candidate
                    break
            else:
                return

        code, remote_head = _git("rev-parse", upstream, cwd=root, timeout=5)
        if code != 0 or not remote_head or remote_head == old_head:
            return

        code, behind_str = _git(
            "rev-list", "--count", f"HEAD..{upstream}",
            cwd=root, timeout=5,
        )
        behind = int(behind_str) if code == 0 and behind_str.isdigit() else 0
        if behind == 0:
            return

        code, log_out = _git(
            "log", "--oneline", f"HEAD..{upstream}",
            cwd=root, timeout=5,
        )
        new_commits = [l.strip() for l in log_out.splitlines() if l.strip()]

        code, _ = _git("pull", "--ff-only", cwd=root, timeout=30)
        if code != 0:
            return

        pip_ok = pip_install_repo(root)

        import jarvis.state as _state
        _state.update_result = {
            "count": behind,
            "commits": new_commits,
            "pip_installed": pip_ok,
        }

    except Exception:
        pass
