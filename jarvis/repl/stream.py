"""Call the configured model API with streaming + retry + OAuth refresh."""
import ctypes
import threading
import time
from typing import Any, Dict

from anthropic import APITimeoutError

try:
    from openai import RateLimitError as OpenAIRateLimitError
    from openai import APIStatusError as OpenAIAPIStatusError
except ImportError:  # pragma: no cover
    class OpenAIRateLimitError(Exception):
        """Stub when openai is not installed."""

    class OpenAIAPIStatusError(Exception):
        """Stub when openai is not installed."""

from ..console import console, APIStatusError, RateLimitError, HarnessAPIError
from ..tools.router import select_tools
from ..constants.models import API_MAX_TOKENS, THINKING_BUDGET_TOKENS
from ..constants import (
    PROVIDER_OPENCODE, PROVIDER_OPENCODE_ZEN, PROVIDER_OPENAI_CODEX,
    PROVIDER_OPENROUTER, OPENROUTER_DEFAULT_MODEL, PROVIDER_POLLINATIONS,
)
from ..auth.oauth_tokens import load_oauth_tokens, oauth_refresh
from ..auth.codex_oauth_tokens import load_codex_oauth_tokens, codex_oauth_refresh
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
_worker_thread_id: int = 0  # set at the start of each turn


def _raise_in_thread(tid: int, exc_type) -> bool:
    """Inject an exception into a thread by ID using ctypes. Returns True on success."""
    if not tid:
        return False
    try:
        res = ctypes.pythonapi.PyThreadState_SetAsyncExc(
            ctypes.c_ulong(tid),
            ctypes.py_object(exc_type),
        )
        return res == 1
    except Exception:
        return False


def cancel_current_stream():
    """Cancel the current turn from any thread.

    Sets the persistent cancel flag so every phase (tool execution, next stream
    start, render_assistant) knows to abort. Also closes the active stream and
    injects KeyboardInterrupt into the worker thread for immediate unblocking.
    Safe to call from any thread, including the TUI event loop.
    """
    from .. import state as _state
    _state.cancel_requested.set()

    global _current_stream
    s = _current_stream
    if s is not None:
        try:
            s.close()
        except Exception:
            pass
    _raise_in_thread(_worker_thread_id, KeyboardInterrupt)
    return True


def _iter_live_deltas(stream):
    """Yield ``(kind, chunk)`` for thinking and text deltas from any provider stream."""
    delta_stream = getattr(stream, "delta_stream", None)
    if delta_stream is not None:
        yield from delta_stream
        return

    if hasattr(stream, "get_final_message") and hasattr(stream, "__iter__"):
        for event in stream:
            et = getattr(event, "type", None)
            if et == "thinking":
                thinking = getattr(event, "thinking", "") or ""
                if thinking:
                    yield "thinking", thinking
            elif et == "text":
                text = getattr(event, "text", "") or ""
                if text:
                    yield "text", text
        return

    for chunk in stream.text_stream:
        if chunk:
            yield "text", chunk


def _consume_live_text_stream(stream, panel_title: str) -> None:
    """Drain live thinking + text deltas for real-time UX; then call ``get_final_message()``.

    TUI: pushes deltas via ``TUIConsole`` helpers and sets stream UI flags.
    Legacy Rich REPL: uses :class:`RichAssistantStreamDisplay`.

    Extended thinking emits reasoning deltas before text — without handling them the
    UI sits on a generic "streaming" label with no transcript motion.
    """
    rich_live: RichAssistantStreamDisplay | None = None
    stop_watch = threading.Event()
    last_progress = [time.monotonic()]
    phase = [""]

    def _idle_watch() -> None:
        while not stop_watch.wait(25.0):
            idle = time.monotonic() - last_progress[0]
            if idle >= 35:
                if phase[0] == "thinking":
                    report_turn_phase(
                        f"Still thinking ({int(idle)}s) — Esc=cancel"
                    )
                else:
                    report_turn_phase(
                        f"No reply yet ({int(idle)}s) — API queue/throttle; Esc=cancel"
                    )

    watcher = threading.Thread(target=_idle_watch, daemon=True)
    watcher.start()
    text_started = False
    try:
        tui = hasattr(console, "assistant_stream_start")
        thinking_started = False

        if tui:
            think_start = getattr(console, "thinking_stream_start", None)
            think_push = getattr(console, "thinking_stream_push", None)
            think_flush = getattr(console, "thinking_stream_flush", None)
            think_finalize = getattr(console, "thinking_stream_finalize", None)
        else:
            think_start = think_push = think_flush = think_finalize = None
            rich_live = RichAssistantStreamDisplay(console)

        for kind, chunk in _iter_live_deltas(stream):
            last_progress[0] = time.monotonic()
            if kind == "thinking":
                if phase[0] != "thinking":
                    phase[0] = "thinking"
                    report_turn_phase("Thinking…")
                if tui:
                    if not thinking_started and think_start:
                        think_start()
                        thinking_started = True
                    if think_push:
                        think_push(chunk)
                continue

            if kind != "text":
                continue

            if phase[0] != "text":
                phase[0] = "text"
                if tui and thinking_started and think_finalize:
                    if think_flush:
                        think_flush()
                    think_finalize()
                report_turn_phase("Replying…")

            if tui:
                if not text_started:
                    console.assistant_stream_start(panel_title)
                    state._assistant_stream_ui_active = True
                    text_started = True
                console.assistant_stream_push(chunk)
            else:
                if not text_started:
                    rich_live.start(panel_title)
                    text_started = True
                rich_live.push(chunk)

        if tui:
            if thinking_started and think_flush:
                think_flush()
            if text_started:
                console.assistant_stream_flush()
            elif thinking_started and think_finalize:
                think_finalize()
            return

        if text_started and rich_live is not None:
            rich_live.stop()
    finally:
        stop_watch.set()
        if rich_live is not None and not text_started:
            try:
                rich_live.stop()
            except Exception:
                pass


