"""Re-install the package after git pull so non-editable installs pick up new code."""
from __future__ import annotations

import pathlib
import subprocess
import sys


def repo_python(repo_root: pathlib.Path) -> str:
    venv_py = repo_root / ".venv" / "bin" / "python"
    if venv_py.is_file():
        return str(venv_py)
    return sys.executable


def pip_install_repo(repo_root: pathlib.Path, *, timeout: int = 180) -> bool:
    """Run ``pip install .`` in the repo (matches scripts/install)."""
    try:
        r = subprocess.run(
            [repo_python(repo_root), "-m", "pip", "install", "."],
            cwd=str(repo_root),
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        return r.returncode == 0
    except Exception:
        return False
