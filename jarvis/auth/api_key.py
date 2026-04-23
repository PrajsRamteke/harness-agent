"""API key prompt / load."""
import os, sys
from getpass import getpass

from ..console import console, Panel
from ..constants import KEY_FILE
from ..utils.io import _secure_write


def prompt_for_key(reason: str = "") -> str:
    if reason:
        console.print(f"[red]{reason}[/]")
    console.print(Panel(
        "[bold yellow]Anthropic API key needed[/]\n\n"
        "Get one at: https://console.anthropic.com/settings/keys\n"
        "Must start with [cyan]sk-ant-[/]\n\n"
        f"Saved to: [dim]{KEY_FILE}[/] (chmod 600)",
        title="Setup · API key", border_style="yellow"
    ))
    key = getpass("Paste sk-ant- key (hidden): ").strip()
    if not key.startswith("sk-ant-"):
        console.print("[red]Key must start with sk-ant-[/]"); sys.exit(1)
    _secure_write(KEY_FILE, key)
    console.print("[green]✓ API key saved[/]")
    return key


def load_key() -> str:
    if os.getenv("ANTHROPIC_API_KEY"):
        return os.environ["ANTHROPIC_API_KEY"]
    if KEY_FILE.exists():
        k = KEY_FILE.read_text().strip()
        if k.startswith("sk-ant-"):
            return k
    return prompt_for_key()
