"""Subagent spawning — delegate independent tasks to isolated agent instances.

Usage (model-side):
  spawn_subagent(task="Refactor utils.py into helpers/", context="<file contents>")
  spawn_subagent(task="Find all PNGs", tools="glob_files,fast_find", model="claude-haiku-4-5")
"""

from .. import state
from ..constants.models import API_MAX_TOKENS

# Tool output cap for subagent tool results — same as main agent.
_SUBAGENT_TOOL_OUTPUT_CAP = 6000


def spawn_subagent(
    task: str,
    context: str = "",
    tools: str = "",
    model: str = "",
    max_turns: int = 15,
) -> str:
    """Spawn an isolated sub-agent to complete a task independently.

    The sub-agent runs its own tool-call loop with a separate message context.
    It can use any Jarvis tools and returns the completed result as text.

    Args:
        task: The specific task/instruction for the subagent.
        context: Background context the subagent needs (file contents, data,
                 previous results, instructions, etc.). Pass anything relevant
                 so the subagent doesn't need to re-read files you already have.
        tools: Optional comma-separated tool names the subagent is allowed to
               use (e.g. "read_file,run_bash,search_code,write_file,grep").
               Empty = all available tools. Core file/shell tools are always
               included even if not listed.
        model: Optional model override (e.g. "claude-haiku-4-5" for cheap
               sub-tasks, or "claude-opus-4-7" for complex reasoning).
               Defaults to parent's current model.
        max_turns: Maximum tool-call turns before forcing a result.
                   Default 15, max 30.

    Returns:
        The sub-agent's final text output. If the sub-agent fails entirely,
        returns an error string starting with "ERROR:".
    """
    # ── lazy imports (avoid circular at module load) ────────────────────
    from ..repl.system import build_system
    from ..tools.router import select_tools
    from ..tools import FUNC

    # ── clamp inputs ────────────────────────────────────────────────────
    max_turns = min(max(max_turns, 1), 30)
    subagent_model = (model.strip() or state.MODEL) if model else state.MODEL

    # ── build isolated message list ─────────────────────────────────────
    messages: list[dict] = []
    if context:
        messages.append({
            "role": "user",
            "content": f"[Background context]\n{context}",
        })
    messages.append({"role": "user", "content": task})

    # ── tool selection ──────────────────────────────────────────────────
    all_tools = select_tools(state.messages)
    if tools:
        allow = {t.strip() for t in tools.split(",")}
        tool_schemas = [t for t in all_tools if t["name"] in allow]
        # Always include core file/shell/git tools so the subagent can
        # read/write files and execute commands.
        core_names = {
            "read_file", "read_document", "write_file", "edit_file",
            "list_dir", "run_bash", "search_code", "glob_files",
            "rank_files", "fast_find",
            "git_status", "git_diff", "git_log",
        }
        for t in all_tools:
            if t["name"] in core_names and t["name"] not in allow:
                tool_schemas.append(t)
    else:
        tool_schemas = all_tools

    # ── system prompt (same as parent) ──────────────────────────────────
    system = build_system()

    text_output: list[str] = []

    for turn in range(max_turns):
        kwargs = dict(
            model=subagent_model,
            max_tokens=API_MAX_TOKENS,
            system=system,
            messages=messages,
            tools=tool_schemas,
        )

        # ── call API (non-streaming, isolated) ──────────────────────────
        try:
            with state.client.messages.stream(**kwargs) as stream:
                final = stream.get_final_message()
        except Exception as exc:
            err = f"ERROR: sub-agent failed on turn {turn+1}: {type(exc).__name__}: {exc}"
            if text_output:
                text_output.append(f"\n\n[{err}]")
                break
            return err

        # ── collect text from this response ─────────────────────────────
        for block in final.content:
            if block.type == "text" and block.text:
                text_output.append(block.text)

        # ── collect tool calls ──────────────────────────────────────────
        tool_uses = [b for b in final.content if b.type == "tool_use"]
        if not tool_uses:
            break  # end_turn — subagent is done

        # Append assistant message with tool-call blocks
        asst_content: list[dict] = []
        for b in tool_uses:
            asst_content.append({
                "type": "tool_use",
                "id": b.id,
                "name": b.name,
                "input": b.input,
            })
        messages.append({"role": "assistant", "content": asst_content})

        # Execute each tool call and build tool-result blocks
        tool_results: list[dict] = []
        for b in tool_uses:
            try:
                out = FUNC[b.name](**b.input)
            except Exception as exc:
                out = f"ERROR: {type(exc).__name__}: {exc}"
            tool_results.append({
                "type": "tool_result",
                "tool_use_id": b.id,
                "content": str(out)[:_SUBAGENT_TOOL_OUTPUT_CAP],
            })

        messages.append({"role": "user", "content": tool_results})

    return "\n\n".join(text_output).strip()
