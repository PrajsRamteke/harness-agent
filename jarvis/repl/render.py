"""Render assistant response: text panels, thinking blocks, and tool calls."""
import json, re
from concurrent.futures import ThreadPoolExecutor

from rich.text import Text

from ..console import console, Panel, Markdown
from ..constants import TOOL_ICONS, MAX_TOOL_OUTPUT, MAX_PARALLEL_TOOLS, CONTEXT_BUNDLE_MAX_CHARS
from ..tools import FUNC
from .. import state
from .hallucination import _scrub_hallucinations
from .tool_activity import describe_tool_activity
from .turn_progress import report_turn_phase


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
_SERIAL_TOOLS = {
    "run_bash", "edit_file", "write_file",
    "click_at", "click_element", "click_menu", "key_press", "type_text",
    "launch_app", "focus_app", "quit_app", "applescript", "shortcut_run",
    "clipboard_set", "mac_control", "speck",
    # context — builds/shared state
    "resolve_context", "read_bundle",
    # subagent — spawns independent agent instance (uses shared client + state)
    "spawn_subagent",
    # JSON-backed storage — file-level read/write races when run in parallel
    "memory_save", "memory_delete",
    "lesson_save", "lesson_delete",
    "skill_list", "skill_load",
}

# All MCP-prefixed tools are treated as serial (stateful) too.
from ..mcp.registry import is_mcp_tool

# Context tools get a much higher output limit since they bundle many files at once.
_CONTEXT_TOOL_NAMES = {"resolve_context", "read_bundle"}

def _run_tool(b):
    icon = TOOL_ICONS.get(b.name, "🔧")
    args_preview = json.dumps(b.input, ensure_ascii=False)[:120]
    report_turn_phase(describe_tool_activity(b.name, b.input))
    try:
        out = FUNC[b.name](**b.input)
    except Exception as e:
        out = f"ERROR: {type(e).__name__}: {e}"
    return b, icon, args_preview, str(out)


def _run_parallel_batch(batch, outputs):
    if not batch:
        return
    if len(batch) > 1:
        workers = min(MAX_PARALLEL_TOOLS, len(batch))
        if state.show_internal:
            console.print(f"[cyan]⚡ running {len(batch)} tools in parallel (max {workers} workers)[/]")
        with ThreadPoolExecutor(max_workers=workers) as ex:
            for b, icon, ap, out_str in ex.map(_run_tool, batch):
                outputs[b.id] = (icon, ap, out_str)
    else:
        b, icon, ap, out_str = _run_tool(batch[0])
        outputs[b.id] = (icon, ap, out_str)


def render_assistant(resp) -> bool:
    """Print assistant content, execute any tool calls, return True if more turns needed."""
    report_turn_phase("Jarvis: applying model output (text & tool plan)…")
    _model_label = assistant_model_label()
    panel_title = f"Jarvis [{_model_label}]"

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
            if was_flagged:
                console.print("[dim red]⚠ hallucination guard triggered — sentence(s) removed[/]")
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
                continue
            console.print(Panel(
                Markdown(text),
                title=panel_title,
                border_style="magenta",
                padding=(0, 1),
            ))

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
    # streaming path renders them inside assistant_stream_commit above).
    if state.show_internal:
        for thinking in thinking_blocks:
            console.print(Panel(
                thinking,
                title="thinking",
                border_style="dim",
                padding=(0, 1),
            ))

    # Execute collected tool calls: parallel-safe ones concurrently,
    # unsafe/stateful ones serially in their original order. Results are
    # emitted back in the original order so tool_use_id pairing stays intact.
    if tool_uses:
        outputs = {}  # b.id -> (icon, args_preview, out_str)
        parallel_batch = []

        for b in tool_uses:
            if b.name in _SERIAL_TOOLS or is_mcp_tool(b.name):
                _run_parallel_batch(parallel_batch, outputs)
                parallel_batch = []
                _, icon, ap, out_str = _run_tool(b)
                outputs[b.id] = (icon, ap, out_str)
            else:
                parallel_batch.append(b)
        _run_parallel_batch(parallel_batch, outputs)

        for b in tool_uses:
            icon, ap, out_str = outputs[b.id]
            if state.show_internal:
                console.print(f"{icon} [yellow]{b.name}[/] [dim]{ap}[/]")
                if re.search(r"\S", out_str):
                    short = out_str.strip()[:400] + ("…" if len(out_str.strip()) > 400 else "")
                    # Wrap in Text so stray brackets in tool output (e.g. URLs,
                    # JSON fragments) aren't interpreted as Rich markup tags.
                    console.print(Panel(Text(short), border_style="dim", padding=(0, 1)))
            tool_results.append({
                "type": "tool_result",
                "tool_use_id": b.id,
                # Context tools (resolve_context, read_bundle) return the FULL
                # output — no truncation. Everything else gets the standard cap.
                "content": out_str if b.name in _CONTEXT_TOOL_NAMES else out_str[:MAX_TOOL_OUTPUT],
            })

    if tool_results:
        state.messages.append({"role": "user", "content": tool_results})
        return True
    return False
