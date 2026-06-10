"""Call the configured model API with streaming + retry + OAuth refresh."""
import ctypes
import threading
import time
from typing import Any, Dict

from anthropic import APITimeoutError
import httpx

try:
    from openai import APITimeoutError as OpenAITimeoutError
except ImportError:  # pragma: no cover
    class OpenAITimeoutError(Exception):
        """Stub when openai is not installed."""

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
    PROVIDER_OPENROUTER, OPENROUTER_DEFAULT_MODEL,
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

_STREAM_TIMEOUT_ERRORS = (
    APITimeoutError,
    OpenAITimeoutError,
    TimeoutError,
    httpx.ReadTimeout,
    httpx.WriteTimeout,
    httpx.ConnectTimeout,
    httpx.PoolTimeout,
)


def _report_http_timeout() -> None:
    report_turn_phase("HTTP timeout — no data from API")
    console.print(
        "[red]Timed out waiting for the model API (read stalled too long).[/]\n"
        "[dim]Free/queued models often stall; try `HARNESS_HTTP_READ_TIMEOUT=600` "
        "for more patience, switch to a faster model, or Esc to cancel earlier.[/]"
    )


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

    from ..console import console
    cancel_prompts = getattr(console, "cancel_pending_prompts", None)
    if callable(cancel_prompts):
        try:
            cancel_prompts()
        except Exception:
            pass

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
            # Drop any thinking state left by the previous iteration of this
            # turn (e.g. thinking → tool_use with no text) so its stale
            # anchor can never truncate content written since.
            think_reset = getattr(console, "thinking_stream_reset", None)
            if think_reset:
                think_reset()
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
        # If /think mode was ON but the model never emitted any reasoning
        # deltas, the backend may not support separate thinking for this
        # model.  Post a gentle notice so the user isn't misled into
        # thinking their think setting is broken — it's a provider limit.
        if state.think_mode and not thinking_started:
            from ..console import console as _console
            _console.print(
                "[dim]— thinking mode on but model/provider doesn't return "
                "separate reasoning (thinking may appear inside the reply)[/]"
            )


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


def _block_dict(block: Any) -> dict:
    if isinstance(block, dict):
        return block
    if hasattr(block, "model_dump"):
        return block.model_dump()
    return {k: v for k, v in getattr(block, "__dict__", {}).items()}


def _is_tool_use_block(block: Any) -> bool:
    return _block_dict(block).get("type") == "tool_use"


def _is_tool_result_block(block: Any) -> bool:
    return _block_dict(block).get("type") == "tool_result"


def _assistant_tool_use_ids(msg: Dict) -> set[str]:
    content = msg.get("content") or []
    if not isinstance(content, list):
        return set()
    out: set[str] = set()
    for block in content:
        if not _is_tool_use_block(block):
            continue
        bid = _block_dict(block).get("id")
        if bid:
            out.add(str(bid))
    return out


def _user_message_has_text(msg: Dict) -> bool:
    content = msg.get("content")
    if isinstance(content, str):
        return bool(content.strip())
    if not isinstance(content, list):
        return False
    for block in content:
        if _is_tool_result_block(block):
            continue
        bd = _block_dict(block)
        if bd.get("type") == "text" and str(bd.get("text") or "").strip():
            return True
    return False


def _tool_result_path_blocked(messages: list[Dict], assistant_idx: int, result_idx: int) -> bool:
    """True when another turn sits between an assistant tool_use and its tool_result."""
    for j in range(assistant_idx + 1, result_idx):
        mid = messages[j]
        role = mid.get("role")
        if role == "assistant":
            return True
        if role == "user" and _user_message_has_text(mid):
            return True
    return False


def _find_assistant_for_tool_use(messages: list[Dict], before_idx: int, tool_use_id: str) -> int | None:
    for j in range(before_idx - 1, -1, -1):
        msg = messages[j]
        if msg.get("role") != "assistant":
            continue
        if tool_use_id in _assistant_tool_use_ids(msg):
            return j
    return None


