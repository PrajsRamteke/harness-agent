"""Render assistant response: text panels, thinking blocks, and tool calls."""
import json, re, threading
from concurrent.futures import ThreadPoolExecutor

from rich.text import Text

from ..console import console, Panel, Markdown
from ..constants import TOOL_ICONS, MAX_TOOL_OUTPUT, MAX_PARALLEL_TOOLS, CONTEXT_BUNDLE_MAX_CHARS
from ..tools import FUNC
from ..utils.tool_repair import repair_tool_input
from .. import state
from .hallucination import _scrub_hallucinations
from .tool_activity import describe_tool_activity
from .tool_events import emit_tool_done, emit_tool_start
from .tool_display import format_tool_output_preview
from .tool_runs import (
    begin_wave,
    cancel_pending,
    compact_file_tool_ui,
    flush_tool_ui,
    is_dock_tool,
    register_queued,
    set_done,
    set_running,
)
from .turn_progress import report_turn_phase

try:
    from ..tui import theme as _ui
except Exception:  # pragma: no cover
    from types import SimpleNamespace
    _ui = SimpleNamespace(
        ACCENT_2="#c084fc", WARN="#e3b341", SEP="#1f2630",
        FG_DIM="#6b7684", ERR="#f85149",
    )


def assistant_model_label() -> str:
    """Short label for assistant panels (Sonnet / Opus / Haiku / raw model id)."""
    m = state.MODEL.lower()
    if "opus" in m:
        return "Opus"
    if "sonnet" in m:
        return "Sonnet"
    if "haiku" in m:
        return "Haiku"
    return state.MODEL


# Tools that must run single-threaded — they mutate shared state, cwd, or UI.
# File writes use per-path locks in tools/files.py so different paths can run
# in parallel; only truly global tools stay here.
_SERIAL_TOOLS = {
    "run_bash",
    "git_status", "git_log", "git_diff",
    "search_code",
    "click_at", "click_element", "click_menu", "key_press", "type_text",
    "launch_app", "focus_app", "quit_app", "applescript", "shortcut_run",
    "clipboard_set", "mac_control", "speck",
    # context — builds/shared state
    "resolve_context", "read_bundle",
    "ask_user_question",
    # JSON-backed storage — file-level read/write races when run in parallel
    "memory_save", "memory_delete",
    "lesson_save", "lesson_delete",
    # Chrome extension bridge is one connection / active-tab state.
    "browser_status", "browser_navigate", "browser_find_tab", "browser_snapshot",
    "browser_page_context", "browser_click", "browser_mouse_click",
    "browser_fill", "browser_type", "browser_keys", "browser_evaluate",
    "browser_screenshot",
}

# All MCP-prefixed tools are treated as serial (stateful) too.
from ..mcp.registry import is_mcp_tool

# Context tools get a much higher output limit since they bundle many files at once.
_CONTEXT_TOOL_NAMES = {"resolve_context", "read_bundle", "lesson_list", "lesson_search"}

_FILE_WRITE_TOOLS = frozenset({"write_file", "edit_file", "multi_edit"})
_DISCOVERY_TOOLS = frozenset({"glob_files", "list_dir", "search_code", "fast_find", "rank_files"})

_tool_pool: ThreadPoolExecutor | None = None
_tool_pool_lock = threading.Lock()


def _tool_executor() -> ThreadPoolExecutor:
    global _tool_pool
    with _tool_pool_lock:
        if _tool_pool is None:
            _tool_pool = ThreadPoolExecutor(max_workers=MAX_PARALLEL_TOOLS)
        return _tool_pool


def _batch_has_tool(batch, names: frozenset[str]) -> bool:
    return any(b.name in names for b in batch)


