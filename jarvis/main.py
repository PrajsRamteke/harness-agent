"""Entry point: construct client, init DB, run REPL or one-shot headless runs."""
from datetime import datetime

from .console import console, Panel
from .auth.client import make_client
from .storage.sessions import (
    db_init, db_create_session, db_append_message, db_set_title_if_empty,
)
from .repl.banners import welcome_banner, header_panel
from .repl.stream import call_claude_stream
from .repl.render import render_assistant
from .commands.dispatch import handle_slash
from .tools.shell import run_bash
from .constants import PANEL_PREVIEW_CHARS
from .tools.image_input import (
    append_image_block,
    clipboard_image_to_file,
    extract_image_paths,
    file_digest,
    ocr_image_block,
    process_input_for_images,
)
from . import state


def init_runtime(*, quiet: bool = False) -> None:
    """Shared startup for interactive REPL, TUI, and headless runs."""
    state.client = make_client()
    if not quiet:
        welcome_banner()
        header_panel()
    db_init()
    state.current_session_id = db_create_session(state.MODEL)

    from .mcp.registry import auto_connect_servers

    if quiet:
        auto_connect_servers(console_print=lambda *_a, **_k: None)
    else:
        auto_connect_servers(console_print=console.print)

    from .project_context import detect_project_context

    detect_project_context()

    from .storage.agents import auto_activate_coding_agent

    auto_activate_coding_agent()


def prepare_user_prompt(inp: str, *, include_clipboard: bool = True) -> str | None:
    """Normalize one user prompt before sending (aliases, slash, @files, images)."""
    inp = (inp or "").strip()
    if not inp:
        return None

    if inp.startswith("/"):
        head = inp.split(maxsplit=1)[0]
        if head[1:] in state.aliases:
            rest = inp[len(head):]
            inp = state.aliases[head[1:]] + rest

    if inp.startswith("!"):
        cmd = inp[1:].strip()
        if cmd:
            prev = state.auto_approve
            state.auto_approve = True
            console.print(run_bash(cmd))
            state.auto_approve = prev
        return None

    if inp.startswith("/"):
        result, should_send, inp = handle_slash(inp)
        if result == "exit":
            return None
        if not should_send:
            return None

    from .prompt_refs import expand_file_refs
    from .prompt_attachments import expand_attachment_tokens, reset_registry

    expanded, attached = expand_file_refs(inp)
    if attached:
        console.print(f"[dim]▣ attached {len(attached)} file(s): {', '.join(attached)}[/]")
    inp = expanded

    expanded, dropped = expand_attachment_tokens(inp)
    if dropped:
        console.print(f"[dim]▣ dropped {len(dropped)} file(s): {', '.join(dropped)}[/]")
    inp = expanded
    reset_registry()

    if not dropped:
        hits = extract_image_paths(inp)
        if hits:
            names = ", ".join(p.name for _, p in hits)
            console.print(f"[dim]▣ detected image(s): {names} — running OCR…[/]")
            inp = process_input_for_images(inp)
        elif include_clipboard:
            img = clipboard_image_to_file()
            if img is None:
                state.last_clipboard_image_digest = ""
            else:
                digest = file_digest(img)
                if digest != state.last_clipboard_image_digest:
                    state.last_clipboard_image_digest = digest
                    console.print(f"[dim]▣ fresh clipboard image detected → OCR ({img})[/]")
                    block, ocr = ocr_image_block(img, label="clipboard")
                    inp = append_image_block(inp, block)
                    console.print(
                        Panel(
                            ocr[:PANEL_PREVIEW_CHARS]
                            + ("…" if len(ocr) > PANEL_PREVIEW_CHARS else ""),
                            title="▣ attached clipboard image (OCR)",
                            border_style="cyan",
                        )
                    )

    return inp


def run_headless(prompt: str) -> int:
    """Run one task without launching the TUI, then exit."""
    init_runtime(quiet=True)
    state.auto_approve = True
    console.print(f"[bold cyan]jarvis[/] [dim]→[/] {prompt}")
    prepared = prepare_user_prompt(prompt, include_clipboard=False)
    if not prepared:
        return 1
    try:
        _send_and_loop(prepared)
    except KeyboardInterrupt:
        console.print("\n[yellow]interrupted[/]")
        return 130
    except Exception as e:
        console.print(f"[red]error: {type(e).__name__}: {e}[/]")
        return 1
    return 0


def main():
    init_runtime(quiet=False)

    pending_startup = state.startup_prompt.strip()
    state.startup_prompt = ""

    while True:
        try:
            # Process queued prompts (from TUI /script injection) before fresh input
            if state.prompt_queue:
                item = state.prompt_queue.pop(0)
                if isinstance(item, tuple):
                    inp = item[0]
                    if len(item) > 1 and isinstance(item[1], tuple):
                        attachments, llm_paths = item[1]
                    else:
                        attachments, llm_paths = item[1], None
                    from .prompt_attachments import restore_registry
                    restore_registry(attachments, llm_paths)
                else:
                    inp = item
                remaining = len(state.prompt_queue)
                console.print(
                    f"[bold #58a6ff]⏭ from queue ({remaining} remaining)[/]"
                    if remaining else "[bold #58a6ff]⏭ from queue[/]"
                )
            elif pending_startup:
                inp = pending_startup
                pending_startup = ""
                now_str = datetime.now().strftime("%H:%M")
                console.rule(style="grey37")
                console.print(
                    f"[bold yellow]▎[/][dim] {now_str} [/][bold bright_yellow]you[/] "
                    f"[bold yellow]❯[/] {inp}"
                )
            else:
                now_str = datetime.now().strftime("%H:%M")
                console.rule(style="grey37")
                inp = console.input(
                    f"[bold yellow]▎[/][dim] {now_str} [/][bold bright_yellow]you[/] [bold yellow]❯[/] "
                ).strip()
        except (EOFError, KeyboardInterrupt):
            console.print("\n[magenta]bye ♪[/]"); break
        if not inp:
            continue

        prepared = prepare_user_prompt(inp, include_clipboard=True)
        if prepared is None:
            continue
        _send_and_loop(prepared)


def _send_and_loop(inp: str):
    """Append the user message and run the tool-call loop until end_turn."""
    user_msg = {"role": "user", "content": inp}
    state.messages.append(user_msg)
    state.web_tool_used_this_turn = False
    if state.current_session_id:
        db_append_message(state.current_session_id, len(state.messages) - 1, user_msg)
        db_set_title_if_empty(state.current_session_id, inp)
    try:
        while True:
            if state.cancel_requested.is_set():
                raise KeyboardInterrupt()
            with console.status("[dim]thinking…[/]", spinner="dots"):
                resp = call_claude_stream()
            asst_msg = {"role": "assistant", "content": resp.content}
            state.messages.append(asst_msg)
            if state.current_session_id:
                db_append_message(state.current_session_id, len(state.messages) - 1, asst_msg)
            more = render_assistant(resp)
            if resp.stop_reason == "end_turn" or not more:
                break
            # Check cancel after tool execution too — if set during
            # render_assistant tool calls, break out cleanly
            if state.cancel_requested.is_set():
                raise KeyboardInterrupt()
            # tool results get appended inside render_assistant via messages — persist the latest
            if state.current_session_id and state.messages and state.messages[-1] is not asst_msg:
                db_append_message(state.current_session_id, len(state.messages) - 1, state.messages[-1])
    except KeyboardInterrupt:
        console.print("\n[yellow]interrupted[/]")
    except Exception as e:
        console.print(f"[red]error: {type(e).__name__}: {e}[/]")


if __name__ == "__main__":
    main()
