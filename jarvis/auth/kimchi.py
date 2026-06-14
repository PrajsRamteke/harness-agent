"""Kimchi API key prompt / load — BYO API key for llm.kimchi.dev."""
import os, sys

from ..console import console, Panel
from ..constants import KIMCHI_KEY_FILE
from ..utils.io import _secure_write


def prompt_for_kimchi_key(reason: str = "") -> str:
    if reason:
        console.print(f"[red]{reason}[/]")
    console.print(Panel(
        "[bold yellow]Kimchi API key needed[/]\n\n"
        "Get one at: https://kimchi.dev\n"
        "Paste your Kimchi API key (Bearer token) below.\n\n"
        f"Saved to: [dim]{KIMCHI_KEY_FILE}[/] (chmod 600)",
        title="Setup · Kimchi key", border_style="yellow"
    ))
    key = console.input("Paste Kimchi API key: ").strip()
    if not key:
        console.print("[red]Key cannot be empty[/]"); sys.exit(1)
    _secure_write(KIMCHI_KEY_FILE, key)
    console.print("[green]✓ Kimchi API key saved[/]")
    return key


def has_kimchi_key() -> bool:
    if os.getenv("KIMCHI_API_KEY"):
        return True
    try:
        return KIMCHI_KEY_FILE.exists() and bool(KIMCHI_KEY_FILE.read_text().strip())
    except OSError:
        return False


def load_kimchi_key() -> str:
    if os.getenv("KIMCHI_API_KEY"):
        return os.environ["KIMCHI_API_KEY"]
    if KIMCHI_KEY_FILE.exists():
        k = KIMCHI_KEY_FILE.read_text().strip()
        if k:
            return k
    return prompt_for_kimchi_key()
