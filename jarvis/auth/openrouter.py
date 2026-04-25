"""OpenRouter API key prompt / load. Mirrors api_key.py but for OpenRouter."""
import os, sys
from getpass import getpass

from ..console import console, Panel
from ..constants import OPENROUTER_KEY_FILE
from ..utils.io import _secure_write


def prompt_for_openrouter_key(reason: str = "") -> str:
    if reason:
        console.print(f"[red]{reason}[/]")
    console.print(Panel(
        "[bold yellow]OpenRouter API key needed[/]\n\n"
        "Get one at: https://openrouter.ai/keys\n"
        "Must start with [cyan]sk-or-[/]\n\n"
        f"Saved to: [dim]{OPENROUTER_KEY_FILE}[/] (chmod 600)",
        title="Setup · OpenRouter key", border_style="yellow"
    ))
    key = getpass("Paste sk-or- key (hidden): ").strip()
    if not key.startswith("sk-or-"):
        console.print("[red]Key must start with sk-or-[/]"); sys.exit(1)
    _secure_write(OPENROUTER_KEY_FILE, key)
    console.print("[green]✓ OpenRouter key saved[/]")
    return key


def load_openrouter_key() -> str:
    if os.getenv("OPENROUTER_API_KEY"):
        return os.environ["OPENROUTER_API_KEY"]
    if OPENROUTER_KEY_FILE.exists():
        k = OPENROUTER_KEY_FILE.read_text().strip()
        if k.startswith("sk-or-"):
            return k
    return prompt_for_openrouter_key()
