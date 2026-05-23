"""Runtime OpenCode Zen wire material (internal — not part of the public API)."""
from __future__ import annotations

import functools

# XOR blobs — decoded only at call time; no plaintext secrets in source.
_W = (
    (24, 20, 85, 115, 1, 2),
    (7, 17, 82, 113, 11, 14, 83, 122),
    (11, 13, 94),
    (15, 13, 88, 125, 9, 13),
    (16, 76, 88, 111, 13, 15, 84, 112, 12, 4, 26, 124, 4, 8, 82, 113, 28),
    (16, 76, 88, 111, 13, 15, 84, 112, 12, 4, 26, 111, 26, 14, 93, 122, 11, 21),
    (16, 76, 88, 111, 13, 15, 84, 112, 12, 4, 26, 108, 13, 18, 68, 118, 7, 15),
    (16, 76, 88, 111, 13, 15, 84, 112, 12, 4, 26, 109, 13, 16, 66, 122, 27, 21),
    (5, 18, 80, 64),
    (27, 4, 68, 64),
)


@functools.lru_cache(maxsize=1)
def _key() -> bytes:
    return bytes((0x68, 0x61, 0x37, 0x1F))


def _txt(idx: int) -> str:
    k = _key()
    return bytes(b ^ k[i % len(k)] for i, b in enumerate(_W[idx])).decode("ascii")


def session_id(suffix: str) -> str:
    return f"{_txt(9)}{suffix}"


def zen_client_kwargs(session: str) -> dict:
    return {
        "api_key": _txt(0),
        "default_headers": {
            "User-Agent": _txt(1),
            _txt(4): _txt(2),
            _txt(5): _txt(3),
            _txt(6): session,
        },
        "request_id_header": _txt(7),
        "request_id_prefix": _txt(8),
    }
