"""Render assistant response: text panels, thinking blocks, and tool calls."""
import json, re

from ..console import console, Panel, Markdown
from ..constants import TOOL_ICONS, MAX_TOOL_OUTPUT
from ..tools import FUNC
from .. import state
from .hallucination import _scrub_hallucinations


def render_assistant(resp) -> bool:
    """Print assistant content, execute any tool calls, return True if more turns needed."""
    # Build a friendly short model label: "Sonnet", "Opus", "Haiku" etc.
    _m = state.MODEL.lower()
    if "opus"   in _m: _model_label = "Opus"
    elif "sonnet" in _m: _model_label = "Sonnet"
    elif "haiku"  in _m: _model_label = "Haiku"
    else:                _model_label = state.MODEL

    tool_results = []
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
            if re.search(r"\S", thinking):
                console.print(Panel(
                    thinking.strip(),
                    title="thinking",
                    border_style="dim",
                    padding=(0, 1),
                ))

        # ── tool call ────────────────────────────────────────────────
        elif b.type == "tool_use":
            state.tool_calls_count += 1
            if b.name in ("web_search", "fetch_url", "verified_search"):
                state.web_tool_used_this_turn = True
            icon = TOOL_ICONS.get(b.name, "🔧")
            args_preview = json.dumps(b.input, ensure_ascii=False)[:120]
            console.print(f"{icon} [yellow]{b.name}[/] [dim]{args_preview}[/]")
            try:
                out = FUNC[b.name](**b.input)
            except Exception as e:
                out = f"ERROR: {type(e).__name__}: {e}"
            out_str = str(out)
            if re.search(r"\S", out_str):
                short = out_str.strip()[:400] + ("…" if len(out_str.strip()) > 400 else "")
                console.print(Panel(short, border_style="dim", padding=(0, 1)))
            tool_results.append({
                "type": "tool_result",
                "tool_use_id": b.id,
                "content": out_str[:MAX_TOOL_OUTPUT],
            })

    if tool_results:
        state.messages.append({"role": "user", "content": tool_results})
        return True
    return False
