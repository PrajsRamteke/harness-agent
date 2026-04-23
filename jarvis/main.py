"""Entry point: construct client, init DB, run REPL."""
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
from . import state


def main():
    # Build client (sets state.auth_mode internally) before welcome so auth prompts appear first.
    state.client = make_client()

    welcome_banner()
    header_panel()
    db_init()
    state.current_session_id = db_create_session(state.MODEL)

    while True:
        try:
            now_str = datetime.now().strftime("%H:%M")
            console.rule(style="grey37")
            inp = console.input(
                f"[bold yellow]▎[/][dim] {now_str} [/][bold bright_yellow]you[/] [bold yellow]❯[/] "
            ).strip()
        except (EOFError, KeyboardInterrupt):
            console.print("\n[magenta]bye 👋[/]"); break
        if not inp: continue

        # alias expansion: first token may resolve to a saved alias
        if inp.startswith("/"):
            head = inp.split(maxsplit=1)[0]
            if head[1:] in state.aliases:
                rest = inp[len(head):]
                inp = state.aliases[head[1:]] + rest

        if inp.startswith("/"):
            result, should_send, inp = handle_slash(inp)
            if result == "exit":
                break
            if not should_send:
                continue
            # fall through to send `inp` as a user message

        _send_and_loop(inp)


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
            with console.status("[dim]thinking…[/]", spinner="dots"):
                resp = call_claude_stream()
            asst_msg = {"role": "assistant", "content": resp.content}
            state.messages.append(asst_msg)
            if state.current_session_id:
                db_append_message(state.current_session_id, len(state.messages) - 1, asst_msg)
            more = render_assistant(resp)
            if resp.stop_reason == "end_turn" or not more:
                break
            # tool results get appended inside render_assistant via messages — persist the latest
            if state.current_session_id and state.messages and state.messages[-1] is not asst_msg:
                db_append_message(state.current_session_id, len(state.messages) - 1, state.messages[-1])
    except KeyboardInterrupt:
        console.print("\n[yellow]interrupted[/]")
    except Exception as e:
        console.print(f"[red]error: {type(e).__name__}: {e}[/]")


if __name__ == "__main__":
    main()
