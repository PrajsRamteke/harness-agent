"""Command-line entrypoint for Jarvis."""
from __future__ import annotations

import argparse


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
    return parser


def main() -> None:
    """Start Jarvis.

    By default this launches the Textual TUI, matching `python agent.py`.
    Pass `--legacy` to use the older rich REPL.
    """
    args = _build_parser().parse_args()

    if args.legacy:
        from .main import main as legacy_main

        legacy_main()
    else:
        from .tui.app import run

        run()


if __name__ == "__main__":
    main()