def _stop_on_rate_limit(detail: str = "") -> None:
    """Print a clear rate-limit message and abort the turn (no backoff retries)."""
    report_turn_phase("Rate limited — stopping")
    console.print(
        f"[red]Rate limited — Provider: {state.provider}[/]"
        + (f" · model: {state.MODEL}" if state.MODEL else "")
    )
    if detail:
        console.print(f"[dim]{detail}[/]")
    console.print(
        "[yellow]Wait and try again later, or switch models with /model.[/]"
    )


def _heal_orphan_tool_uses() -> None:
    """Ensure every assistant tool_use has a following tool_result.

    Strict providers (OpenAI / DeepSeek via OpenRouter / OpenCode) reject the
    request with "An assistant message with 'tool_calls' must be followed by
    tool messages responding to each 'tool_call_id'" if any tool_use is left
    unanswered. This can happen when tool execution is cancelled or crashes
    mid-batch, or when a session was persisted in a partially-completed state.

    Walk state.messages; for each assistant message with tool_use blocks, look
    at the immediately-following user message and add stub tool_result blocks
    for any tool_use ids that aren't already answered. Inserts a new user
    message if one isn't there. Idempotent — running it twice is a no-op.
    """
    msgs = state.messages
    i = 0
    while i < len(msgs):
        m = msgs[i]
        if m.get("role") != "assistant":
            i += 1
            continue
        content = m.get("content") or []
        if not isinstance(content, list):
            i += 1
            continue
        tool_use_ids = []
        for block in content:
            btype = getattr(block, "type", None) if not isinstance(block, dict) else block.get("type")
            if btype != "tool_use":
                continue
            bid = getattr(block, "id", None) if not isinstance(block, dict) else block.get("id")
            if bid:
                tool_use_ids.append(bid)
        if not tool_use_ids:
            i += 1
            continue

        nxt = msgs[i + 1] if i + 1 < len(msgs) else None
        nxt_content = nxt.get("content") if isinstance(nxt, dict) and nxt.get("role") == "user" else None
        if not isinstance(nxt_content, list):
            nxt_content = None

        answered = set()
        if nxt_content:
            for block in nxt_content:
                btype = block.get("type") if isinstance(block, dict) else None
                if btype == "tool_result":
                    tid = block.get("tool_use_id")
                    if tid:
                        answered.add(tid)

        missing = [tid for tid in tool_use_ids if tid not in answered]
        if missing:
            stubs = [{
                "type": "tool_result",
                "tool_use_id": tid,
                "content": "ERROR: tool execution did not complete (state recovered)",
            } for tid in missing]
            if nxt_content is not None:
                nxt["content"] = stubs + nxt_content
            else:
                msgs.insert(i + 1, {"role": "user", "content": stubs})
        i += 1


