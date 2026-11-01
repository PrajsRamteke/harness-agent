"""Git card in the web inspector: open in an editor, switch branch, stash,
commit, push, create a PR, and a model-written commit / PR message.

UI-free. Every git / gh / open call goes through :func:`_run`, so tests swap
in a fake and nothing real is committed or pushed. Runs on the HTTP handler
thread (subprocesses and network), never on the TUI thread; mutations hold
``_lock`` so two clicks can't interleave. Always works in this Jarvis's
folder (``os.getcwd()``) — the page names an operation, never a path.
"""
from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Any

GIT_TIMEOUT = 20.0
NET_TIMEOUT = 180.0
DIFF_MAX_CHARS = 24_000      # what the model sees of the diff
UNTRACKED_MAX_FILES = 300    # untracked files whose lines count towards +N
UNTRACKED_MAX_BYTES = 2_000_000
MESSAGE_TIMEOUT = 90.0

_lock = threading.Lock()


class GitError(Exception):
    """Shown to the user as is."""


def _env() -> dict[str, str]:
    env = dict(os.environ)
    # Never wait on a credential / editor prompt nobody can answer.
    env.update(GIT_TERMINAL_PROMPT="0", GIT_EDITOR="true", GIT_OPTIONAL_LOCKS="0", GH_PROMPT_DISABLED="1")
    return env


def _run(args: list[str], cwd: str, timeout: float = GIT_TIMEOUT) -> tuple[int, str, str]:
    """(exit code, stdout, stderr). Tests replace this."""
    try:
        p = subprocess.run(
            args, cwd=cwd, capture_output=True, text=True, timeout=timeout,
            stdin=subprocess.DEVNULL, env=_env(),
        )
    except FileNotFoundError:
        return 127, "", f"{args[0]} is not installed"
    except subprocess.TimeoutExpired:
        return 124, "", f"{' '.join(args[:3])} took longer than {int(timeout)}s"
    return p.returncode, p.stdout, p.stderr


def _git(cwd: str, *args: str, timeout: float = GIT_TIMEOUT) -> tuple[int, str, str]:
    return _run(["git", *args], cwd, timeout)


def _ok(cwd: str, *args: str, timeout: float = GIT_TIMEOUT) -> str:
    code, out, err = _git(cwd, *args, timeout=timeout)
    if code != 0:
        raise GitError(_first_error(err or out) or f"git {args[0]} failed")
    return out


def _first_error(text: str) -> str:
    lines = [ln.strip() for ln in (text or "").splitlines() if ln.strip()]
    for ln in lines:  # git's own "error:" / "fatal:" line says it best
        if ln.lower().startswith(("error:", "fatal:", "! [rejected]", "hint: updates were rejected")):
            return re.sub(r"^(error|fatal):\s*", "", ln, flags=re.I)[:300]
    return (lines[-1] if lines else "")[:300]


# ─── Editors / "Open in" ──────────────────────────────────────────────────

# id, label, macOS app names to look for, CLI on PATH (other systems)
EDITORS: list[tuple[str, str, tuple[str, ...], str]] = [
    ("cursor", "Cursor", ("Cursor",), "cursor"),
    ("vscode", "VS Code", ("Visual Studio Code",), "code"),
    ("antigravity", "Antigravity", ("Antigravity",), "antigravity"),
    ("windsurf", "Windsurf", ("Windsurf",), "windsurf"),
    ("zed", "Zed", ("Zed",), "zed"),
    ("idea", "IntelliJ IDEA", ("IntelliJ IDEA", "IntelliJ IDEA CE"), "idea"),
    ("pycharm", "PyCharm", ("PyCharm", "PyCharm CE"), "pycharm"),
    ("terminal", "Terminal", ("Terminal",), ""),
    ("finder", "Finder", (), ""),
]


def _mac_app(names: tuple[str, ...]) -> str | None:
    for name in names:
        for root in ("/Applications", str(Path.home() / "Applications"),
                     "/System/Applications", "/System/Applications/Utilities"):
            if Path(root, f"{name}.app").exists():
                return name
    return None


def editors() -> list[dict[str, str]]:
    out = []
    for eid, label, apps, cli in EDITORS:
        if sys.platform == "darwin":
            if eid == "finder" or _mac_app(apps):
                out.append({"id": eid, "label": label})
        elif eid == "finder":
            if shutil.which("xdg-open"):
                out.append({"id": eid, "label": "Files"})
        elif cli and shutil.which(cli):
            out.append({"id": eid, "label": label})
    return out


