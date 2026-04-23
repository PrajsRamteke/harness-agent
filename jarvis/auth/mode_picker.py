"""Prompt user to choose between API key and OAuth login."""
import sys

from ..console import console, Panel


def _choose_auth_mode() -> str:
    """Prompt user to pick API key or OAuth login on first run."""
    console.print(Panel(
        "[bold]How would you like to authenticate?[/]\n\n"
        "  [cyan]1[/]  API key  [dim](pay-as-you-go, sk-ant-…)[/]\n"
        "  [cyan]2[/]  Log in with Anthropic  [dim](Claude Pro/Max subscription)[/]\n",
        title="🔐 Setup", border_style="cyan",
    ))
    while True:
        try:
            ch = console.input("choice [1/2]: ").strip()
        except (EOFError, KeyboardInterrupt):
            console.print("\n[yellow]cancelled[/]"); sys.exit(1)
        if ch in ("1", "key", "api", "api_key"): return "api_key"
        if ch in ("2", "oauth", "login"):        return "oauth"
        console.print("[red]enter 1 or 2[/]")
