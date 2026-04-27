"""OpenCode Go API key prompt / load."""
import os, sys
from getpass import getpass

from ..console import console, Panel
from ..constants import OPENCODE_KEY_FILE
from ..utils.io import _secure_write


def prompt_for_opencode_key(reason: str = "") -> str:
    if reason:
        console.print(f"[red]{reason}[/]")
    console.print(Panel(
        "[bold yellow]OpenCode Go API key needed[/]\n\n"
        "Get one at: https://opencode.ai\n"
        "Your key starts with your OpenCode Go credentials.\n\n"
        f"Saved to: [dim]{OPENCODE_KEY_FILE}[/] (chmod 600)",
        title="Setup · OpenCode Go key", border_style="yellow"
    ))
    key = getpass("Paste OpenCode Go key (hidden): ").strip()
    if not key:
        console.print("[red]Key cannot be empty[/]"); sys.exit(1)
    _secure_write(OPENCODE_KEY_FILE, key)
    console.print("[green]✓ OpenCode Go key saved[/]")
    return key


def load_opencode_key() -> str:
    if os.getenv("OPENCODE_API_KEY"):
        return os.environ["OPENCODE_API_KEY"]
    if OPENCODE_KEY_FILE.exists():
        k = OPENCODE_KEY_FILE.read_text().strip()
        if k:
            return k
    return prompt_for_opencode_key()
