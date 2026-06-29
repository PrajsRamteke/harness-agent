"""Command-line entrypoint for Jarvis."""
from __future__ import annotations

import argparse
import json
import os
import sys


def _normalize_web_args(argv: list[str]) -> list[str]:
    """Expand ``--web PORT`` into ``--web --web-port PORT``."""
    out: list[str] = []
    i = 0
    while i < len(argv):
        arg = argv[i]
        if arg == "--web" and i + 1 < len(argv):
            nxt = argv[i + 1]
            if nxt.isdigit() and not nxt.startswith("-") and 1 <= int(nxt) <= 65535:
                out.extend(["--web", "--web-port", nxt])
                i += 2
                continue
        out.append(arg)
        i += 1
    return out


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="jarvis",
        description="Start the Jarvis terminal agent.",
        epilog=(
            "commands:\n"
            "  jarvis update           pull the latest version and reinstall\n"
            "  jarvis upgrade          alias for `jarvis update`\n"
            "  jarvis update --check   show available updates without applying them\n"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
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
        help=(
            "enable browser remote control (mobile-friendly web UI); "
            "optional PORT as next arg (e.g. --web 9000)"
        ),
    )
    parser.add_argument(
        "--web-port",
        type=int,
        default=None,
        metavar="PORT",
        help="web remote port (default: 8765 or HARNESS_WEB_PORT; see also --web PORT)",
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


_UPDATE_ALIASES = {"update", "upgrade", "self-update", "selfupdate"}


def run_update_cli(argv: list[str]) -> int:
    """Handle ``jarvis update`` / ``jarvis upgrade`` — pull latest + reinstall.

    ``--check`` reports whether an update is available without applying it.
    Returns a process exit code.
    """
    check_only = any(a in ("--check", "-n", "--dry-run") for a in argv)

    from .updater import force_update

    print("jarvis: checking for updates…", file=sys.stderr)
    result = force_update()
    status = result.get("status")
    version = result.get("version", "?")

    if status == "no_repo":
        print(
            "jarvis: cannot self-update — this install is not a git checkout.\n"
            "        Reinstall with the official installer to enable updates.",
            file=sys.stderr,
        )
        return 1

    if status == "git_error":
        print(f"jarvis: update failed — {result.get('error', 'git error')}", file=sys.stderr)
        return 1

    if status == "up_to_date":
        print(f"jarvis: already up to date (v{version}, {result.get('head', '')}).")
        return 0

    count = result.get("count", 0)
    commits = result.get("commits", [])

    if check_only:
        print(f"jarvis: {count} update(s) available (currently v{version}):")
        for line in commits[:20]:
            print(f"  • {line}")
        if len(commits) > 20:
            print(f"  … (+{len(commits) - 20} more)")
        print("\nRun `jarvis update` to apply.")
        return 0

    if status == "sync_failed":
        print(
            f"jarvis: update failed — {result.get('error', 'sync failed')}",
            file=sys.stderr,
        )
        return 1

    if status == "updated":
        print(f"jarvis: updated {result.get('old_head', '')} → {result.get('new_head', '')} "
              f"({count} commit{'s' if count != 1 else ''}):")
        for line in commits[:20]:
            print(f"  • {line}")
        if len(commits) > 20:
            print(f"  … (+{len(commits) - 20} more)")
        if not result.get("pip_ok", True):
            print(
                "\njarvis: WARNING — `pip install -e .` did not complete cleanly.\n"
                "        Try re-running `jarvis update` or reinstall manually.",
                file=sys.stderr,
            )
            return 1
        print("\njarvis: done. New version is live on next launch.")
        return 0

    print(f"jarvis: unexpected update status: {status}", file=sys.stderr)
    return 1


def main() -> None:
    """Start Jarvis.

    By default this launches the Textual TUI, matching `python agent.py`.
    Pass ``-p`` to run one task without opening the TUI.
    Pass ``--legacy`` to use the older rich REPL.
    """
    _handle_post_reexec_banner()

    argv = sys.argv[1:]
    if argv and argv[0] in _UPDATE_ALIASES:
        raise SystemExit(run_update_cli(argv[1:]))

    args = _build_parser().parse_args(_normalize_web_args(argv))

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