def open_in(cwd: str, editor_id: str) -> dict[str, Any]:
    spec = next((e for e in EDITORS if e[0] == editor_id), None)
    if spec is None:
        return {"ok": False, "error": "Unknown app"}
    eid, label, apps, cli = spec
    if sys.platform == "darwin":
        if eid == "finder":
            args = ["open", cwd]
        else:
            app = _mac_app(apps)
            if not app:
                return {"ok": False, "error": f"{label} isn't installed on this computer"}
            args = ["open", "-a", app, cwd]
    elif eid == "finder":
        args = ["xdg-open", cwd]
    elif cli and shutil.which(cli):
        args = [cli, cwd]
    else:
        return {"ok": False, "error": f"{label} isn't installed on this computer"}
    code, out, err = _run(args, cwd, timeout=15)
    if code != 0:
        return {"ok": False, "error": _first_error(err or out) or f"Couldn't open {label}"}
    return {"ok": True, "editor": eid, "label": label}


# ─── Status ───────────────────────────────────────────────────────────────

_BRANCH_LINE = re.compile(r"^## (?P<head>.+?)(?:\.\.\.(?P<up>\S+))?(?: \[(?P<track>[^\]]+)\])?$")


def _parse_branch_line(line: str) -> dict[str, Any]:
    m = _BRANCH_LINE.match(line.strip())
    info: dict[str, Any] = {"branch": "", "upstream": "", "ahead": 0, "behind": 0, "gone": False}
    if not m:
        return info
    head = m.group("head")
    if head.startswith("No commits yet on "):
        head = head[len("No commits yet on "):]
    if head.startswith("HEAD (no branch)"):
        head = ""
    info["branch"] = head
    info["upstream"] = m.group("up") or ""
    for part in (m.group("track") or "").split(","):
        part = part.strip()
        if part.startswith("ahead "):
            info["ahead"] = int(part[6:] or 0)
        elif part.startswith("behind "):
            info["behind"] = int(part[7:] or 0)
        elif part == "gone":
            info["gone"] = True
    return info


def _count_lines(path: Path) -> int:
    try:
        if path.stat().st_size > UNTRACKED_MAX_BYTES:
            return 0
        data = path.read_bytes()
    except OSError:
        return 0
    if b"\0" in data[:8000]:
        return 0  # binary
    return data.count(b"\n") + (1 if data and not data.endswith(b"\n") else 0)


def status(cwd: str) -> dict[str, Any]:
    """Everything the card shows. ``repo: False`` outside a git repository."""
    base: dict[str, Any] = {"ok": True, "repo": False, "cwd": cwd, "editors": editors(),
                            "gh": bool(shutil.which("gh"))}
    code, top, _ = _git(cwd, "rev-parse", "--show-toplevel")
    if code != 0:
        return base
    code, out, err = _git(cwd, "status", "--porcelain=v1", "-b", "--untracked-files=all")
    if code != 0:
        return {**base, "repo": True, "error": _first_error(err)}
    lines = out.splitlines()
    info = _parse_branch_line(lines[0] if lines and lines[0].startswith("## ") else "")
    files = [ln for ln in lines if not ln.startswith("## ")]
    untracked = [ln[3:] for ln in files if ln.startswith("??")]
    staged = sum(1 for ln in files if ln[:1] not in (" ", "?"))
    detached = not info["branch"]
    if detached:
        _, sha, _ = _git(cwd, "rev-parse", "--short", "HEAD")
        info["branch"] = sha.strip()
    added = removed = 0
    has_head = _git(cwd, "rev-parse", "--verify", "-q", "HEAD")[0] == 0
    numstat = _git(cwd, "diff", "--numstat", "HEAD")[1] if has_head else (
        _git(cwd, "diff", "--numstat")[1] + _git(cwd, "diff", "--numstat", "--cached")[1])
    for ln in numstat.splitlines():
        parts = ln.split("\t")
        if len(parts) >= 2 and parts[0].isdigit() and parts[1].isdigit():
            added += int(parts[0])
            removed += int(parts[1])
    root = Path(top.strip() or cwd)
    for rel in untracked[:UNTRACKED_MAX_FILES]:
        added += _count_lines(root / rel.strip('"'))
    remotes = [r for r in _git(cwd, "remote")[1].split() if r]
    return {
        **base,
        "repo": True,
        "root": str(root),
        "branch": info["branch"],
        "detached": detached,
        "upstream": info["upstream"],
        "ahead": info["ahead"],
        "behind": info["behind"],
        "upstream_gone": info["gone"],
        "files": len(files),
        "staged": staged,
        "untracked": len(untracked),
        "added": added,
        "removed": removed,
        "has_head": has_head,
        "remotes": remotes,
    }


