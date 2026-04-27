"""Call the configured model API with streaming + retry + OAuth refresh."""
import threading
import time
from typing import Any, Dict

from anthropic import APITimeoutError

from ..console import console, APIStatusError, RateLimitError
from ..tools.router import select_tools
from ..auth.oauth_tokens import load_oauth_tokens, oauth_refresh
from ..auth.client import _build_client_from_mode
from .. import state
from .system import build_system
from .trim import trim_messages
from .render import assistant_model_label
from .stream_display import RichAssistantStreamDisplay
from .turn_progress import report_turn_phase


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


def _consume_live_text_stream(stream, panel_title: str) -> None:
    """Drain `stream.text_stream` for real-time typing UX; then call `get_final_message()`.

    TUI: pushes deltas via ``TUIConsole`` helpers and sets ``state._assistant_stream_ui_active``.
    Legacy Rich REPL: uses :class:`RichAssistantStreamDisplay`.

    ``text_stream`` only yields *text* deltas; some providers emit nothing until a long
    queue/processing phase — a background watchdog nudges the activity line so the UI
    does not look frozen with no explanation.
    """
    rich_live: RichAssistantStreamDisplay | None = None
    stop_watch = threading.Event()
    last_progress = [time.monotonic()]

    def _idle_watch() -> None:
        while not stop_watch.wait(25.0):
            idle = time.monotonic() - last_progress[0]
            if idle >= 35:
                report_turn_phase(
                    f"Jarvis: still no text tokens ({int(idle)}s) — "
                    "API queue/throttle (often on OpenRouter :free); Esc cancels"
                )

    watcher = threading.Thread(target=_idle_watch, daemon=True)
    watcher.start()
    try:
        if hasattr(console, "assistant_stream_start"):
            console.assistant_stream_start(panel_title)
            state._assistant_stream_ui_active = True
            try:
                for chunk in stream.text_stream:
                    last_progress[0] = time.monotonic()
                    console.assistant_stream_push(chunk)
            finally:
                console.assistant_stream_flush()
            return

        rich_live = RichAssistantStreamDisplay(console)
        rich_live.start(panel_title)
        for chunk in stream.text_stream:
            last_progress[0] = time.monotonic()
            rich_live.push(chunk)
    finally:
        stop_watch.set()
        if rich_live is not None:
            rich_live.stop()


def call_claude_stream():
    report_turn_phase("Jarvis: choosing tools & building request…")
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
    panel_title = f"Jarvis [{assistant_model_label()}]"
    for attempt in range(len(delays) + 1):
        try:
            report_turn_phase("Jarvis: waiting for model response…")
            with state.client.messages.stream(**kwargs) as stream:
                _current_stream = stream
                try:
                    if state.stream_reply_live:
                        report_turn_phase("Jarvis: streaming reply text…")
                        _consume_live_text_stream(stream, panel_title)
                    else:
                        report_turn_phase("Jarvis: buffering full reply (stream off)…")
                    report_turn_phase("Jarvis: finalizing response…")
                    final = stream.get_final_message()
                finally:
                    _current_stream = None
            state.total_in += final.usage.input_tokens
            state.total_out += final.usage.output_tokens
            return final
        except APITimeoutError:
            report_turn_phase("HTTP timeout — no data from API")
            console.print(
                "[red]Timed out waiting for the model API (read stalled too long).[/]\n"
                "[dim]OpenRouter free tiers often queue; try `HARNESS_HTTP_READ_TIMEOUT=600` "
                "for more patience, a faster model, or Esc to cancel earlier.[/]"
            )
            raise
        except RateLimitError:
            if attempt == len(delays): raise
            w = delays[attempt]
            report_turn_phase(f"Rate limited — waiting {w}s before retry…")
            console.print(f"[yellow]rate-limited, retry in {w}s[/]")
            time.sleep(w)
        except APIStatusError as e:
            if e.status_code == 401:
                if state.provider == "openrouter":
                    console.print(
                        "[red]Auth error — Provider: OpenRouter (API key)[/]\n"
                        "[yellow]OpenRouter rejected the key. "
                        "Delete `~/.config/harness-agent/openrouter_key` and restart, "
                        "or run /provider to reconfigure.[/]"
                    )
                elif state.provider == "opencode":
                    console.print(
                        "[red]Auth error — Provider: OpenCode Go (API key)[/]\n"
                        "[yellow]OpenCode rejected the key. "
                        "Delete `~/.config/harness-agent/opencode_key` and restart, "
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
                    w = delays[attempt]
                    report_turn_phase(f"OpenRouter rate limit — waiting {w}s…")
                    time.sleep(w)
                    continue
                raise
            if e.status_code >= 500 and attempt < len(delays):
                report_turn_phase(f"Server {e.status_code} — retrying soon…")
                console.print(f"[yellow]server {e.status_code}, retry...[/]")
                time.sleep(delays[attempt]); continue
            raise
