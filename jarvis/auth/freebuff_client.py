"""Freebuff (Codebuff) client — OpenAI-compatible chat with session bootstrap."""
from __future__ import annotations

import uuid
from typing import Any

import httpx
from openai import OpenAI

from ..constants.providers import (
    FREEBUFF_BASE_URL,
    FREEBUFF_DEFAULT_MODEL,
    FREEBUFF_USER_AGENT,
    freebuff_wire,
)
from .opencode_client import _OpenCodeMessages

# Reuse session when the same token + model is selected again (avoids re-bootstrap).
_client_cache: dict[tuple[str, str], "FreebuffClient"] = {}


def clear_freebuff_client_cache() -> None:
    _client_cache.clear()


def get_freebuff_client(auth_token: str, model: str | None = None) -> "FreebuffClient":
    """Return a cached client for (token, model) or bootstrap a new session."""
    slug = (model or FREEBUFF_DEFAULT_MODEL).strip()
    key = (auth_token, slug)
    cached = _client_cache.get(key)
    if cached is not None:
        return cached
    client = FreebuffClient(auth_token, slug)
    _client_cache[key] = client
    return client


def _bootstrap_session(auth_token: str, api_model: str, agent_id: str) -> tuple[str, str]:
    """Create Freebuff instance + agent run; return (instance_id, run_id)."""
    headers = {
        "Authorization": f"Bearer {auth_token}",
        "Content-Type": "application/json",
    }
    with httpx.Client(timeout=30.0) as client:
        session_resp = client.post(
            f"{FREEBUFF_BASE_URL}/freebuff/session",
            headers={**headers, "x-freebuff-model": api_model},
        )
        session_resp.raise_for_status()
        instance_id = session_resp.json()["instanceId"]

        run_resp = client.post(
            f"{FREEBUFF_BASE_URL}/agent-runs",
            headers=headers,
            json={"action": "START", "agentId": agent_id, "ancestorRunIds": []},
        )
        run_resp.raise_for_status()
        run_id = run_resp.json()["runId"]

    return instance_id, run_id


class _FreebuffMessages(_OpenCodeMessages):
    """OpenCode message adapter with Codebuff metadata on every completion."""

    def _create_completion(self, **kwargs: Any):
        owner: FreebuffClient = self._owner  # type: ignore[assignment]
        kwargs["model"] = owner.api_model
        extra_body = dict(kwargs.pop("extra_body", None) or {})
        extra_body["codebuff_metadata"] = {
            "run_id": owner.run_id,
            "client_id": str(uuid.uuid4()),
            "freebuff_instance_id": owner.instance_id,
            "cost_mode": "free",
        }
        extra_body["provider"] = {"allow_fallbacks": True}
        kwargs["extra_body"] = extra_body
        return self._client.chat.completions.create(**kwargs)


class FreebuffClient:
    """Drop-in Anthropic client replacement for the Codebuff Freebuff API."""

    def __init__(self, auth_token: str, model: str | None = None):
        slug = (model or FREEBUFF_DEFAULT_MODEL).strip()
        api_model, agent_id = freebuff_wire(slug)
        self.harness_model = slug
        self.api_model = api_model
        self._agent_id = agent_id
        self._auth_token = auth_token
        self.instance_id, self.run_id = _bootstrap_session(auth_token, api_model, agent_id)
        self._oai = OpenAI(
            api_key=auth_token,
            base_url=f"{FREEBUFF_BASE_URL}/",
            default_headers={"User-Agent": FREEBUFF_USER_AGENT},
        )
        self.messages = _FreebuffMessages(self._oai, owner=self)

    def validate(self) -> bool:
        return bool(self.instance_id and self.run_id)
