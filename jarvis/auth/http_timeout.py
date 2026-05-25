"""Shared HTTP timeout settings for provider SDK clients."""
from __future__ import annotations

import os

import httpx


def http_read_timeout_seconds(*, openrouter: bool = False) -> float:
    """Seconds to wait between bytes on a streaming HTTP response."""
    env_read = os.getenv("HARNESS_HTTP_READ_TIMEOUT", "").strip()
    if env_read:
        return float(env_read)
    return 240.0 if openrouter else 600.0


def harness_http_timeout(*, openrouter: bool = False) -> httpx.Timeout:
    """Limits how long we wait between bytes on streaming responses."""
    read = http_read_timeout_seconds(openrouter=openrouter)
    connect_default = 30
    c = float(
        os.getenv("HARNESS_HTTP_CONNECT_TIMEOUT", str(connect_default)).strip()
        or str(connect_default)
    )
    return httpx.Timeout(connect=c, read=read, write=c, pool=c)
