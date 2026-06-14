"""Pure slash-command sniffers — detect bare modal-opening commands.

Extracted from ``tui/app.py``. These are side-effect-free string predicates
used to route a typed command to the matching picker/modal. They are
re-exported from ``tui.app`` for backwards compatibility (tests import some
of them from there).
"""
from __future__ import annotations


def _is_bare_model_command(text: str) -> bool:
    s = (text or "").strip()
    head = s.split(maxsplit=1)[0]
    return head in ("/model", "/mode")


def _is_bare_provider_command(text: str) -> bool:
    s = (text or "").strip()
    if not s.startswith("/provider"):
        return False
    parts = s.split(maxsplit=1)
    return len(parts) == 1


def _is_session_picker_command(text: str) -> bool:
    s = (text or "").strip()
    return s in ("/session", "/sessions", "/session list", "/session ls")


def _is_think_picker_command(text: str) -> bool:
    s = (text or "").strip().lower()
    return s in ("/think", "/think mode", "/think modes", "/think select")


def _is_mcp_modal_command(text: str) -> bool:
    s = (text or "").strip().lower()
    return s in ("/mcp", "/mcps")


def _is_agent_picker_command(text: str) -> bool:
    s = (text or "").strip().lower()
    return s in ("/agent", "/agents")


def _is_skill_picker_command(text: str) -> bool:
    s = (text or "").strip().lower()
    return s in ("/skill", "/skills")


def _is_command_manager_command(text: str) -> bool:
    s = (text or "").strip().lower()
    return s in ("/command", "/commands")


def _is_memory_modal_command(text: str) -> bool:
    s = (text or "").strip().lower()
    return s in ("/memory", "/memories")


def _is_pin_modal_command(text: str) -> bool:
    s = (text or "").strip().lower()
    return s == "/pin"


def _is_lesson_modal_command(text: str) -> bool:
    s = (text or "").strip().lower()
    return s in ("/lesson", "/lessons")


def _is_settings_modal_command(text: str) -> bool:
    s = (text or "").strip().lower()
    return s in ("/settings", "/setting")


def _is_theme_modal_command(text: str) -> bool:
    s = (text or "").strip().lower()
    return s in ("/theme", "/themes")


def _is_provider_hub_command(text: str) -> bool:
    """Provider setup is reached only through the unified ``/provider`` command.

    The old ``/login``, ``/logout``, ``/auth``, ``/key`` aliases were removed —
    everything lives behind ``/provider``.
    """
    s = (text or "").strip().lower()
    return s == "/provider" or s.startswith("/provider ")


def _is_oauth_modal_command(text: str) -> bool:
    return _is_provider_hub_command(text)


def _oauth_modal_title(text: str) -> str:
    return "Provider Setup"


def _is_local_command(text: str) -> bool:
    s = (text or "").strip().lower()
    return s == "/local" or s.startswith("/local ")


def _is_key_command(text: str) -> bool:
    return _is_provider_hub_command(text)
