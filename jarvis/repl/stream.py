"""Call Claude API with streaming + retry + OAuth refresh."""
import time
from typing import Any, Dict

from ..console import console, APIStatusError, RateLimitError
from ..tools.router import select_tools
from ..auth.oauth_tokens import load_oauth_tokens, oauth_refresh
from ..auth.client import _build_client_from_mode
from .. import state
from .system import build_system
from .trim import trim_messages


# Reference to the active stream context so another thread (e.g. the TUI Esc
# handler) can abort an in-flight response.
_current_stream = None


def cancel_current_stream():
    """Close the active stream, if any. Safe to call from any thread."""
    global _current_stream
    s = _current_stream
    if s is None:
        return False
    try:
        s.close()
    except Exception:
        pass
    return True


def call_claude_stream():
    tools = select_tools(state.messages)
    if state.show_internal:
        console.print(f"[dim]tool schemas: {len(tools)} selected[/]")
    kwargs: Dict[str, Any] = dict(
        model=state.MODEL, max_tokens=8192, system=build_system(),
        messages=trim_messages(state.messages), tools=tools,
    )
    if state.think_mode:
        kwargs["thinking"] = {"type": "enabled", "budget_tokens": 4000}

    global _current_stream
    delays = [1, 3, 6]
    oauth_refreshed = False
    for attempt in range(len(delays) + 1):
        try:
            with state.client.messages.stream(**kwargs) as stream:
                _current_stream = stream
                try:
                    final = stream.get_final_message()
                finally:
                    _current_stream = None
            state.total_in += final.usage.input_tokens
            state.total_out += final.usage.output_tokens
            return final
        except RateLimitError:
            if attempt == len(delays): raise
            console.print(f"[yellow]rate-limited, retry in {delays[attempt]}s[/]")
            time.sleep(delays[attempt])
        except APIStatusError as e:
            if e.status_code == 401:
                if state.provider == "openrouter":
                    console.print(
                        "[red]Auth error — Provider: OpenRouter (API key)[/]\n"
                        "[yellow]OpenRouter rejected the key. "
                        "Delete `~/.config/harness-agent/openrouter_key` and restart, "
                        "or run /provider to reconfigure.[/]"
                    )
                elif state.auth_mode == "oauth" and not oauth_refreshed:
                    tokens = load_oauth_tokens()
                    refreshed = oauth_refresh(tokens) if tokens else None
                    if refreshed:
                        console.print("[dim]OAuth token refreshed, retrying…[/]")
                        state.client = _build_client_from_mode("oauth")
                        oauth_refreshed = True
                        continue
                    console.print("[red]Auth error — Provider: Anthropic (OAuth)[/]\n"
                                  "[yellow]OAuth session expired. Run /login to re-authenticate.[/]")
                else:
                    console.print("[red]Auth error — Provider: Anthropic (API key)[/]\n"
                                  "[yellow]Run /key reset (or /login) and restart.[/]")
                raise SystemExit(1)
            if e.status_code == 402:
                console.print(
                    f"[red]Payment required — Provider: {state.provider}[/]\n"
                    "[yellow]Insufficient credits for this model. "
                    "Try a [cyan]:free[/] model via /model, or top up at "
                    "https://openrouter.ai/credits[/]"
                )
                raise SystemExit(1)
            if e.status_code == 404 and state.provider == "openrouter":
                console.print(
                    f"[red]OpenRouter: model '{state.MODEL}' not found.[/]\n"
                    "[yellow]Run /model to pick a valid slug.[/]"
                )
                raise SystemExit(1)
            if e.status_code == 429 and state.provider == "openrouter":
                console.print(
                    f"[yellow]OpenRouter rate limit on '{state.MODEL}'. "
                    "Free models are heavily throttled — retry shortly or try another model.[/]"
                )
                if attempt < len(delays):
                    time.sleep(delays[attempt]); continue
                raise
            if e.status_code >= 500 and attempt < len(delays):
                console.print(f"[yellow]server {e.status_code}, retry...[/]")
                time.sleep(delays[attempt]); continue
            raise
