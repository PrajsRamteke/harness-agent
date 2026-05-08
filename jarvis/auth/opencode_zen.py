"""OpenCode Zen API key prompt / load. Mirrors opencode.py but for Zen."""
import os, sys

from ..console import console, Panel
from ..constants import OPENCODE_ZEN_KEY_FILE
from ..utils.io import _secure_write


def prompt_for_opencode_zen_key(reason: str = "") -> str:
    if reason:
        console.print(f"[red]{reason}[/]")
    console.print(Panel(
        "[bold yellow]OpenCode Zen API key needed[/]\n\n"
        "Get one at: https://opencode.ai\n"
        "Your key starts with your OpenCode Zen credentials.\n\n"
        f"Saved to: [dim]{OPENCODE_ZEN_KEY_FILE}[/] (chmod 600)",
        title="Setup · OpenCode Zen key", border_style="yellow"
    ))
    key = console.input("Paste OpenCode Zen key: ").strip()
    if not key:
        console.print("[red]Key cannot be empty[/]"); sys.exit(1)
    _secure_write(OPENCODE_ZEN_KEY_FILE, key)
    console.print("[green]✓ OpenCode Zen key saved[/]")
    return key


def load_opencode_zen_key() -> str:
    if os.getenv("OPENCODE_ZEN_API_KEY"):
        return os.environ["OPENCODE_ZEN_API_KEY"]
    if OPENCODE_ZEN_KEY_FILE.exists():
        k = OPENCODE_ZEN_KEY_FILE.read_text().strip()
        if k:
            return k
    return prompt_for_opencode_zen_key()