def branches(cwd: str) -> dict[str, Any]:
    """Local branches (newest first) + remote ones not checked out yet."""
    code, out, err = _git(cwd, "for-each-ref", "--sort=-committerdate",
                          "--format=%(refname:short)\t%(HEAD)", "refs/heads")
    if code != 0:
        return {"ok": False, "error": _first_error(err) or "Not a git repository"}
    local, current = [], ""
    for ln in out.splitlines():
        name, _, head = ln.partition("\t")
        if not name:
            continue
        local.append(name)
        if head.strip() == "*":
            current = name
    remote = []
    _, rout, _ = _git(cwd, "for-each-ref", "--sort=-committerdate", "--format=%(refname:short)", "refs/remotes")
    for name in rout.splitlines():
        name = name.strip()
        if not name or name.endswith("/HEAD") or "/" not in name:
            continue
        short = name.split("/", 1)[1]
        if short not in local and len(remote) < 50:
            remote.append(name)
    return {"ok": True, "current": current, "local": local, "remote": remote}


# ─── Mutations ────────────────────────────────────────────────────────────

def _valid_branch(cwd: str, name: str) -> str:
    name = (name or "").strip()
    if not name or name.startswith("-"):
        raise GitError("Give the branch a name")
    code, out, _ = _git(cwd, "check-ref-format", "--branch", name)
    if code != 0:
        raise GitError(f"“{name}” isn't a valid branch name")
    return out.strip() or name


def checkout(cwd: str, branch: str, create: bool = False) -> dict[str, Any]:
    with _lock:
        try:
            name = _valid_branch(cwd, branch)
            if create:
                _ok(cwd, "switch", "-c", name)
            elif _git(cwd, "show-ref", "--verify", "-q", f"refs/heads/{name}")[0] == 0:
                _ok(cwd, "switch", name)
            elif "/" in name and _git(cwd, "show-ref", "--verify", "-q", f"refs/remotes/{name}")[0] == 0:
                _ok(cwd, "switch", "--track", name)  # origin/x → a local x that tracks it
            else:
                raise GitError(f"No branch called “{name}”")
        except GitError as exc:
            return {"ok": False, "error": str(exc)}
    return {"ok": True, "branch": status(cwd).get("branch", name)}


def stash(cwd: str, message: str = "") -> dict[str, Any]:
    msg = (message or "").strip() or f"Jarvis web · {time.strftime('%Y-%m-%d %H:%M')}"
    with _lock:
        try:
            out = _ok(cwd, "stash", "push", "--include-untracked", "-m", msg)
        except GitError as exc:
            return {"ok": False, "error": str(exc)}
    if "No local changes" in out:
        return {"ok": False, "error": "Nothing to stash"}
    return {"ok": True, "message": msg}


def _push_locked(cwd: str) -> dict[str, Any]:
    st = status(cwd)
    if not st.get("repo"):
        raise GitError("Not a git repository")
    if st.get("detached"):
        raise GitError("You're not on a branch — switch to one first")
    if st.get("upstream") and not st.get("upstream_gone"):
        _ok(cwd, "push", timeout=NET_TIMEOUT)
        return {"ok": True, "published": False}
    remotes = st.get("remotes") or []
    if not remotes:
        raise GitError("This repository has no remote to push to")
    remote = "origin" if "origin" in remotes else remotes[0]
    _ok(cwd, "push", "-u", remote, "HEAD", timeout=NET_TIMEOUT)
    return {"ok": True, "published": True, "remote": remote}


def push(cwd: str) -> dict[str, Any]:
    with _lock:
        try:
            return _push_locked(cwd)
        except GitError as exc:
            return {"ok": False, "error": str(exc)}


def commit(cwd: str, message: str, *, then_push: bool = False) -> dict[str, Any]:
    message = (message or "").strip()
    if not message:
        return {"ok": False, "error": "Write a commit message first (or Generate one)"}
    with _lock:
        try:
            _ok(cwd, "add", "--all")
            if _git(cwd, "diff", "--cached", "--quiet")[0] == 0:
                raise GitError("Nothing to commit")
            _ok(cwd, "commit", "-m", message)
            sha = _git(cwd, "rev-parse", "--short", "HEAD")[1].strip()
            result: dict[str, Any] = {"ok": True, "sha": sha}
            if then_push:
                try:
                    result.update(_push_locked(cwd), ok=True, pushed=True)
                except GitError as exc:  # committed, but the push failed
                    result.update(pushed=False, push_error=str(exc))
            return result
        except GitError as exc:
            return {"ok": False, "error": str(exc)}


