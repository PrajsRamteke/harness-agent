"""The model that writes small side text — commit messages, PR titles and
descriptions, branch names.

By default ("Auto") these come from the chat's own model, exactly as before.
Settings → Model → *Commit text model* lets the user pick another provider and
model just for this work (a cheap fast model while chatting with a big one).

The choice is stored in ``settings.json`` as
``{"side": {"source": "<picker source>", "model": "<model id>"}}`` — both empty
means Auto. ``resolve()`` builds a one-off client on demand (see
``auth.client.build_side_client``) and never touches the session's provider,
model or client, so the chat is unaffected.
"""
from __future__ import annotations

from typing import Any

from .storage.settings import get_settings

# Settings keys (dotted) — the terminal's /settings shows them as raw fields.
SETTING_SOURCE = "side.source"
SETTING_MODEL = "side.model"


def preference() -> tuple[str, str]:
    """``(source, model)``; both empty when Auto."""
    settings = get_settings()
    source = str(settings.get(SETTING_SOURCE) or "").strip()
    model = str(settings.get(SETTING_MODEL) or "").strip()
    if not source or not model:
        return "", ""
    return source, model


def is_auto() -> bool:
    return preference() == ("", "")


def source_label(source: str) -> str:
    """The provider's display name ("Anthropic", "OpenRouter" …)."""
    if not source:
        return ""
    from .constants import MODEL_SOURCE_LABELS, provider_label

    return MODEL_SOURCE_LABELS.get(source) or provider_label(source) or source


def label(source: str = "", model: str = "") -> str:
    """Human label for the chosen model ("Anthropic · Claude Sonnet 5")."""
    if not source and not model:
        source, model = preference()
    if not source or not model:
        return "Auto"
    return f"{source_label(source)} · {str(model).rsplit('/', 1)[-1]}"


def set_preference(source: str, model: str) -> None:
    """Save the choice; empty strings put it back on Auto."""
    get_settings().set(SETTING_SOURCE, str(source or "").strip())
    get_settings().set(SETTING_MODEL, str(model or "").strip())


def resolve() -> tuple[Any, str, str, str]:
    """``(client, model, provider, auth_mode)`` for one side request.

    Auto → the live session's client and model. Otherwise a fresh client for
    the chosen provider; ``auth_mode`` is the *side* provider's mode (the
    session's is irrelevant there). Raises ``RuntimeError`` when the chosen
    provider isn't usable — callers show that and the user can pick Auto again.
    """
    from . import state

    source, model = preference()
    if not source:
        return state.client, state.MODEL, state.provider, state.auth_mode
    if state.client is not None and source == state.provider and model == state.MODEL:
        return state.client, state.MODEL, state.provider, state.auth_mode
    from .auth.client import build_side_client

    client, provider, auth_mode = build_side_client(source, model)
    return client, model, provider, auth_mode


def public() -> dict[str, Any]:
    """The web Settings row / picker needs: what's chosen, and a label."""
    source, model = preference()
    return {
        "source": source,
        "model": model,
        "auto": not source,
        "label": label(source, model),
        "source_label": source_label(source),
        "model_label": str(model).rsplit("/", 1)[-1] if model else "",
    }
