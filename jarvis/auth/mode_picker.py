"""Prompt user to choose a provider and (for Anthropic) an auth mode."""
import sys

from ..console import console, Panel
from ..constants import PROVIDER_ANTHROPIC, PROVIDER_OPENROUTER, PROVIDER_OPENCODE, AUTH_API_KEY, AUTH_OAUTH


def _choose_provider() -> str:
    """Pick Anthropic, OpenRouter, or OpenCode Go."""
    console.print(Panel(
        "[bold]Which provider would you like to use?[/]\n\n"
        "  [cyan]1[/]  Anthropic     [dim](Claude models — API key or Pro/Max login)[/]\n"
        "  [cyan]2[/]  OpenRouter    [dim](free & paid models from many providers)[/]\n"
        "  [cyan]3[/]  OpenCode Go   [dim](GLM, Kimi, DeepSeek, MiMo, MiniMax, Qwen)[/]\n",
        title="🌐 Provider", border_style="cyan",
    ))
    while True:
        try:
            ch = console.input("choice [1/2/3]: ").strip().lower()
        except (EOFError, KeyboardInterrupt):
            console.print("\n[yellow]cancelled[/]"); sys.exit(1)
        if ch in ("1", "anthropic", "a"):          return PROVIDER_ANTHROPIC
        if ch in ("2", "openrouter", "or"):        return PROVIDER_OPENROUTER
        if ch in ("3", "opencode", "oc", "go"):    return PROVIDER_OPENCODE
        console.print("[red]enter 1, 2, or 3[/]")


def _choose_auth_mode() -> str:
    """Pick Anthropic API key vs OAuth login."""
    console.print(Panel(
        "[bold]How would you like to authenticate with Anthropic?[/]\n\n"
        "  [cyan]1[/]  API key  [dim](pay-as-you-go, sk-ant-…)[/]\n"
        "  [cyan]2[/]  Log in with Anthropic  [dim](Claude Pro/Max subscription)[/]\n",
        title="🔐 Anthropic auth", border_style="cyan",
    ))
    while True:
        try:
            ch = console.input("choice [1/2]: ").strip()
        except (EOFError, KeyboardInterrupt):
            console.print("\n[yellow]cancelled[/]"); sys.exit(1)
        if ch in ("1", "key", "api", "api_key"): return AUTH_API_KEY
        if ch in ("2", "oauth", "login"):        return AUTH_OAUTH
        console.print("[red]enter 1 or 2[/]")