_URL = re.compile(r"https?://\S+")


def create_pr(cwd: str, title: str = "", body: str = "", *, draft: bool = False) -> dict[str, Any]:
    if not shutil.which("gh"):
        return {"ok": False, "error": "Creating a PR needs the GitHub CLI — brew install gh, then gh auth login"}
    with _lock:
        try:
            st = status(cwd)
            if not st.get("upstream") or st.get("ahead") or st.get("upstream_gone"):
                _push_locked(cwd)  # gh needs the branch on the remote
            args = ["gh", "pr", "create"]
            if title.strip():
                args += ["--title", title.strip(), "--body", body.strip()]
            else:
                args += ["--fill"]
            if draft:
                args.append("--draft")
            code, out, err = _run(args, cwd, timeout=NET_TIMEOUT)
            text = f"{out}\n{err}"
            if code != 0:
                m = _URL.search(text)
                if m and "already exists" in text:
                    return {"ok": True, "url": m.group(0), "existing": True}
                raise GitError(_first_error(err or out) or "gh pr create failed")
            m = _URL.search(text)
            return {"ok": True, "url": m.group(0) if m else ""}
        except GitError as exc:
            return {"ok": False, "error": str(exc)}


# ─── Model-written commit / PR text ───────────────────────────────────────

_COMMIT_SYSTEM = """You write git commit messages for a developer.
- First line: a summary of at most 72 characters, imperative mood. Follow the style of the recent commit subjects you are shown (e.g. "feat(web): …" prefixes) when they have one.
- If the change needs it, a blank line and then a short body: 1–5 lines or "- " bullets on what changed and why.
- Describe only what the diff shows. No preamble, no quotes, no code fences, no sign-off lines."""

_PR_SYSTEM = """You write GitHub pull request descriptions for a developer.
- First line: the PR title, at most 72 characters, no "Title:" label.
- Then a blank line and a Markdown body: "## Summary" with 2–6 bullets on what changed and why, then "## Testing" with how it was or can be checked.
- Describe only what the diff and commits show. No preamble and no code fences around the whole answer."""


def _diff_for_model(cwd: str, *, against: str = "HEAD") -> str:
    parts = []
    has_head = _git(cwd, "rev-parse", "--verify", "-q", "HEAD")[0] == 0
    stat = _git(cwd, "diff", "--stat", against)[1] if has_head else _git(cwd, "diff", "--stat", "--cached")[1]
    if stat.strip():
        parts.append("Stat:\n" + stat.strip())
    untracked = _git(cwd, "ls-files", "--others", "--exclude-standard")[1].split("\n")
    untracked = [u for u in untracked if u.strip()]
    if untracked:
        parts.append("New files:\n" + "\n".join(untracked[:60]))
    diff = _git(cwd, "diff", against)[1] if has_head else _git(cwd, "diff", "--cached")[1]
    if diff.strip():
        parts.append("Diff:\n" + diff)
    for rel in untracked[:8]:  # a peek at new files' content
        try:
            head = Path(cwd, rel).read_text(errors="replace")[:1500]
        except OSError:
            continue
        parts.append(f"New file {rel}:\n{head}")
    text = "\n\n".join(parts)
    if len(text) > DIFF_MAX_CHARS:
        text = text[:DIFF_MAX_CHARS] + "\n… (diff cut here)"
    return text


def _default_base(cwd: str) -> str:
    code, out, _ = _git(cwd, "symbolic-ref", "--short", "-q", "refs/remotes/origin/HEAD")
    if code == 0 and out.strip():
        return out.strip()
    for cand in ("origin/main", "origin/master", "main", "master"):
        if _git(cwd, "rev-parse", "--verify", "-q", cand)[0] == 0:
            return cand
    return ""


