"""API-key provider connection status (separate from OAuth login)."""
from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Literal

from ...constants.api_keys import api_key_spec

ApiConnectionSource = Literal["env", "file", "none"]


@dataclass(frozen=True)
class ApiConnectionStatus:
    connected: bool
    source: ApiConnectionSource
    detail: str


def api_connection_status(spec_id: str) -> ApiConnectionStatus:
    key_spec = api_key_spec(spec_id)
    if not key_spec:
        return ApiConnectionStatus(False, "none", "unknown provider")

    env_var = key_spec["env_var"]
    file_path = key_spec["file_path"]
    if os.getenv(env_var):
        suffix = os.getenv(env_var, "")[-6:]
        return ApiConnectionStatus(True, "env", f"env {env_var} …{suffix}")
    try:
        if file_path.exists() and file_path.read_text().strip():
            suffix = file_path.read_text().strip()[-6:]
            return ApiConnectionStatus(True, "file", f"file {file_path.name} …{suffix}")
    except OSError:
        pass
    return ApiConnectionStatus(False, "none", "not configured")
