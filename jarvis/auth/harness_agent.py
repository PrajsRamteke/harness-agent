"""Free Harness Agent tier — OpenCode Zen with public bearer (no API key)."""
from __future__ import annotations

import uuid

from ..constants import (
    OPENCODE_ZEN_BASE_URL,
    PROVIDER_HARNESS_AGENT,
    PROVIDER_OPENCODE_ZEN,
    is_harness_agent_model,
)
from .opencode_client import OpenCodeClient
from .opencode_zen import has_opencode_zen_key

HARNESS_AGENT_PUBLIC_KEY = "public"


def should_use_harness_agent_client(model: str | None = None, *, source: str = "") -> bool:
    """Free public Zen tier — Harness Agent source, or no Zen key on first run."""
    if source == PROVIDER_OPENCODE_ZEN:
        return False
    if source == PROVIDER_HARNESS_AGENT:
        return True
    from .. import state

    if getattr(state, "harness_agent_free", False) and is_harness_agent_model(
        (model if model is not None else state.MODEL).strip()
    ):
        return True
    m = (model if model is not None else state.MODEL).strip()
    if not is_harness_agent_model(m):
        return False
    return not has_opencode_zen_key()


def build_harness_agent_client() -> OpenCodeClient:
    """OpenCode Zen client with required Harness Agent headers (Bearer public)."""
    session_id = f"ses_{uuid.uuid4().hex[:12]}"
    return OpenCodeClient(
        api_key=HARNESS_AGENT_PUBLIC_KEY,
        base_url=f"{OPENCODE_ZEN_BASE_URL}/",
        default_headers={
            "User-Agent": "opencode",
            "x-opencode-client": "cli",
            "x-opencode-project": "global",
            "x-opencode-session": session_id,
        },
        request_id_header="x-opencode-request",
        request_id_prefix="msg_",
    )