def _ask_model(system: str, user: str) -> str:
    """One side request to the current model (not part of the conversation)."""
    from .. import state
    from ..constants import AUTH_OAUTH, OAUTH_IDENTITY, PROVIDER_OPENCODE, PROVIDER_OPENCODE_ZEN
    from ..prompt_enhance import _blocks, _short_error

    client = state.client
    if client is None:
        raise GitError("No model connected — run /provider")
    sys_prompt: Any = system
    if state.auth_mode == AUTH_OAUTH:
        sys_prompt = [{"type": "text", "text": OAUTH_IDENTITY}, {"type": "text", "text": system}]
    answer_tool = {
        "name": "write_text", "description": "Return the text you wrote. Call it once.",
        "input_schema": {"type": "object", "properties": {"text": {"type": "string"}}, "required": ["text"]},
    }
    kwargs: dict[str, Any] = dict(model=state.MODEL, max_tokens=4096, system=sys_prompt,
                                  messages=[{"role": "user", "content": user}])
    if state.provider in (PROVIDER_OPENCODE, PROVIDER_OPENCODE_ZEN):
        kwargs["thinking"] = {"type": "disabled"}
    if getattr(client, "gate_tools", None):  # gateways that force tools into every request
        kwargs["tools"] = [answer_tool]
    box: dict[str, Any] = {}

    def run() -> None:
        try:
            with client.messages.stream(**kwargs) as stream:
                blocks = _blocks(stream.get_final_message())
            for b in blocks:
                if b.get("type") == "tool_use" and b.get("name") == "write_text":
                    box["text"] = str((b.get("input") or {}).get("text") or "")
                    return
            box["text"] = "".join(b.get("text") or "" for b in blocks if b.get("type") == "text")
        except BaseException as exc:  # noqa: BLE001 — reported below
            box["error"] = exc

    worker = threading.Thread(target=run, name="git-message", daemon=True)
    worker.start()
    worker.join(MESSAGE_TIMEOUT)
    if worker.is_alive():
        raise GitError(f"The model took longer than {int(MESSAGE_TIMEOUT)}s — try again")
    if box.get("error") is not None:
        raise GitError(_short_error(box["error"]))
    return _clean_message(box.get("text", ""))


def _clean_message(text: str) -> str:
    text = (text or "").strip()
    text = re.sub(r"^```[a-z]*\n|\n```$", "", text).strip()
    lines = text.splitlines()
    if lines and re.match(r"^(here(?:'s| is)|sure|commit message|title)\b.*:\s*$", lines[0], re.I):
        lines = lines[1:]
    if lines:
        lines[0] = re.sub(r"^(?:title|subject)\s*:\s*", "", lines[0], flags=re.I).strip().strip('"`')
    return "\n".join(lines).strip()


def generate(cwd: str, kind: str = "commit") -> dict[str, Any]:
    try:
        if kind == "pr":
            base = _default_base(cwd)
            log = _git(cwd, "log", f"{base}..HEAD", "--format=- %s%n%b", "-30")[1] if base else ""
            committed = _git(cwd, "diff", f"{base}...HEAD")[1] if base else ""
            pending = _diff_for_model(cwd)
            diff = (committed + "\n" + pending)[:DIFF_MAX_CHARS]
            if not (log.strip() or diff.strip()):
                raise GitError("No changes on this branch yet")
            user = (f"Branch commits (vs {base or 'the default branch'}):\n{log.strip() or '(none yet)'}\n\n"
                    f"Changes:\n{diff}\n\nWrite the pull request title and description.")
            text = _ask_model(_PR_SYSTEM, user)
            title, _, body = text.partition("\n")
            return {"ok": True, "title": title.strip(), "body": body.strip()}
        diff = _diff_for_model(cwd)
        if not diff.strip():
            raise GitError("Nothing to commit")
        recent = _git(cwd, "log", "-8", "--format=%s")[1].strip()
        user = (f"Recent commit subjects in this repository:\n{recent or '(none yet)'}\n\n"
                f"Changes to commit:\n{diff}\n\nWrite the commit message.")
        return {"ok": True, "message": _ask_model(_COMMIT_SYSTEM, user)}
    except GitError as exc:
        return {"ok": False, "error": str(exc)}


# ─── Entry point for the HTTP handler ─────────────────────────────────────

MUTATING = {"checkout", "stash", "commit", "push", "pr"}


def post(op: str, data: dict[str, Any], *, busy: bool = False) -> dict[str, Any]:
    cwd = os.getcwd()
    if op in ("checkout", "stash") and busy:
        return {"ok": False, "code": "busy",
                "error": "Jarvis is working in these files. Wait for it to finish, or stop it first."}
    if op == "open":
        return open_in(cwd, str(data.get("editor") or ""))
    if op == "branches":
        return branches(cwd)
    if op == "checkout":
        return checkout(cwd, str(data.get("branch") or ""), create=bool(data.get("create")))
    if op == "stash":
        return stash(cwd, str(data.get("message") or ""))
    if op == "commit":
        return commit(cwd, str(data.get("message") or ""), then_push=bool(data.get("push")))
    if op == "push":
        return push(cwd)
    if op == "pr":
        return create_pr(cwd, str(data.get("title") or ""), str(data.get("body") or ""),
                         draft=bool(data.get("draft")))
    if op == "generate":
        return generate(cwd, "pr" if data.get("kind") == "pr" else "commit")
    return {"ok": False, "error": f"unknown git op: {op}"}