def _heal_orphan_tool_results() -> None:
    """Drop tool_result blocks that no longer match a valid assistant tool_use turn.

    DeepSeek/OpenAI reject requests when a converted ``role: tool`` message
    references a ``tool_call_id`` that is not part of the immediately
    preceding assistant ``tool_calls`` block. That happens when the user sends
    a new prompt before slow tools finish, when a session loses an assistant
    message, or when stale/cancelled tool batches append results out of band.
    """
    msgs = state.messages
    if not msgs:
        return

    keep: dict[tuple[int, int], bool] = {}
    answered: set[tuple[int, str]] = set()

    for i, msg in enumerate(msgs):
        if msg.get("role") != "user":
            continue
        content = msg.get("content")
        if not isinstance(content, list):
            continue
        for bi, raw in enumerate(content):
            if not _is_tool_result_block(raw):
                continue
            tid = str(_block_dict(raw).get("tool_use_id") or "")
            if not tid:
                keep[(i, bi)] = False
                continue
            asst_idx = _find_assistant_for_tool_use(msgs, i, tid)
            if asst_idx is None or _tool_result_path_blocked(msgs, asst_idx, i):
                keep[(i, bi)] = False
                continue
            key = (asst_idx, tid)
            if key in answered:
                keep[(i, bi)] = False
                continue
            answered.add(key)
            keep[(i, bi)] = True

    healed: list[Dict] = []
    for i, msg in enumerate(msgs):
        if msg.get("role") != "user":
            healed.append(msg)
            continue
        content = msg.get("content")
        if not isinstance(content, list):
            healed.append(msg)
            continue
        new_content = []
        for bi, raw in enumerate(content):
            if _is_tool_result_block(raw) and not keep.get((i, bi), False):
                continue
            new_content.append(raw)
        if not new_content:
            continue
        if new_content == content:
            healed.append(msg)
        else:
            healed.append({**msg, "content": new_content})

    state.messages = healed


def _assistant_turn_abandoned(messages: list[Dict], assistant_idx: int) -> bool:
    """True when the user typed a new prompt before this tool batch finished."""
    ids = _assistant_tool_use_ids(messages[assistant_idx])
    if not ids:
        return False
    answered: set[str] = set()
    for j in range(assistant_idx + 1, len(messages)):
        msg = messages[j]
        if msg.get("role") == "assistant":
            break
        if msg.get("role") != "user":
            continue
        if _user_message_has_text(msg):
            return answered != ids
        content = msg.get("content")
        if not isinstance(content, list):
            continue
        for block in content:
            if not _is_tool_result_block(block):
                continue
            tid = str(_block_dict(block).get("tool_use_id") or "")
            if tid not in ids:
                continue
            if not _tool_result_path_blocked(messages, assistant_idx, j):
                answered.add(tid)
    return False


def _strip_abandoned_assistant_tool_uses() -> None:
    """Remove tool_use blocks from assistant turns the user already moved past."""
    msgs = state.messages
    for i, msg in enumerate(msgs):
        if msg.get("role") != "assistant" or not _assistant_turn_abandoned(msgs, i):
            continue
        content = msg.get("content") or []
        if not isinstance(content, list):
            continue
        new_content = [block for block in content if not _is_tool_use_block(block)]
        if len(new_content) == len(content):
            continue
        if new_content:
            msgs[i] = {**msg, "content": new_content}
        else:
            msgs[i] = {**msg, "content": ""}


def _heal_message_history() -> None:
    """Repair tool_use/tool_result pairings before strict-provider API calls."""
    _heal_orphan_tool_results()
    _strip_abandoned_assistant_tool_uses()
    _heal_orphan_tool_uses()
    _heal_orphan_tool_results()


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

    # Repair broken tool_use/tool_result pairings before sending — strict
    # providers (DeepSeek, OpenAI) reject the request otherwise.
    _heal_message_history()

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
        if state.provider in (PROVIDER_OPENCODE, PROVIDER_OPENCODE_ZEN):
            kwargs["thinking"]["effort"] = state.think_effort
    elif state.provider in (PROVIDER_OPENCODE, PROVIDER_OPENCODE_ZEN):
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
        except _STREAM_TIMEOUT_ERRORS:
            _report_http_timeout()
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
