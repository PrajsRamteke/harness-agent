"""Call Claude API with streaming + retry + OAuth refresh."""
import time
from typing import Any, Dict

from ..console import console, APIStatusError, RateLimitError
from ..tools import TOOLS
from ..auth.oauth_tokens import load_oauth_tokens, oauth_refresh
from ..auth.client import _build_client_from_mode
from .. import state
from .system import build_system
from .trim import trim_messages


def call_claude_stream():
    kwargs: Dict[str, Any] = dict(
        model=state.MODEL, max_tokens=8192, system=build_system(),
        messages=trim_messages(state.messages), tools=TOOLS,
    )
    if state.think_mode:
        kwargs["thinking"] = {"type": "enabled", "budget_tokens": 4000}

    delays = [1, 3, 6]
    oauth_refreshed = False
    for attempt in range(len(delays) + 1):
        try:
            with state.client.messages.stream(**kwargs) as stream:
                final = stream.get_final_message()
            state.total_in += final.usage.input_tokens
            state.total_out += final.usage.output_tokens
            return final
        except RateLimitError:
            if attempt == len(delays): raise
            console.print(f"[yellow]rate-limited, retry in {delays[attempt]}s[/]")
            time.sleep(delays[attempt])
        except APIStatusError as e:
            if e.status_code == 401:
                if state.auth_mode == "oauth" and not oauth_refreshed:
                    tokens = load_oauth_tokens()
                    refreshed = oauth_refresh(tokens) if tokens else None
                    if refreshed:
                        console.print("[dim]OAuth token refreshed, retrying…[/]")
                        state.client = _build_client_from_mode("oauth")
                        oauth_refreshed = True
                        continue
                    console.print("[red]OAuth session expired. Run /login to re-authenticate.[/]")
                else:
                    console.print("[red]Auth failed mid-session. Run /key reset (or /login) and restart.[/]")
                raise SystemExit(1)
            if e.status_code >= 500 and attempt < len(delays):
                console.print(f"[yellow]server {e.status_code}, retry...[/]")
                time.sleep(delays[attempt]); continue
            raise
