"""Command-line entrypoint for Jarvis."""
from __future__ import annotations

import argparse
import sys
import threading


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
    return parser


def main() -> None:
    """Start Jarvis.

    By default this launches the Textual TUI, matching `python agent.py`.
    Pass ``-p`` to run one task without opening the TUI.
    Pass ``--legacy`` to use the older rich REPL.
    """
    # Kick off auto-update in background immediately so it runs in parallel
    # with auth resolution. We join (max 8 s) before the UI starts so the
    # welcome banner can show the result without racing.
    from .updater import check_and_update
    _update_thread = threading.Thread(target=check_and_update, daemon=True)
    _update_thread.start()

    args = _build_parser().parse_args()

    if args.run_prompt:
        _update_thread.join(timeout=8)
        from .main import run_headless

        prompt = " ".join(args.run_prompt).strip()
        if not prompt:
            print("jarvis: -p requires a prompt", file=sys.stderr)
            raise SystemExit(2)
        raise SystemExit(run_headless(prompt))

    startup_prompt = " ".join(args.startup_prompt).strip()
    if startup_prompt:
        from . import state

        state.startup_prompt = startup_prompt

    if args.legacy:
        _update_thread.join(timeout=8)
        from .main import main as legacy_main

        legacy_main()
    else:
        _update_thread.join(timeout=8)
        from .tui.app import run

        run()


if __name__ == "__main__":
    main()
