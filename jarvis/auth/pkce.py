"""PKCE helpers for OAuth authorization-code flow."""
import base64, hashlib, secrets


def _b64url(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def _pkce_pair() -> tuple[str, str, str]:
    """Return ``(code_verifier, code_challenge, state)`` for OAuth authorize."""
    verifier = _b64url(secrets.token_bytes(64))
    challenge = _b64url(hashlib.sha256(verifier.encode()).digest())
    state = _b64url(secrets.token_bytes(32))
    return verifier, challenge, state
