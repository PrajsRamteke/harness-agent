"""Render assistant response: text panels, thinking blocks, and tool calls."""
import json, re
from concurrent.futures import ThreadPoolExecutor

from rich.text import Text

from ..console import console, Panel, Markdown
from ..constants import TOOL_ICONS, MAX_TOOL_OUTPUT, MAX_PARALLEL_TOOLS
from ..tools import FUNC
from .. import state
from .hallucination import _scrub_hallucinations

# Tools that must run single-threaded — they mutate shared state, cwd, or UI.
_SERIAL_TOOLS = {
    "run_bash", "edit_file", "write_file",
    "click_at", "click_element", "click_menu", "key_press", "type_text",
    "launch_app", "focus_app", "quit_app", "applescript", "shortcut_run",
    "clipboard_set", "mac_control",
}

def _run_tool(b):
    icon = TOOL_ICONS.get(b.name, "🔧")
    args_preview = json.dumps(b.input, ensure_ascii=False)[:120]
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
    # Build a friendly short model label: "Sonnet", "Opus", "Haiku" etc.
    _m = state.MODEL.lower()
    if "opus"   in _m: _model_label = "Opus"
    elif "sonnet" in _m: _model_label = "Sonnet"
    elif "haiku"  in _m: _model_label = "Haiku"
    else:                _model_label = state.MODEL

    tool_results = []
    tool_uses = []  # collect, then run in parallel where safe
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
                continue
            state.last_assistant_text = text
            console.print(Panel(
                Markdown(text),
                title=f"Jarvis [{_model_label}]",
                border_style="magenta",
                padding=(0, 1),
            ))

        # ── thinking block ───────────────────────────────────────────
        elif b.type == "thinking":
            thinking = b.thinking or ""
            if state.show_internal and re.search(r"\S", thinking):
                console.print(Panel(
                    thinking.strip(),
                    title="thinking",
                    border_style="dim",
                    padding=(0, 1),
                ))

        # ── tool call (collect now, run below in parallel) ───────────
        elif b.type == "tool_use":
            state.tool_calls_count += 1
            if b.name in ("web_search", "fetch_url", "verified_search"):
                state.web_tool_used_this_turn = True
            tool_uses.append(b)

    # Execute collected tool calls: parallel-safe ones concurrently,
    # unsafe/stateful ones serially in their original order. Results are
    # emitted back in the original order so tool_use_id pairing stays intact.
    if tool_uses:
        outputs = {}  # b.id -> (icon, args_preview, out_str)
        parallel_batch = []

        for b in tool_uses:
            if b.name in _SERIAL_TOOLS:
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
                "content": out_str[:MAX_TOOL_OUTPUT],
            })

    if tool_results:
        state.messages.append({"role": "user", "content": tool_results})
        return True
    return False