def _should_flush_parallel_batch(batch, new_block) -> bool:
    """Split batches so discovery→read and read→write ordering stays safe."""
    if not batch:
        return False
    if new_block.name == "read_file" and _batch_has_tool(batch, _DISCOVERY_TOOLS):
        return True
    if new_block.name in _FILE_WRITE_TOOLS and (
        _batch_has_tool(batch, _DISCOVERY_TOOLS)
        or any(b.name not in _FILE_WRITE_TOOLS for b in batch)
    ):
        return True
    if new_block.name == "read_file" and _batch_has_tool(batch, _FILE_WRITE_TOOLS):
        return True
    if new_block.name not in _FILE_WRITE_TOOLS and _batch_has_tool(batch, _FILE_WRITE_TOOLS):
        return True
    return False


def _run_tool(b):
    dock = is_dock_tool(b.name)
    if dock:
        set_running(b.id)
    icon = TOOL_ICONS.get(b.name, "⚙")
    args_preview = json.dumps(b.input, ensure_ascii=False)[:120]
    activity_label = describe_tool_activity(b.name, b.input)
    report_turn_phase(activity_label)
    emit_tool_start(tool_id=b.id, name=b.name, label=activity_label)

    def _finish(out_str: str):
        if dock:
            set_done(b.id, out_str)
        emit_tool_done(
            tool_id=b.id,
            name=b.name,
            label=activity_label,
            error=str(out_str).startswith("ERROR"),
        )
        return b, icon, args_preview, out_str

    # OpenCode/OpenAI-compatible providers occasionally stream truncated or
    # empty JSON tool arguments. opencode_client._build_final() flags those
    # with a __stream_error__ sentinel so we can return an actionable error
    # instead of trying to invoke the tool with garbage.
    if isinstance(b.input, dict) and "__stream_error__" in b.input:
        return _finish(f"ERROR: {b.input['__stream_error__']}")

    # Strip unknown kwargs that some models hallucinate (e.g. `language`,
    # `description`) which would otherwise crash the tool with a confusing
    # `unexpected keyword argument` TypeError.
    call_input = b.input if isinstance(b.input, dict) else {}

    # ── smart repair layer ─────────────────────────────────────────
    # Auto-fix common formatting mistakes in model tool arguments
    # (null on optional fields, stringified arrays, markdown in paths, …)
    # so open-source models don't look "dumb" for small harness-level issues.
    call_input, repair_log = repair_tool_input(b.name, call_input)

    if b.name not in FUNC:
        if is_mcp_tool(b.name):
            if not state.global_mcp:
                out = (
                    f"ERROR: MCP tool '{b.name}' is unavailable — global MCP scope is OFF. "
                    "Servers from Cursor/Claude/OpenCode configs are not loaded in this session. "
                    "Tell the user to open /mcp and press g to enable global scope, then connect "
                    "the server. Do NOT read ~/.claude, ~/.cursor, or other global MCP config "
                    "files as a workaround."
                )
            else:
                out = (
                    f"ERROR: MCP tool '{b.name}' is not connected. "
                    "Open /mcp and connect the server before calling MCP tools."
                )
        else:
            out = f"ERROR: tool '{b.name}' is not available in this session"
        return _finish(str(out))

    try:
        out = FUNC[b.name](**call_input)
    except TypeError as e:
        msg = str(e)
        if "missing" in msg and "required" in msg:
            stripped = [
                entry for entry in repair_log if entry.startswith("removed unknown field")
            ]
            if stripped:
                hint = (
                    "wrong parameter names were used and stripped before the tool ran"
                )
            else:
                hint = "the provider dropped one or more required arguments mid-stream"
            out = (
                f"ERROR: tool '{b.name}' was invoked with input "
                f"{json.dumps(call_input, ensure_ascii=False)} — {hint}. "
                f"Detail: {e}. "
                f"Retry the SAME tool call with every required parameter populated."
            )
        elif "unexpected keyword argument" in msg:
            out = (
                f"ERROR: tool '{b.name}' was called with an unsupported argument "
                f"({e}). Re-issue the call using only the parameters listed in the "
                f"tool's input_schema."
            )
        else:
            out = f"ERROR: TypeError: {e}"
    except Exception as e:
        out = f"ERROR: {type(e).__name__}: {e}"

    out_str = str(out)
    if repair_log:
        out_str += "\n[repair note: " + "; ".join(repair_log) + "]"
    return _finish(out_str)


