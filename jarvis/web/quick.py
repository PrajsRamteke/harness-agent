"""``jw`` — Jarvis in the browser, with no terminal to keep open.

    jw [folder]          open the folder's Jarvis in your browser; if none runs
                         there, start one with no terminal (``jarvis --headless``)
    jw --new [folder]    start another one there even if one runs
    jw --no-open         start / find it, print the link, don't open a browser
    jw ls                list the Jarvis web remotes running on this computer
    jw stop [folder]     stop the browser-only Jarvis in that folder
    jw stop --all        stop every browser-only Jarvis

``jarvis web …`` is the same command. The Jarvis it starts is the one the web
page's "Open a folder" starts (``launcher.open_project``): its own session, so
it keeps running after this command — and the terminal it ran in — is gone.
A terminal Jarvis is never stopped from here; quit it in its terminal.
"""
from __future__ import annotations

import argparse
import os
import sys
import time
from pathlib import Path
from typing import Any

from . import fs_api, launcher, registry

PROG = "jw"


def _out(msg: str = "") -> None:
    print(msg, flush=True)


def _err(msg: str) -> None:
    print(f"{PROG}: {msg}", file=sys.stderr, flush=True)


def local_url(rec: dict[str, Any]) -> str:
    """The loopback link for a registry entry (token included, like ``jarvis --web`` prints)."""
    return f"http://127.0.0.1:{int(rec.get('port') or 0)}/?token={rec.get('token') or ''}"


def _kind(rec: dict[str, Any]) -> str:
    return "browser-only" if rec.get("headless") else "terminal"


def _in_folder(path: Path) -> list[dict[str, Any]]:
    real = os.path.realpath(path)
    return [r for r in registry.list_instances()
            if r.get("cwd") and os.path.realpath(str(r["cwd"])) == real]


def _resolve(raw: str | None) -> Path | None:
    try:
        return fs_api.resolve_dir(raw, default=os.getcwd())
    except fs_api.FsError as exc:
        _err(str(exc))
        return None


def _open_browser(url: str) -> bool:
    import webbrowser

    try:
        return bool(webbrowser.open(url))
    except Exception:
        return False


_STAGES = {"spawn": "starting", "boot": "loading", "ready": "ready"}


def _wait_for(launch_id: str, label: str) -> dict[str, Any] | None:
    """Follow a launch until it's ready or failed; a one-line progress note on a TTY."""
    tty = sys.stderr.isatty()
    frames = "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏"
    i = 0
    st: dict[str, Any] | None = None
    deadline = time.time() + launcher.LAUNCH_TIMEOUT + 10
    while time.time() < deadline:
        st = launcher.launch_status(launch_id)
        if st is None or st["status"] != "starting":
            break
        if tty:
            stage = _STAGES.get(st.get("stage", ""), "starting")
            sys.stderr.write(f"\r\x1b[2K{frames[i % len(frames)]} Jarvis in {label} · {stage} · {st['elapsed']:.0f}s")
            sys.stderr.flush()
            i += 1
        time.sleep(0.12)
    if tty:
        sys.stderr.write("\r\x1b[2K")
        sys.stderr.flush()
    return st


def cmd_open(args: argparse.Namespace) -> int:
    path = _resolve(args.folder)
    if path is None:
        return 2
    label = fs_api.display(path)
    rec: dict[str, Any] | None = None
    if not args.new:
        running = _in_folder(path)
        if running:
            rec = running[0]
            _out(f"🌐 Jarvis already runs in {label} ({_kind(rec)})")
    if rec is None:
        res = launcher.open_project(str(path), reuse=False)
        if not res.get("ok"):
            _err(res.get("error") or "couldn't start Jarvis")
            return 1
        st = _wait_for(res["launch"]["id"], label)
        if not st or st["status"] != "ready":
            _err((st or {}).get("error") or "Jarvis didn't start")
            if st and st.get("log"):
                print(st["log"], file=sys.stderr)
            return 1
        rec = registry.get(st["instance_id"])
        if rec is None:
            _err("Jarvis started but stopped again right away")
            return 1
        _out(f"🌐 Jarvis started in {label} — no terminal needed (stop it: {PROG} stop)")
    url = local_url(rec)
    _out(f"   {url}")
    if args.no_open:
        return 0
    if _open_browser(url):
        _out("   opened in your browser")
    else:
        _out("   couldn't open a browser — paste the link into one")
    return 0


def cmd_ls(_args: argparse.Namespace) -> int:
    rows = registry.list_instances()
    if not rows:
        _out(f"No Jarvis web remote is running. Start one: {PROG}")
        return 0
    for rec in rows:
        where = fs_api.display(str(rec.get("cwd") or "?"))
        title = str(rec.get("session_title") or rec.get("title") or "").strip()
        busy = " · working" if rec.get("busy") else ""
        extra = f" · {title}" if title else ""
        _out(f"{_kind(rec):<12}  {where}{extra}{busy}")
        _out(f"{'':<12}  {local_url(rec)}")
    return 0


def cmd_stop(args: argparse.Namespace) -> int:
    if args.all:
        targets = registry.list_instances()
        scope = "on this computer"
    else:
        path = _resolve(args.folder)
        if path is None:
            return 2
        targets = _in_folder(path)
        scope = f"in {fs_api.display(path)}"
    mine = [r for r in targets if r.get("headless")]
    terminals = [r for r in targets if not r.get("headless")]
    if not mine:
        _out(f"No browser-only Jarvis is running {scope}.")
    code = 0
    for rec in mine:
        res = launcher.stop_project(str(rec["id"]))
        where = fs_api.display(str(rec.get("cwd") or "?"))
        if res.get("ok"):
            _out(f"stopped  {where}")
        else:
            _err(f"{where}: {res.get('error')}")
            code = 1
    for rec in terminals:
        _out(f"left running  {fs_api.display(str(rec.get('cwd') or '?'))} — it runs in a terminal; quit it there")
    return code


def _parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog=PROG,
        description="Open Jarvis in your browser — no terminal to keep open.",
        epilog=(
            "commands:\n"
            f"  {PROG} [folder]          open (or start) the folder's Jarvis in your browser\n"
            f"  {PROG} ls                list the Jarvis web remotes running here\n"
            f"  {PROG} stop [folder]     stop the browser-only Jarvis in a folder (--all: every one)\n"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument("folder", nargs="?", help="project folder (default: the current one)")
    p.add_argument("-n", "--new", action="store_true", help="start another Jarvis even if one runs there")
    p.add_argument("--no-open", action="store_true", help="print the link instead of opening a browser")
    return p


def _stop_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog=f"{PROG} stop", description="Stop browser-only Jarvis.")
    p.add_argument("folder", nargs="?", help="project folder (default: the current one)")
    p.add_argument("-a", "--all", action="store_true", help="stop every browser-only Jarvis")
    return p


def run(argv: list[str]) -> int:
    if argv and argv[0] in ("ls", "list"):
        return cmd_ls(argparse.Namespace())
    if argv and argv[0] == "stop":
        return cmd_stop(_stop_parser().parse_args(argv[1:]))
    if argv and argv[0] == "open":
        argv = argv[1:]
    return cmd_open(_parser().parse_args(argv))


def main() -> None:
    try:
        raise SystemExit(run(sys.argv[1:]))
    except KeyboardInterrupt:
        # Ctrl+C while it starts: the Jarvis keeps starting on its own; say how to find it.
        print(file=sys.stderr)
        _err(f"stopped waiting — `{PROG} ls` shows it once it's up")
        raise SystemExit(130)


if __name__ == "__main__":
    main()
