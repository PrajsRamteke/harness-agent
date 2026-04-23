"""Central slash-command dispatcher.

Returns a tuple (consumed, should_send, inp) where:
  - consumed: True if the input was a slash command (handled or unknown)
  - should_send: True if caller should send `inp` as a user message
  - inp: possibly-updated input text to send
"""
from ..console import console
from .help import cmd_help
from .session import cmd_session
from .files_shell import handle_files_shell
from .context import handle_context
from .history import handle_history
from .control import handle_control
from .memory import handle_memory

# commands that set `inp` for sending
FALLTHROUGH = {"/retry", "/paste", "/multi"}


def handle_slash(inp: str):
    parts = inp.split(maxsplit=1)
    c = parts[0]
    arg = parts[1] if len(parts) > 1 else ""

    if c == "/exit":
        return ("exit", False, inp)
    if c == "/help":
        cmd_help(); return ("ok", False, inp)
    if c in ("/session", "/sessions"):
        cmd_session(arg); return ("ok", False, inp)

    handled, _ = handle_memory(c, arg)
    if handled:
        return ("ok", False, inp)

    # grouped handlers
    if handle_files_shell(c, arg):
        return ("ok", False, inp)

    handled, new_inp = handle_history(c, arg)
    if handled:
        if c in FALLTHROUGH and new_inp is not None:
            return ("ok", True, new_inp)
        return ("ok", False, inp)

    handled, new_inp = handle_context(c, arg)
    if handled:
        if c in FALLTHROUGH and new_inp is not None:
            return ("ok", True, new_inp)
        return ("ok", False, inp)

    handled, new_inp = handle_control(c, arg)
    if handled:
        if c in FALLTHROUGH and new_inp is not None:
            return ("ok", True, new_inp)
        return ("ok", False, inp)

    console.print(f"[red]unknown: {c}[/]  (/help)")
    return ("ok", False, inp)
