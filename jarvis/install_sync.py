"""Keep the running jarvis install in sync with its git checkout."""
from __future__ import annotations

import os
import pathlib
import shutil
import subprocess
import sys


def find_install_root() -> pathlib.Path | None:
    """Locate the harness-agent git checkout backing this install."""
    candidates: list[pathlib.Path] = []

    # Editable install: jarvis/*.py live directly under the repo.
    here = pathlib.Path(__file__).resolve().parent
    for parent in [here, *here.parents[:14]]:
        if (parent / ".git").is_dir() and (parent / "pyproject.toml").is_file():
            candidates.append(parent)

    # Standard install: `jarvis` symlink → ~/.local/share/harness-agent/.venv/bin/jarvis
    try:
        jarvis_bin = shutil.which("jarvis")
        if jarvis_bin:
            resolved = pathlib.Path(jarvis_bin).resolve()
            for repo in (resolved.parent.parent, resolved.parent.parent.parent):
                if (repo / ".git").is_dir() and (repo / "pyproject.toml").is_file():
                    candidates.insert(0, repo)
    except Exception:
        pass

    seen: set[pathlib.Path] = set()
    for candidate in candidates:
        try:
            candidate = candidate.resolve()
        except Exception:
            continue
        if candidate in seen:
            continue
        seen.add(candidate)
        if (candidate / "jarvis" / "cli.py").is_file():
            return candidate
    return None


def running_python() -> str:
    """Python interpreter actually executing jarvis right now."""
    return sys.executable


def pip_install_repo(repo_root: pathlib.Path, *, timeout: int = 180) -> bool:
    """Editable install into the *running* venv so git pull = live code after restart."""
    try:
        r = subprocess.run(
            [running_python(), "-m", "pip", "install", "-e", "."],
            cwd=str(repo_root),
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        return r.returncode == 0
    except Exception:
        return False


def harness_agent_models_available() -> bool:
    """True when this process can list free Harness Agent models."""
    try:
        from .constants.providers import harness_agent_models_for_picker
        return len(harness_agent_models_for_picker()) >= 3
    except Exception:
        return False


def reexec_jarvis(*, update_banner: dict | None = None) -> None:
    """Replace this process so Python reloads modules from disk."""
    if update_banner:
        import json
        os.environ["HARNESS_UPDATE_RESULT"] = json.dumps(update_banner)
    os.environ["HARNESS_UPDATED_REEXEC"] = "1"
    os.execv(sys.executable, [sys.executable, *sys.argv])