def call_claude_stream():
    # Check cancel flag before starting a new stream — allows Escape to
    # prevent the next stream from even starting after tool results.
    if state.cancel_requested.is_set():
        raise KeyboardInterrupt()

    # Repair any orphan tool_use blocks before sending — strict providers
    # (DeepSeek, OpenAI) reject the request otherwise.
    _heal_orphan_tool_uses()

    report_turn_phase("Jarvis: building request…")
    tools = select_tools(state.messages)
    if state.show_internal:
        console.print(f"[dim]tool schemas: {len(tools)} selected[/]")
    kwargs: Dict[str, Any] = dict(
        model=state.MODEL, max_tokens=API_MAX_TOKENS, system=build_system(),
        messages=trim_messages(state.messages), tools=tools,
    )
    if state.think_mode:
        kwargs["thinking"] = {
            "type": "enabled",
            "budget_tokens": THINKING_BUDGET_TOKENS,
        }
        if state.provider in (PROVIDER_OPENCODE, PROVIDER_OPENCODE_ZEN, PROVIDER_POLLINATIONS):
            kwargs["thinking"]["effort"] = state.think_effort
    elif state.provider in (PROVIDER_OPENCODE, PROVIDER_OPENCODE_ZEN, PROVIDER_POLLINATIONS):
        # OpenCode (DeepSeek, etc.) needs explicit {"type": "disabled"} to turn off thinking
        kwargs["thinking"] = {"type": "disabled"}

    global _current_stream, _worker_thread_id
    _worker_thread_id = threading.current_thread().ident or 0
    delays = [1, 3, 6]
    oauth_refreshed = False
    openrouter_model_retried = False
    panel_title = f"jarvis · {assistant_model_label()}"
    for attempt in range(len(delays) + 1):
        try:
            report_turn_phase("Jarvis: API waiting...")
            with state.client.messages.stream(**kwargs) as stream:
                _current_stream = stream
                try:
                    if state.stream_reply_live:
                        report_turn_phase("Waiting for model…")
                        _consume_live_text_stream(stream, panel_title)
                    else:
                        report_turn_phase("Jarvis: buffering full reply (stream off)…")
                    report_turn_phase("Jarvis: finalizing...")
                    final = stream.get_final_message()
                finally:
                    _current_stream = None
            # input_tokens is the FULL prompt sent in THIS request (includes full
            # conversation history).  Accumulating it across turns massively
            # overcounts — just store the latest value which reflects total
            # *unique* input consumed so far.  Output tokens are per-turn unique
            # so accumulation is correct.
            state.total_in = final.usage.input_tokens
            state.total_out += final.usage.output_tokens
            # Anthropic's Usage exposes only input/output tokens; OpenCode's
            # fake Usage adds total_tokens. Compute when absent.
            state.total_tokens = getattr(
                final.usage,
                "total_tokens",
                final.usage.input_tokens + final.usage.output_tokens,
            )
            return final
        except APITimeoutError:
            report_turn_phase("HTTP timeout — no data from API")
            console.print(
                "[red]Timed out waiting for the model API (read stalled too long).[/]\n"
                "[dim]OpenRouter free tiers often queue; try `HARNESS_HTTP_READ_TIMEOUT=600` "
                "for more patience, a faster model, or Esc to cancel earlier.[/]"
            )
            raise
        except RateLimitError as e:
            _stop_on_rate_limit(str(e))
            raise
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
                elif state.provider == PROVIDER_OPENAI_CODEX and not oauth_refreshed:
                    tokens = load_codex_oauth_tokens()
                    refreshed = codex_oauth_refresh(tokens) if tokens else None
                    if refreshed:
                        console.print("[dim]Codex OAuth token refreshed, retrying…[/]")
                        from ..auth.client import _build_codex_client
                        state.client = _build_codex_client()
                        oauth_refreshed = True
                        continue
                    console.print(
                        "[red]Auth error — Provider: OpenAI Codex (OAuth)[/]\n"
                        "[yellow]OAuth session expired. Run /login to re-authenticate.[/]"
                    )
                elif state.auth_mode == "oauth" and state.provider == "anthropic" and not oauth_refreshed:
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
                raise HarnessAPIError("auth error")
            if e.status_code == 402:
                console.print(
                    f"[red]Payment required — Provider: {state.provider}[/]\n"
                    "[yellow]Insufficient credits for this model. "
                    "Try a [cyan]:free[/] model via /model, or top up at "
                    "https://openrouter.ai/credits[/]"
                )
                raise HarnessAPIError("payment required")
            if e.status_code == 404 and state.provider == PROVIDER_OPENROUTER:
                fallback = OPENROUTER_DEFAULT_MODEL
                if not openrouter_model_retried and state.MODEL != fallback:
                    openrouter_model_retried = True
                    console.print(
                        f"[yellow]OpenRouter: model '{state.MODEL}' not found — "
                        f"switching to [cyan]{fallback}[/][/]"
                    )
                    state.MODEL = fallback
                    kwargs["model"] = fallback
                    try:
                        from ..storage.prefs import save_last_model
                        save_last_model()
                    except Exception:
                        pass
                    continue
                console.print(
                    f"[red]OpenRouter: model '{state.MODEL}' not found.[/]\n"
                    "[yellow]Run /model to pick a valid slug.[/]"
                )
                raise HarnessAPIError("model not found")
            if e.status_code == 429:
                _stop_on_rate_limit(str(e))
                raise
            if e.status_code >= 500 and attempt < len(delays):
                report_turn_phase(f"Server {e.status_code} — retrying soon…")
                console.print(f"[yellow]server {e.status_code}, retry...[/]")
                time.sleep(delays[attempt]); continue
            raise
        except OpenAIRateLimitError as e:
            _stop_on_rate_limit(str(e))
            raise
        except OpenAIAPIStatusError as e:
            if getattr(e, "status_code", None) == 429:
                _stop_on_rate_limit(str(e))
                raise
            if getattr(e, "status_code", None) == 400 and state.provider == PROVIDER_OPENAI_CODEX:
                console.print(
                    f"[red]Codex rejected model '{state.MODEL}'.[/]\n"
                    "[yellow]Run /model to pick a Codex model (e.g. gpt-5.4), "
                    "or /provider to switch provider.[/]"
                )
            raise
