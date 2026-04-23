#!/usr/bin/env python3
"""
agent.py — Claude Code-style terminal agent (thin entrypoint).

The implementation lives in the `jarvis` package. See
`jarvis/main.py` for the REPL entry point.
"""
import sys


if __name__ == "__main__":
    if "--legacy" in sys.argv:
        from jarvis.main import main
        main()
    else:
        from jarvis.tui.app import run
        run()
