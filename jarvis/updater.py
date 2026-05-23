"""Auto-update: pull from origin + editable pip install on startup."""
from __future__ import annotations

import pathlib
import subprocess

from .install_sync import (
    find_install_root,
    harness_agent_models_available,
    pip_install_repo,
)


def _git(*args: str, cwd: pathlib.Path, timeout: int = 30) -> tuple[int, str]:
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


def check_and_update() -> dict | None:
    """Fetch + pull when behind, then ``pip install -e .``. Returns update info."""
    try:
        root = find_install_root()
        if root is None:
            return None

        code, old_head = _git("rev-parse", "HEAD", cwd=root, timeout=10)
        if code != 0 or not old_head:
            return None

        code, _ = _git("fetch", "origin", cwd=root, timeout=30)
        if code != 0:
            return None

        code, upstream = _git(
            "rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{u}",
            cwd=root, timeout=10,
        )
        if code != 0 or not upstream:
            for candidate in ("origin/main", "origin/master"):
                c, _ = _git("rev-parse", candidate, cwd=root, timeout=10)
                if c == 0:
                    upstream = candidate
                    break
            else:
                return None

        code, remote_head = _git("rev-parse", upstream, cwd=root, timeout=10)
        if code != 0 or not remote_head or remote_head == old_head:
            return None

        code, behind_str = _git(
            "rev-list", "--count", f"HEAD..{upstream}",
            cwd=root, timeout=10,
        )
        behind = int(behind_str) if code == 0 and behind_str.isdigit() else 0
        if behind == 0:
            return None

        code, log_out = _git(
            "log", "--oneline", f"HEAD..{upstream}",
            cwd=root, timeout=10,
        )
        new_commits = [line.strip() for line in log_out.splitlines() if line.strip()]

        code, _ = _git("pull", "--ff-only", cwd=root, timeout=60)
        if code != 0:
            return None

        pip_ok = pip_install_repo(root)
        if not pip_ok:
            # Retry once — friend installs often hit transient pip errors.
            pip_ok = pip_install_repo(root, timeout=240)

        result = {
            "updated": True,
            "count": behind,
            "commits": new_commits,
            "pip_installed": pip_ok,
            "pip_ok": pip_ok,
            "harness_models": harness_agent_models_available(),
        }

        import jarvis.state as _state
        _state.update_result = result
        return result

    except Exception:
        return None
