"""Pollinations.AI — free OpenAI-compatible text API (no API key)."""
from __future__ import annotations

from ..constants import POLLINATIONS_BASE_URL
from .opencode_client import OpenCodeClient

POLLINATIONS_API_KEY = "not-needed"


def build_pollinations_client() -> OpenCodeClient:
    """OpenCodeClient adapter pointed at text.pollinations.ai."""
    return OpenCodeClient(
        api_key=POLLINATIONS_API_KEY,
        base_url=f"{POLLINATIONS_BASE_URL}/",
    )
