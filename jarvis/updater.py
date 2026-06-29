"""Auto-update: pull from origin + editable pip install on startup."""
from __future__ import annotations

import os
import pathlib
import subprocess
import time

from .constants import CONFIG_DIR
from .install_sync import (
    find_install_root,
    harness_agent_models_available,
    pip_install_repo,
    sync_repo_to_remote,
)


UPDATE_CHECK_STAMP = CONFIG_DIR / "last_update_check"
# 0 = check on every launch (background — does not block the TUI prompt).
_DEFAULT_UPDATE_INTERVAL_SEC = 0


def _update_interval_sec() -> int:
    raw = os.getenv("HARNESS_UPDATE_INTERVAL", "").strip()
    if raw:
        try:
            return max(60, int(raw))
        except ValueError:
            pass
    return _DEFAULT_UPDATE_INTERVAL_SEC


def should_check_for_updates(*, now: float | None = None) -> bool:
    """Return False when auto-update should be skipped (env or recent check)."""
    if os.environ.get("HARNESS_SKIP_UPDATE", "").strip().lower() in ("1", "true", "yes"):
        return False
    if _update_interval_sec() <= 0:
        return True
    if not UPDATE_CHECK_STAMP.exists():
        return True
    try:
        last = float(UPDATE_CHECK_STAMP.read_text(encoding="utf-8").strip())
    except (OSError, ValueError):
        return True
    ts = now if now is not None else time.time()
    return (ts - last) >= _update_interval_sec()


def _record_update_check(*, now: float | None = None) -> None:
    try:
        UPDATE_CHECK_STAMP.parent.mkdir(parents=True, exist_ok=True)
        UPDATE_CHECK_STAMP.write_text(
            str(now if now is not None else time.time()),
            encoding="utf-8",
        )
    except OSError:
        pass


