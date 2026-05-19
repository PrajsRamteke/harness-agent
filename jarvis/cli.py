"""Command-line entrypoint for Jarvis."""
from __future__ import annotations

import argparse
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
        "prompt",
        nargs="*",
        metavar="PROMPT",
        help="optional prompt to send immediately on launch",
    )
    return parser


def main() -> None:
    """Start Jarvis.

    By default this launches the Textual TUI, matching `python agent.py`.
    Pass `--legacy` to use the older rich REPL.
    """
    # Kick off auto-update in background immediately so it runs in parallel
    # with auth resolution. We join (max 8 s) before the UI starts so the
    # welcome banner can show the result without racing.
    from .updater import check_and_update
    _update_thread = threading.Thread(target=check_and_update, daemon=True)
    _update_thread.start()

    args = _build_parser().parse_args()
    startup_prompt = " ".join(args.prompt).strip()
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
