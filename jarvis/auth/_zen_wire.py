"""OpenCode Zen / Harness Agent free-tier wire material (internal).

The free tier is reached with a fixed *public* bearer plus a synthetic OpenCode
client identity at ``https://opencode.ai/inference/v1`` (OpenCode moved the free
endpoint from ``/zen/v1`` to ``/inference/v1`` in Oct 2026 — the old path now
hard-403s every chat/completions request). The gateway validates several things,
so keep these in sync with the real OpenCode client on each release:

  1. ``User-Agent`` must be ``opencode/<semver>`` — a bare ``opencode`` is
     rejected with 403 ``FreeTierError``.
  2. The advertised version must be >= the server's enforced minimum, otherwise
     the request fails with 426: "OpenCode <min> or newer is required to use the
     free tier" (minimum was 1.18.0 as of 2026-10; 1.18.32 stays valid).
  3. ``x-opencode-session`` must be a well-formed id:
     ``ses_`` + 12 lowercase-hex chars + 14 alphanumeric chars = 30 chars total.
     Wrong shape -> 403 ``FreeTierError`` ("...can only be used from within
     OpenCode"). As of OpenCode 1.19.0 (2026-10) the gateway ALSO decodes the
     first 12 hex chars as a timestamp and rejects ids whose segment doesn't
     decode to a recent time -> 403 ``FreeTierError``. A random hex segment
     (what this client used before) now fails; see
     :func:`_session_timestamp_segment` for the exact encoding. The trailing
     14 chars are random and not checked against any registry.

  4. The request body's ``tools`` array must contain tool definitions named
     exactly ``bash`` and ``read`` (case-sensitive). The gateway uses these two
     OpenCode built-ins as its client-identity fingerprint — a request carrying
     neither (or only one) is rejected with 403 ``FreeTierError``, while any
     request that includes both passes regardless of the other tools present.
     Jarvis's equivalents are ``run_bash`` / ``read_file``, so
     :func:`harness_agent.build_harness_agent_client` injects two alias schemas
     via ``gate_tools``; ``repl.render`` maps the names back to the real
     handlers.

The other ``x-opencode-*`` headers mirror what the real CLI sends. As of the
``/inference/v1`` move both ``x-opencode-session`` and ``x-opencode-session-id``
are load-bearing (the same session id in each).

Internal module — not part of the public API. Do not expose the wire recipe to
end users; the Harness Agent tier is meant to be consumed through this app.
"""
from __future__ import annotations

import secrets
import time

# The free tier's shared bearer. Not a secret credential — the tier is gated by
# client identity + rate limits, not by this value.
ZEN_PUBLIC_KEY = "public"

# Server requires a versioned User-Agent at or above the enforced minimum.
# Bump this when OpenCode raises the floor (server currently enforces >= 1.18.0).
OPENCODE_CLIENT_VERSION = "1.19.0"
OPENCODE_USER_AGENT = f"opencode/{OPENCODE_CLIENT_VERSION}"

SESSION_PREFIX = "ses_"
REQUEST_ID_PREFIX = "msg_"
REQUEST_ID_HEADER = "x-opencode-request"

# Free-tier models the gateway serves through the OpenAI Responses API
# (``/inference/v1/responses``) instead of ``/chat/completions``. These are the
# catalog entries whose ``provider.npm`` is ``@ai-sdk/openai``; every other
# free model uses the default OpenAI-compatible (chat/completions) API.
# Posting a Responses-only model to /chat/completions returns 500. The client
# adds models.dev's live answer (``native_responses_models``); this set only
# matters while models.dev is off.
RESPONSES_API_MODELS = frozenset({
    "muse-spark-1.2-contributor-free",
    "muse-spark-1.3-contributor-free",
})

# Session ids: "ses_" + 12 lowercase hex + 14 alnum.
_SESSION_HEX_LEN = 12
_SESSION_RANDOM_LEN = 14

# OpenCode's identifier alphabet (packages/schema/src/identifier.ts). The
# random tail picks each char as ``alphabet[byte % 62]``.
_SESSION_ALPHABET = "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz"


def _session_timestamp_segment(timestamp_ms: int | None = None) -> str:
    """The 12-hex leading segment of an OpenCode *descending* identifier.

    Mirrors ``Identifier.descending()`` (OpenCode 1.19.0,
    ``packages/schema/src/identifier.ts``): the id value is
    ``~(timestamp_ms * 0x1000 + counter)`` and the segment is its low 48 bits
    rendered big-endian as 12 hex chars. The gateway decodes this timestamp
    and 403s (``FreeTierError``) a session whose segment doesn't look recent,
    so the old random-hex segment no longer works.
    """
    ts = int(time.time() * 1000) if timestamp_ms is None else int(timestamp_ms)
    value = ~(ts * 0x1000)  # descending: bitwise-not of the millisecond clock
    return f"{value & 0xFFFFFFFFFFFF:012x}"


def session_id(body: str) -> str:
    """Prefix *body* with the session marker.

    *body* must be 26 chars: 12 lowercase hex followed by 14 alphanumerics.
    Use :func:`new_session_id` unless you need a deterministic id (tests).
    """
    return f"{SESSION_PREFIX}{body}"


def new_session_id() -> str:
    """Generate a wire-valid session id (``ses_`` + 12 hex + 14 alnum).

    The 12 hex chars encode the current time in OpenCode's descending-id
    format; the 14-char tail is random. Both are required by the gateway.
    """
    return _new_identifier(SESSION_PREFIX)


def new_message_id() -> str:
    """Generate a wire-valid message/request id (``msg_`` + 12 hex + 14 alnum).

    Same descending-identifier shape as :func:`new_session_id`. OpenCode's
    ``x-opencode-request`` header is validated too: a bare ``msg_1`` (a plain
    counter) is 403'd ``FreeTierError``, while a real ``msg_<descending>`` id
    passes (Oct 2026). The CLI sends one per request.
    """
    return _new_identifier(REQUEST_ID_PREFIX)


def _new_identifier(prefix: str) -> str:
    rand = "".join(
        _SESSION_ALPHABET[byte % 62]
        for byte in secrets.token_bytes(_SESSION_RANDOM_LEN)
    )
    return f"{prefix}{_session_timestamp_segment()}{rand}"


def zen_client_kwargs(session: str) -> dict:
    return {
        "api_key": ZEN_PUBLIC_KEY,
        "default_headers": {
            "User-Agent": OPENCODE_USER_AGENT,
            "x-opencode-client": "cli",
            "x-opencode-project": "global",
            "x-opencode-session": session,
            # OpenCode added this alongside x-opencode-session; the /inference/v1
            # gateway 403s (FreeTierError) when it's absent (Oct 2026).
            "x-opencode-session-id": session,
        },
        "request_id_header": REQUEST_ID_HEADER,
        "request_id_prefix": REQUEST_ID_PREFIX,
        # OpenCode validates the request id shape too: a counter (msg_1) 403s,
        # so generate a fresh descending identifier per request.
        "request_id_factory": new_message_id,
    }
