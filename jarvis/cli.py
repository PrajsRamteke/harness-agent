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


def _sync_update_before_start() -> None:
    """Pull + pip install before UI loads; re-exec so new code actually runs."""
    if os.environ.get("HARNESS_UPDATED_REEXEC"):
        _restore_update_banner()
        return

    from .updater import check_and_update
    from .install_sync import reexec_jarvis

    result = check_and_update()
    if not result or not result.get("updated"):
        return

    # Git pull changed files on disk but this process still has old modules
    # in memory — must re-exec or Harness Agent models won't appear until
    # the user manually quits and restarts (friends never do).
    banner = {
        "count": result.get("count", 0),
        "commits": result.get("commits", []),
        "pip_installed": result.get("pip_installed", False),
    }
    reexec_jarvis(update_banner=banner)


def main() -> None:
    """Start Jarvis.

    By default this launches the Textual TUI, matching `python agent.py`.
    Pass ``-p`` to run one task without opening the TUI.
    Pass ``--legacy`` to use the older rich REPL.
    """
    _sync_update_before_start()

    args = _build_parser().parse_args()

    if args.run_prompt:
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
