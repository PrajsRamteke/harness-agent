"""Command-line entrypoint for Jarvis."""
from __future__ import annotations

import argparse
import json
import os
import sys


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="jarvis",
        description="Start the Jarvis terminal agent.",
    )
    parser.add_argument(
        "--legacy",
        action="store_true",
        help="start the older rich REPL instead of the default TUI",
    )
    parser.add_argument(
        "-p",
        "--prompt",
        dest="run_prompt",
        nargs="+",
        metavar="PROMPT",
        help="run one task headlessly (no TUI), auto-approve shell commands, then exit",
    )
    parser.add_argument(
        "startup_prompt",
        nargs="*",
        metavar="PROMPT",
        help="optional prompt to send immediately when launching the TUI",
    )
    parser.add_argument(
        "--web",
        action="store_true",
        help="enable browser remote control (mobile-friendly web UI)",
    )
    parser.add_argument(
        "--web-port",
        type=int,
        default=None,
        metavar="PORT",
        help="web remote port (default: 8765 or HARNESS_WEB_PORT)",
    )
    return parser


def _restore_update_banner() -> None:
    raw = os.environ.pop("HARNESS_UPDATE_RESULT", None)
    if not raw:
        return
    try:
        from . import state
        state.update_result = json.loads(raw)
    except Exception:
        pass


def _handle_post_reexec_banner() -> None:
    """After a background pull + re-exec, show the update banner once."""
    if os.environ.get("HARNESS_UPDATED_REEXEC"):
        _restore_update_banner()


def main() -> None:
    """Start Jarvis.

    By default this launches the Textual TUI, matching `python agent.py`.
    Pass ``-p`` to run one task without opening the TUI.
    Pass ``--legacy`` to use the older rich REPL.
    """
    _handle_post_reexec_banner()

    args = _build_parser().parse_args()

    if args.run_prompt:
        from .updater import maybe_update_and_reexec
        from .main import run_headless

        # Headless runs are one-shot — sync update so the task uses latest code.
        maybe_update_and_reexec()

        prompt = " ".join(args.run_prompt).strip()
        if not prompt:
            print("jarvis: -p requires a prompt", file=sys.stderr)
            raise SystemExit(2)
        raise SystemExit(run_headless(prompt))

    startup_prompt = " ".join(args.startup_prompt).strip()
    if startup_prompt:
        from . import state

        state.startup_prompt = startup_prompt

    from . import state
    from .web.server import default_web_port, web_enabled_from_env

    state.web_enabled = bool(args.web or web_enabled_from_env())
    state.web_port = args.web_port if args.web_port is not None else default_web_port()

    from .bootstrap import ensure_harness_agent_defaults
    ensure_harness_agent_defaults()

    if args.legacy:
        from .main import main as legacy_main

        legacy_main()
    else:
        from .tui.app import run

        run()


if __name__ == "__main__":
    main()
