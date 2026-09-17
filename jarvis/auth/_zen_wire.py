"""OpenCode Zen / Harness Agent free-tier wire material (internal).

The free tier is reached with a fixed *public* bearer plus a synthetic OpenCode
client identity. As of 2026-09 the gateway validates three things, so keep these
in sync with the real OpenCode client when it ships a new release:

  1. ``User-Agent`` must be ``opencode/<semver>`` — a bare ``opencode`` is
     rejected with 403 ``FreeTierError``.
  2. The advertised version must be >= the server's enforced minimum, otherwise
     the request fails with 426: "OpenCode <min> or newer is required to use the
     free tier" (minimum was 1.17.0 as of 2026-09).
  3. ``x-opencode-session`` must be a well-formed id:
     ``ses_`` + 12 lowercase-hex chars + 14 alphanumeric chars = 30 chars total.
     Wrong shape -> 403 ``FreeTierError`` ("...can only be used from within
     OpenCode"). The trailing 14 chars are not checked against any registry.

The other ``x-opencode-*`` headers mirror what the real CLI sends; only
``x-opencode-session`` is actually load-bearing.

Internal module — not part of the public API. Do not expose the wire recipe to
end users; the Harness Agent tier is meant to be consumed through this app.
"""
from __future__ import annotations

import secrets
import uuid

# The free tier's shared bearer. Not a secret credential — the tier is gated by
# client identity + rate limits, not by this value.
ZEN_PUBLIC_KEY = "public"

# Server requires a versioned User-Agent at or above the enforced minimum.
# Bump this when OpenCode raises the floor (server currently enforces >= 1.17.0).
OPENCODE_CLIENT_VERSION = "1.18.30"
OPENCODE_USER_AGENT = f"opencode/{OPENCODE_CLIENT_VERSION}"

SESSION_PREFIX = "ses_"
REQUEST_ID_PREFIX = "msg_"
REQUEST_ID_HEADER = "x-opencode-request"

# Session ids: "ses_" + 12 lowercase hex + 14 alnum.
_SESSION_HEX_LEN = 12
_SESSION_RANDOM_LEN = 14


def session_id(body: str) -> str:
    """Prefix *body* with the session marker.

    *body* must be 26 chars: 12 lowercase hex followed by 14 alphanumerics.
    Use :func:`new_session_id` unless you need a deterministic id (tests).
    """
    return f"{SESSION_PREFIX}{body}"


def new_session_id() -> str:
    """Generate a wire-valid session id (``ses_`` + 12 hex + 14 alnum)."""
    body = (
        uuid.uuid4().hex[:_SESSION_HEX_LEN]
        + secrets.token_hex(_SESSION_RANDOM_LEN // 2)
    )
    return session_id(body)


def zen_client_kwargs(session: str) -> dict:
    return {
        "api_key": ZEN_PUBLIC_KEY,
        "default_headers": {
            "User-Agent": OPENCODE_USER_AGENT,
            "x-opencode-client": "cli",
            "x-opencode-project": "global",
            "x-opencode-session": session,
        },
        "request_id_header": REQUEST_ID_HEADER,
        "request_id_prefix": REQUEST_ID_PREFIX,
    }