def _git(*args: str, cwd: pathlib.Path, timeout: int = 30) -> tuple[int, str]:
    try:
        r = subprocess.run(
            ["git", *args],
            cwd=cwd,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        return r.returncode, r.stdout.strip()
    except Exception:
        return 1, ""


def check_and_update() -> dict | None:
    """Fetch + pull when behind, then ``pip install -e .``. Returns update info."""
    try:
        if not should_check_for_updates():
            return None

        root = find_install_root()
        if root is None:
            return None

        _record_update_check()

        code, old_head = _git("rev-parse", "HEAD", cwd=root, timeout=10)
        if code != 0 or not old_head:
            return None

        code, _ = _git("fetch", "origin", cwd=root, timeout=30)
        if code != 0:
            return None

        code, upstream = _git(
            "rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{u}",
            cwd=root, timeout=10,
        )
        if code != 0 or not upstream:
            for candidate in ("origin/main", "origin/master"):
                c, _ = _git("rev-parse", candidate, cwd=root, timeout=10)
                if c == 0:
                    upstream = candidate
                    break
            else:
                return None

        code, remote_head = _git("rev-parse", upstream, cwd=root, timeout=10)
        if code != 0 or not remote_head or remote_head == old_head:
            return None

        code, behind_str = _git(
            "rev-list", "--count", f"HEAD..{upstream}",
            cwd=root, timeout=10,
        )
        behind = int(behind_str) if code == 0 and behind_str.isdigit() else 0
        if behind == 0:
            return None

        code, log_out = _git(
            "log", "--oneline", f"HEAD..{upstream}",
            cwd=root, timeout=10,
        )
        new_commits = [line.strip() for line in log_out.splitlines() if line.strip()]

        branch = upstream.removeprefix("origin/")
        sync = sync_repo_to_remote(root, branch=branch, fetch_timeout=30, sync_timeout=60)
        if not sync.ok:
            return None

        pip_ok = pip_install_repo(root)
        if not pip_ok:
            # Retry once — friend installs often hit transient pip errors.
            pip_ok = pip_install_repo(root, timeout=240)

        result = {
            "updated": True,
            "count": behind,
            "commits": new_commits,
            "pip_installed": pip_ok,
            "pip_ok": pip_ok,
            "harness_models": harness_agent_models_available(),
        }

        import jarvis.state as _state
        _state.update_result = result
        return result

    except Exception:
        return None


def force_update() -> dict:
    """Force an update regardless of the check interval/stamp (for ``jarvis update``).

    Always returns a status dict describing the outcome (never ``None``)::

        {"status": "no_repo" | "git_error" | "up_to_date" | "sync_failed"
                    | "updated",
         "version": <current VERSION>,
         ... (extra keys per status)}
    """
    from .constants.models import VERSION

    base = {"version": VERSION}

    root = find_install_root()
    if root is None:
        return {**base, "status": "no_repo",
                "error": "could not locate the jarvis git checkout"}

    code, old_head = _git("rev-parse", "HEAD", cwd=root, timeout=10)
    if code != 0 or not old_head:
        return {**base, "status": "git_error", "error": "git rev-parse HEAD failed"}

    code, _ = _git("fetch", "origin", cwd=root, timeout=60)
    if code != 0:
        return {**base, "status": "git_error", "error": "git fetch origin failed"}

    code, upstream = _git(
        "rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{u}",
        cwd=root, timeout=10,
    )
    if code != 0 or not upstream:
        for candidate in ("origin/main", "origin/master"):
            c, _ = _git("rev-parse", candidate, cwd=root, timeout=10)
            if c == 0:
                upstream = candidate
                break
        else:
            return {**base, "status": "git_error",
                    "error": "no upstream branch found"}

    code, remote_head = _git("rev-parse", upstream, cwd=root, timeout=10)
    if code != 0 or not remote_head:
        return {**base, "status": "git_error",
                "error": f"git rev-parse {upstream} failed"}

    code, behind_str = _git(
        "rev-list", "--count", f"HEAD..{upstream}", cwd=root, timeout=10,
    )
    behind = int(behind_str) if code == 0 and behind_str.isdigit() else 0

    if remote_head == old_head or behind == 0:
        return {**base, "status": "up_to_date", "head": old_head[:7]}

    code, log_out = _git("log", "--oneline", f"HEAD..{upstream}", cwd=root, timeout=10)
    new_commits = [line.strip() for line in log_out.splitlines() if line.strip()]

    branch = upstream.removeprefix("origin/")
    sync = sync_repo_to_remote(root, branch=branch, fetch_timeout=60, sync_timeout=120)
    if not sync.ok:
        return {**base, "status": "sync_failed", "error": sync.error,
                "count": behind, "commits": new_commits}

    pip_ok = pip_install_repo(root)
    if not pip_ok:
        pip_ok = pip_install_repo(root, timeout=240)

    _record_update_check()

    return {
        **base,
        "status": "updated",
        "count": behind,
        "commits": new_commits,
        "pip_installed": pip_ok,
        "pip_ok": pip_ok,
        "old_head": old_head[:7],
        "new_head": remote_head[:7],
        "harness_models": harness_agent_models_available(),
    }


def _update_banner_from_result(result: dict) -> dict:
    return {
        "count": result.get("count", 0),
        "commits": result.get("commits", []),
        "pip_installed": result.get("pip_installed", False),
    }


def maybe_update_and_reexec() -> None:
    """Fetch/pull when behind; ``exec`` fresh process so new code loads. No-op when up to date."""
    if os.environ.get("HARNESS_UPDATED_REEXEC"):
        return

    result = check_and_update()
    if not result or not result.get("updated"):
        return

    from .install_sync import reexec_jarvis

    reexec_jarvis(update_banner=_update_banner_from_result(result))


def start_background_update() -> None:
    """Check for updates on a worker thread — startup stays instant, re-exec when pulled."""
    if os.environ.get("HARNESS_SKIP_UPDATE", "").strip().lower() in ("1", "true", "yes"):
        return
    if os.environ.get("HARNESS_UPDATED_REEXEC"):
        return

    import threading

    threading.Thread(
        target=maybe_update_and_reexec,
        daemon=True,
        name="harness-update",
    ).start()