def _run_parallel_batch(batch, outputs):
    if not batch:
        return
    # Honour cancel flag before starting the batch
    if state.cancel_requested.is_set():
        return
    if len(batch) > 1:
        workers = min(MAX_PARALLEL_TOOLS, len(batch))
        if state.show_internal:
            console.print(f"[cyan]⚡ running {len(batch)} tools in parallel (max {workers} workers)[/]")
        ex = _tool_executor()
        for b, icon, ap, out_str in ex.map(_run_tool, batch):
            # Check cancel after each parallel tool completes
            if state.cancel_requested.is_set():
                # Don't bother storing results — we're aborting
                break
            outputs[b.id] = (icon, ap, out_str)
    else:
        b, icon, ap, out_str = _run_tool(batch[0])
        outputs[b.id] = (icon, ap, out_str)


def render_assistant(resp) -> bool:
    """Print assistant content, execute any tool calls, return True if more turns needed."""
    report_turn_phase("Jarvis: applying model output (text & tool plan)…")
    _model_label = assistant_model_label()
    panel_title = f"jarvis · {_model_label}"

    def _abort_stream_if_no_text():
        if not state._assistant_stream_ui_active:
            return
        has_text = any(
            b.type == "text" and re.search(r"\S", (b.text or ""))
            for b in resp.content
        )
        if has_text:
            return
        abort = getattr(console, "assistant_stream_abort", None)
        if abort:
            abort()
        state._assistant_stream_ui_active = False

    _abort_stream_if_no_text()

    tool_results = []
    tool_uses = []  # collect, then run in parallel where safe
    thinking_blocks = []  # rendered AFTER text commit to avoid erase by stream UI
    for b in resp.content:
        # ── text reply ──────────────────────────────────────────────
        if b.type == "text":
            raw = b.text or ""
            if not re.search(r"\S", raw):
                continue
            if state.web_tool_used_this_turn:
                text, was_flagged = raw.strip(), False
            else:
                text, was_flagged = _scrub_hallucinations(raw.strip())
            if not re.search(r"\S", text):
                if state.stream_reply_live and state._assistant_stream_ui_active:
                    ab = getattr(console, "assistant_stream_abort", None)
                    if ab:
                        ab()
                continue
            state.last_assistant_text = text
            commit = getattr(console, "assistant_stream_commit", None)
            if state.stream_reply_live and state._assistant_stream_ui_active and commit:
                commit(text, panel_title, was_flagged, thinking_blocks=thinking_blocks)
                state._assistant_stream_ui_active = False
                thinking_blocks = []  # already rendered inside commit; don't re-render
                if was_flagged:
                    console.print(f"[{_ui.ERR}]⚠ hallucination guard: pattern-matched sentences above may be unverified (shown with ⚠)[/]")
                continue
            console.print(Panel(
                Markdown(text),
                title=panel_title,
                title_align="left",
                border_style=_ui.ACCENT_2,
                padding=(0, 1),
            ))
            if was_flagged:
                console.print(f"[{_ui.ERR}]⚠ hallucination guard: pattern-matched sentences above may be unverified (shown with ⚠)[/]")

        # ── thinking block — collect, render after text commit ───────
        elif b.type == "thinking":
            thinking = b.thinking or ""
            if re.search(r"\S", thinking):
                thinking_blocks.append(thinking.strip())

        # ── tool call (collect now, run below in parallel) ───────────
        elif b.type == "tool_use":
            state.tool_calls_count += 1
            if b.name in ("web_search", "fetch_url", "verified_search"):
                state.web_tool_used_this_turn = True
            tool_uses.append(b)

    # Render thinking blocks (non-streaming / REPL path only — the TUI
    # streaming path renders them live and inside assistant_stream_commit).
    if state.show_internal and not state._thinking_stream_ui_active:
        for thinking in thinking_blocks:
            console.print(Panel(
                thinking,
                title="thinking",
                title_align="left",
                border_style=_ui.SEP,
                padding=(0, 1),
            ))

    # Execute collected tool calls: parallel-safe ones concurrently,
    # unsafe/stateful ones serially in their original order. Results are
    # emitted back in the original order so tool_use_id pairing stays intact.
    if tool_uses:
        begin_wave()
        for b in tool_uses:
            register_queued(b.id, b.name, b.input, notify=False)
        flush_tool_ui()

        outputs = {}  # b.id -> (icon, args_preview, out_str)
        parallel_batch = []

        try:
            for b in tool_uses:
                # Check cancel flag before each tool — allows Escape to abort
                # even during a multi-tool batch.
                if state.cancel_requested.is_set():
                    # Skip remaining tools: the turn is being cancelled.
                    break

                if b.name in _SERIAL_TOOLS or is_mcp_tool(b.name):
                    _run_parallel_batch(parallel_batch, outputs)
                    parallel_batch = []
                    _, icon, ap, out_str = _run_tool(b)
                    outputs[b.id] = (icon, ap, out_str)
                elif _should_flush_parallel_batch(parallel_batch, b):
                    _run_parallel_batch(parallel_batch, outputs)
                    parallel_batch = [b]
                else:
                    parallel_batch.append(b)
            _run_parallel_batch(parallel_batch, outputs)
        except (KeyboardInterrupt, Exception) as exc:
            # Ensure every tool_use gets a tool_result even when execution is
            # interrupted mid-batch — otherwise the next API call heals with
            # a vague "state recovered" stub and the model loses context.
            err_msg = (
                "ERROR: tool execution cancelled before completion"
                if isinstance(exc, KeyboardInterrupt)
                else f"ERROR: {type(exc).__name__}: {exc}"
            )
            for b in tool_uses:
                outputs.setdefault(b.id, ("⚙", "{}", err_msg))
            cancel_pending([b.id for b in tool_uses if b.id not in outputs])

        for b in tool_uses:
            if b.id in outputs:
                icon, ap, out_str = outputs[b.id]
                state.record_tool_output(b.name, ap, out_str)
                if state.show_internal and not compact_file_tool_ui():
                    console.print(f"{icon} [{_ui.WARN}]{b.name}[/] [{_ui.FG_DIM}]{ap}[/]")
                    if re.search(r"\S", out_str):
                        body, _trunc = format_tool_output_preview(out_str)
                        console.print(
                            Panel(
                                body,
                                title=f"{b.name} output",
                                title_align="left",
                                border_style=_ui.SEP,
                                padding=(0, 1),
                            )
                        )
            else:
                # Tool was skipped (cancel, partial batch failure). Emit a stub
                # result so the assistant tool_use has a matching tool_result —
                # required by strict providers (OpenAI / DeepSeek / OpenRouter).
                out_str = "ERROR: tool execution cancelled before completion"
            tool_results.append({
                "type": "tool_result",
                "tool_use_id": b.id,
                # Context tools (resolve_context, read_bundle) return the FULL
                # output — no truncation. Everything else gets the standard cap.
                "content": out_str if b.name in _CONTEXT_TOOL_NAMES else out_str[:MAX_TOOL_OUTPUT],
            })

    # Always append tool_results when tool_uses existed — even on cancel —
    # so state.messages stays a valid tool_use/tool_result pairing. Otherwise
    # the next API call fails with "tool_calls must be followed by tool messages".
    if tool_results:
        # Only attach results to the assistant turn we just executed. If another
        # user message already landed (queued prompt, session edit, retry), drop
        # stale results — stream._heal_orphan_tool_results() will also scrub any
        # that already leaked into persisted history.
        last = state.messages[-1] if state.messages else None
        if isinstance(last, dict) and last.get("role") == "assistant":
            state.messages.append({"role": "user", "content": tool_results})

    if state.cancel_requested.is_set():
        return False

    return bool(tool_results)
