#!/usr/bin/env python3
"""
agent.py — Terminal agent (thin entrypoint).

The implementation lives in the `jarvis` package. See
`jarvis/main.py` for the REPL entry point.
"""


if __name__ == "__main__":
    from jarvis.cli import main

    main()
