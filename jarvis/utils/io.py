"""Secure file writes (chmod 600)."""
import os, pathlib
from ..constants import CONFIG_DIR, FILE_PERMISSION


def _secure_write(path: pathlib.Path, data: str):
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    path.write_text(data)
    try: os.chmod(path, FILE_PERMISSION)
    except OSError: pass
