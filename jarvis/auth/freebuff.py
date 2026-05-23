"""Freebuff (Codebuff) auth — token from `npx freebuff login` credentials file."""
from __future__ import annotations

import json
import os
import sys

from ..console import console, Panel

CREDENTIALS_FILE = os.path.expanduser("~/.config/manicode/credentials.json")


def has_freebuff_credentials() -> bool:
    if os.getenv("FREEBUFF_AUTH_TOKEN", "").strip():
        return True
    try:
        with open(CREDENTIALS_FILE, encoding="utf-8") as f:
            data = json.load(f)
        return bool(data.get("default", {}).get("authToken"))
    except (OSError, json.JSONDecodeError, TypeError, AttributeError):
        return False


def load_freebuff_token() -> str:
    env = os.getenv("FREEBUFF_AUTH_TOKEN", "").strip()
    if env:
        return env
    if not os.path.exists(CREDENTIALS_FILE):
        raise RuntimeError(
            "Freebuff not logged in — run: npx freebuff login "
            "(opens browser; saves token to ~/.config/manicode/credentials.json)"
        )
    with open(CREDENTIALS_FILE, encoding="utf-8") as f:
        data = json.load(f)
    token = (data.get("default") or {}).get("authToken", "")
    if not token:
        raise RuntimeError(
            "Freebuff credentials file exists but has no authToken — run: npx freebuff login"
        )
    return token


def prompt_for_freebuff_login(reason: str = "") -> None:
    """Print login instructions. Freebuff auth is browser-only via the npm CLI."""
    if reason:
        console.print(f"[red]{reason}[/]")
    console.print(Panel(
        "[bold yellow]Freebuff login required[/]\n\n"
        "Run in a terminal (opens browser — GitHub/Google/email):\n"
        "  [cyan]npx freebuff login[/]\n\n"
        "Or: [cyan]npm install -g freebuff && freebuff login[/]\n\n"
        f"Token saved to: [dim]{CREDENTIALS_FILE}[/]\n"
        "Override with env [cyan]FREEBUFF_AUTH_TOKEN[/] if needed.",
        title="Setup · Freebuff", border_style="yellow",
    ))
    sys.exit(1)
