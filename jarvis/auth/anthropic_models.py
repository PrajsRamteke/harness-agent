"""Live Anthropic model discovery after a validated API connection."""
from __future__ import annotations

from typing import TYPE_CHECKING

from ..constants import ANTHROPIC_MODELS, ANTHROPIC_AUTH_MODEL_IDS

if TYPE_CHECKING:
    from anthropic import Anthropic


def fetch_anthropic_model_ids(client: "Anthropic") -> list[str]:
    """Return model ids from the Anthropic API, or [] if the call fails."""
    try:
        page = client.models.list(limit=100)
        return [m.id for m in page.data if getattr(m, "id", None)]
    except Exception:
        return []


def sync_anthropic_model_ids(client: "Anthropic") -> list[str]:
    """Fetch live model ids, store on ``state.anthropic_model_ids``, return them."""
    from .. import state

    ids = fetch_anthropic_model_ids(client)
    if ids:
        state.anthropic_model_ids = ids
    return ids


def anthropic_auth_models_for_picker() -> list[tuple[str, str]]:
    """OAuth / Pro-Max models — catalog plus any live ids from the subscription."""
    from .. import state

    static = {m: d for m, d in ANTHROPIC_MODELS}
    rows: list[tuple[str, str]] = []
    seen: set[str] = set()

    for mid in ANTHROPIC_AUTH_MODEL_IDS:
        if mid in static:
            rows.append((mid, static[mid]))
            seen.add(mid)

    for mid in state.anthropic_model_ids or []:
        if mid not in seen:
            rows.append((mid, static.get(mid, mid)))
            seen.add(mid)

    if rows:
        return rows
    return [(mid, static[mid]) for mid in ANTHROPIC_AUTH_MODEL_IDS if mid in static]


def format_anthropic_model_lines(model_ids: list[str]) -> list[str]:
    static = {m: d for m, d in ANTHROPIC_MODELS}
    lines: list[str] = []
    for mid in model_ids:
        desc = static.get(mid, "")
        lines.append(f"{mid} — {desc}" if desc else mid)
    return lines
