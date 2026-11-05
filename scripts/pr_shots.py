#!/usr/bin/env python3
"""pr_shots — before/after screenshots of the Jarvis web UI for pull requests.

Boots two throwaway Jarvis web remotes — the PR's base commit ("before") and
your working tree ("after") — each with a fake HOME holding the same demo
chats and demo project, drives them in headless Chrome, and saves one PNG per
view and side. Nothing on screen, no Screen Recording permission, no window:
it works with the laptop locked while you prompt from your phone.

No real data reaches a screenshot: the fake HOME has no sessions, keys,
memory or MCP config of yours, the provider is a dummy Anthropic key that is
never called, and the project shown is a generated demo repo.

    python scripts/pr_shots.py doctor
    python scripts/pr_shots.py capture --spec shots.json        # → manifest.json + PNGs
    python scripts/pr_shots.py upload  --manifest RUN/manifest.json   # → pr-assets branch, prints Markdown

Spec (JSON file, inline JSON, or - for stdin)::

    {"base": "origin/main",                 # optional, default: merge-base with origin's default branch
     "views": [
       {"name": "files-drawer",             # file-safe id
        "title": "Files drawer with tabs",  # heading in the PR
        "viewport": "desktop",              # desktop | laptop | tablet | mobile | [w, h]
        "theme": "dark",                    # dark (default) | light
        "steps": [{"press": "Alt+KeyF"}, {"wait_for": "#files:not([hidden])"}],
        "selector": "#files",               # optional: crop to this element
        "before": true, "after": true}]}    # false = skip that side (e.g. new UI)

Steps: click (css), click_text (visible text), hover, type + into, press
("Escape", "Enter", "Alt+KeyF", "Control+k"), wait (ms), wait_for (css),
wait_for_text, eval (js), assert (js, must be truthy), scroll_to (css).

Upload never touches your checkout: images go into a separate orphan branch
(default ``pr-assets``) through git plumbing, then are linked by commit SHA.
"""
from __future__ import annotations

import argparse
import base64
import json
import os
import re
import shutil
import signal
import socket
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
RUNS_DIR = Path(tempfile.gettempdir()) / "jarvis-pr-shots"
KEEP_RUNS = 8
VIEWPORTS = {
    "desktop": (1440, 900, False),
    "laptop": (1280, 800, False),
    "tablet": (834, 1112, True),
    "mobile": (390, 844, True),
}
MOBILE_UA = ("Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit/605.1.15 "
             "(KHTML, like Gecko) Version/17.0 Mobile/15E148 Safari/604.1")
FAKE_KEY = "sk-ant-demo-pr-screenshots-not-a-real-key"
BOOT_TIMEOUT = 90.0
STEP_TIMEOUT = 10.0
SETTLE = 0.6
DEFAULT_SCALE = 2.0
# The page has drawn the chat: the app module ran and a session is showing.
DEFAULT_READY = ("document.readyState === 'complete' && !!document.querySelector('#chat') "
                 "&& (document.querySelector('#session-title')?.textContent || '').trim().length > 0")
# Jump every animation/transition to its end state (pausing could freeze a fade
# half-way) and hide the blinking caret, so before/after differ only by real changes.
FREEZE_CSS = ("*,*::before,*::after{transition-duration:0s!important;transition-delay:0s!important;"
              "animation-duration:0s!important;animation-delay:0s!important;caret-color:transparent!important}")


class ShotError(Exception):
    pass


def log(msg: str) -> None:
    print(f"[pr_shots] {msg}", file=sys.stderr, flush=True)


# ─── git ──────────────────────────────────────────────────────────────────


def git(*args: str, cwd: Path | None = None, env: dict | None = None, check: bool = True,
        timeout: float = 120) -> str:
    r = subprocess.run(["git", *args], cwd=cwd or ROOT, env=env, capture_output=True, text=True, timeout=timeout)
    if check and r.returncode != 0:
        raise ShotError(f"git {' '.join(args[:3])}: {(r.stderr or r.stdout).strip()[:400]}")
    return r.stdout.strip() if r.returncode == 0 else ""


def resolve_base(base: str) -> tuple[str, str]:
    """``(ref, sha)`` of the "before" side: merge-base of HEAD and ``base``."""
    if not base:
        base = git("symbolic-ref", "-q", "--short", "refs/remotes/origin/HEAD", check=False) or "origin/main"
    if base.startswith("origin/"):
        git("fetch", "--quiet", "origin", base.split("/", 1)[1], check=False, timeout=60)
    if not git("rev-parse", "--verify", "-q", base + "^{commit}", check=False):
        short = base.split("/", 1)[-1]
        if git("rev-parse", "--verify", "-q", short + "^{commit}", check=False):
            base = short
        else:
            raise ShotError(f"base {base!r} not found — pass --base <branch|sha>")
    sha = git("merge-base", "HEAD", base, check=False) or git("rev-parse", base)
    return base, sha


def extract_tree(sha: str, dest: Path) -> Path:
    """The ``jarvis`` package as of ``sha`` (git archive — no worktree, no index)."""
    dest.mkdir(parents=True, exist_ok=True)
    arch = subprocess.Popen(["git", "archive", sha, "jarvis"], cwd=ROOT, stdout=subprocess.PIPE)
    tar = subprocess.run(["tar", "-x", "-C", str(dest)], stdin=arch.stdout, capture_output=True)
    arch.stdout.close()
    if arch.wait() != 0 or tar.returncode != 0:
        raise ShotError(f"could not extract {sha[:10]}: {tar.stderr.decode(errors='replace')[:300]}")
    return dest


# ─── demo data (identical on both sides) ──────────────────────────────────

DEMO_FILES = {
    "README.md": "# Acme dashboard\n\nA small dashboard used to demo Jarvis.\n\n## Develop\n\n```bash\nnpm install\nnpm run dev\n```\n",
    "package.json": '{\n  "name": "acme-dashboard",\n  "version": "1.4.0",\n  "private": true,\n  "scripts": {\n    "dev": "vite",\n    "build": "vite build",\n    "test": "vitest"\n  }\n}\n',
    "src/app.js": "import { initShortcuts } from './shortcuts.js';\nimport { renderCharts } from './charts.js';\n\nexport function start() {\n  initShortcuts({ toggleTheme, openSearch });\n  renderCharts(document.querySelector('#charts'));\n}\n\nfunction toggleTheme() {\n  document.documentElement.classList.toggle('dark');\n}\n\nfunction openSearch() {\n  document.querySelector('#search').focus();\n}\n",
    "src/shortcuts.js": "export function initShortcuts(handlers) {\n  document.addEventListener('keydown', (e) => {\n    if (!e.altKey) return;\n    const map = {\n      KeyT: () => handlers.toggleTheme(),\n      KeyK: () => handlers.openSearch(),\n    };\n    map[e.code]?.();\n  });\n}\n",
    "src/charts.js": "export function renderCharts(root) {\n  if (!root) return;\n  root.innerHTML = '<canvas id=\"revenue\"></canvas>';\n}\n",
    "src/styles.css": ":root {\n  --bg: #fff;\n  --fg: #111;\n}\n\n.dark {\n  --bg: #0b0b0f;\n  --fg: #e8e8ea;\n}\n",
    "tests/shortcuts.test.js": "import { describe, it, expect } from 'vitest';\n\ndescribe('shortcuts', () => {\n  it('registers', () => expect(true).toBe(true));\n});\n",
}
DEMO_EDITS = {  # uncommitted work, so Git / Changes views have something to show
    "src/shortcuts.js": "export function initShortcuts(handlers) {\n  document.addEventListener('keydown', (e) => {\n    if (!e.altKey) return;\n    const map = {\n      KeyT: () => handlers.toggleTheme(),\n      KeyK: () => handlers.openSearch(),\n      KeyO: () => handlers.openSettings(),\n    };\n    map[e.code]?.();\n  });\n}\n",
    "src/settings.js": "export function openSettings() {\n  document.querySelector('#settings').showModal();\n}\n",
}


def _tool(i: str, name: str, inp: dict) -> dict:
    return {"type": "tool_use", "id": i, "name": name, "input": inp}


def _result(i: str, text: str) -> dict:
    return {"type": "tool_result", "tool_use_id": i, "content": text}


DEMO_CHATS = [
    {"title": "Fix flaky chart test", "messages": [
        {"role": "user", "content": "The chart test fails about once in ten runs — can you look?"},
        {"role": "assistant", "content": [{"type": "text", "text": "It awaits a canvas that is only created after the next frame. I made the test wait for it — 50/50 runs pass now."}]},
    ]},
    {"title": "Explain the build setup", "messages": [
        {"role": "user", "content": "How is this project built?"},
        {"role": "assistant", "content": [{"type": "text", "text": "Vite: `npm run dev` serves `src/` with hot reload, `npm run build` writes `dist/`."}]},
    ]},
    {"title": "Add a shortcut for settings", "messages": [
        {"role": "user", "content": "Add a keyboard shortcut that opens the settings dialog, next to the others."},
        {"role": "assistant", "content": [
            {"type": "text", "text": "I'll find where the shortcuts are registered."},
            _tool("toolu_demo_1", "search_code", {"pattern": "initShortcuts", "path": "src"}),
        ]},
        {"role": "user", "content": [_result("toolu_demo_1", "src/app.js:1: import { initShortcuts } from './shortcuts.js';\nsrc/shortcuts.js:1: export function initShortcuts(handlers) {")]},
        {"role": "assistant", "content": [
            _tool("toolu_demo_2", "edit_file", {"path": "src/shortcuts.js", "old_str": "      KeyK: () => handlers.openSearch(),\n",
                                                "new_str": "      KeyK: () => handlers.openSearch(),\n      KeyO: () => handlers.openSettings(),\n"}),
        ]},
        {"role": "user", "content": [_result("toolu_demo_2", "Edited src/shortcuts.js (+1 −0)")]},
        {"role": "assistant", "content": [{"type": "text", "text": (
            "Added **Alt + O** — it opens the settings dialog.\n\n"
            "| Shortcut | Action |\n|---|---|\n| Alt T | Toggle theme |\n| Alt K | Search |\n| Alt O | Settings |\n\n"
            "```js\nKeyO: () => handlers.openSettings(),\n```\n\n"
            "`openSettings` lives in the new `src/settings.js`.")}]},
    ]},
]

SEED = r"""
import json, sys
tree, cwd, data = sys.argv[1], sys.argv[2], sys.argv[3]
sys.path.insert(0, tree)
from jarvis import state
from jarvis.storage import sessions as S
S.db_init()
last = None
for chat in json.loads(open(data).read()):
    try:
        sid = S.db_create_session(state.MODEL, cwd)
    except TypeError:
        sid = S.db_create_session(state.MODEL)
    for i, m in enumerate(chat["messages"]):
        S.db_append_message(sid, i, m)
    S.db_rename_session(sid, chat["title"])
    last = sid
print(last)
"""

DEMO_SIBLINGS = {  # other projects a demo master group gathers (branch shows in the sidebar row)
    "acme-api": ("feat/orders", {
        "README.md": "# Acme API\n\nOrders and accounts service for the demo dashboard.\n",
        "src/orders.js": "export const listOrders = () => [{ id: 1, total: 42 }];\n",
    }),
    "acme-web": ("development", {
        "README.md": "# Acme web\n\nThe storefront that the dashboard talks to.\n",
        "src/client.js": "export const apiBase = 'https://api.acme.test';\n",
    }),
}

SEED_WORKSPACE = r"""
import os, sys

tree, home, current = sys.argv[1], sys.argv[2], sys.argv[3]
sys.path.insert(0, tree)
try:
    from jarvis.storage import workspace
except Exception:  # a tree from before the workspace existed: nothing to seed (and that is the point)
    sys.exit(0)
try:
    ids = []
    for path in [current] + [os.path.join(home, "projects", n) for n in sorted(os.listdir(os.path.join(home, "projects")))
                             if n != "acme-dashboard"]:
        if workspace.find_by_path(path) is None:
            ids.append(workspace.add(path, name=os.path.basename(path))["id"])
    if ids:
        workspace.create_group("Acme", ids)
except Exception as exc:  # a demo, not a test: the shots still work without it
    print("pr_shots: workspace seed skipped:", exc, file=sys.stderr)
"""

BOOT = r"""
import os, runpy, sys
tree, port = sys.argv[1], sys.argv[2]
sys.path.insert(0, tree)
import jarvis
here = os.path.realpath(os.path.dirname(jarvis.__file__))
if not here.startswith(os.path.realpath(tree) + os.sep):
    sys.exit("pr_shots: imported jarvis from %s, expected under %s" % (here, tree))
print("pr_shots: jarvis from " + here, flush=True)
sys.argv = ["jarvis", "--headless", "--web-port", port]
runpy.run_module("jarvis", run_name="__main__", alter_sys=True)
"""


def find_python() -> str:
    env = os.environ.get("JARVIS_PR_SHOTS_PYTHON", "").strip()
    cands = [env] if env else []
    cands += [str(ROOT / ".venv" / "bin" / "python"), sys.executable]
    jar = shutil.which("jarvis")
    if jar:
        try:
            first = Path(jar).read_text(errors="replace").splitlines()[0]
            if first.startswith("#!"):
                cands.append(first[2:].strip().split()[0])
        except (OSError, IndexError):
            pass
    for py in cands:
        if py and os.path.exists(py):
            r = subprocess.run([py, "-c", "import textual, anthropic, httpx"], capture_output=True)
            if r.returncode == 0:
                return py
    raise ShotError("no Python with Jarvis's dependencies found — set JARVIS_PR_SHOTS_PYTHON")


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def clean_env(home: Path) -> dict[str, str]:
    env = {k: os.environ[k] for k in ("PATH", "LANG", "LC_ALL", "TMPDIR", "SHELL") if k in os.environ}
    env.update({
        "USER": "demo", "LOGNAME": "demo", "HOSTNAME": "demo",
        "HOME": str(home), "TERM": "xterm-256color", "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_TERMINAL_PROMPT": "0", "GH_PROMPT_DISABLED": "1", "GH_TOKEN": "", "GITHUB_TOKEN": "",
        "ANTHROPIC_API_KEY": FAKE_KEY, "HARNESS_PROVIDER": "anthropic",
        "HARNESS_MODELS_DEV": "0", "HARNESS_LOCAL_MODELS": "0", "HARNESS_OPENROUTER_USAGE": "0",
        "PYTHONDONTWRITEBYTECODE": "1", "JARVIS_PR_SHOTS": "1",
    })
    return env


class Instance:
    """One throwaway Jarvis web remote serving ``tree``."""

    def __init__(self, label: str, tree: Path, run_dir: Path, python: str):
        self.label, self.tree, self.python = label, tree, python
        self.dir = run_dir / label
        self.home = self.dir / "home"
        self.project = self.home / "projects" / "acme-dashboard"
        self.log_path = self.dir / "jarvis.log"
        self.proc: subprocess.Popen | None = None
        self.url = ""

    def prepare(self) -> None:
        self.home.mkdir(parents=True, exist_ok=True)
        # Terminals opened in a shot start the user's shell with this HOME: a neutral
        # prompt instead of the default user@host (the screenshots go to a public PR).
        for rc, line in ((".zshrc", "PROMPT='demo@acme %1~ %# '\nunsetopt BEEP\n"),
                         (".bashrc", "PS1='demo@acme \\W \\$ '\n"),
                         (".bash_profile", "export BASH_SILENCE_DEPRECATION_WARNING=1\n. ~/.bashrc\n")):
            (self.home / rc).write_text(line)
        env = clean_env(self.home)
        p = self.project
        for rel, text in DEMO_FILES.items():
            (p / rel).parent.mkdir(parents=True, exist_ok=True)
            (p / rel).write_text(text)
        ident = ["-c", "user.name=Demo", "-c", "user.email=demo@example.com"]
        git("init", "-q", "-b", "main", cwd=p, env=env)
        git("add", "-A", cwd=p, env=env)
        git(*ident, "commit", "-q", "-m", "Initial dashboard", cwd=p, env={
            **env, "GIT_AUTHOR_DATE": "2026-01-05T10:00:00", "GIT_COMMITTER_DATE": "2026-01-05T10:00:00"})
        # A GitHub-looking origin (never contacted: refs are set locally) so the
        # Source Control / pull request UI has a remote and an upstream to show.
        git("remote", "add", "origin", "https://github.com/acme/acme-dashboard.git", cwd=p, env=env)
        git("update-ref", "refs/remotes/origin/main", "HEAD", cwd=p, env=env)
        git("symbolic-ref", "refs/remotes/origin/HEAD", "refs/remotes/origin/main", cwd=p, env=env)
        git("config", "branch.main.remote", "origin", cwd=p, env=env)
        git("config", "branch.main.merge", "refs/heads/main", cwd=p, env=env)
        for rel, text in DEMO_EDITS.items():
            (p / rel).write_text(text)
        # Siblings of the demo project, so a master group has members to show.
        for name, (branch, files) in DEMO_SIBLINGS.items():
            sib = self.home / "projects" / name
            for rel, text in files.items():
                (sib / rel).parent.mkdir(parents=True, exist_ok=True)
                (sib / rel).write_text(text)
            git("init", "-q", "-b", "main", cwd=sib, env=env)
            git("add", "-A", cwd=sib, env=env)
            git(*ident, "commit", "-q", "-m", f"Initial {name}", cwd=sib, env={
                **env, "GIT_AUTHOR_DATE": "2026-01-06T10:00:00", "GIT_COMMITTER_DATE": "2026-01-06T10:00:00"})
            git("checkout", "-q", "-b", branch, cwd=sib, env=env)
        data = self.dir / "chats.json"
        data.write_text(json.dumps(DEMO_CHATS))
        r = subprocess.run([self.python, "-c", SEED, str(self.tree), str(p), str(data)], cwd=p, env=env,
                           capture_output=True, text=True, timeout=120)
        if r.returncode != 0:
            raise ShotError(f"{self.label}: seeding demo chats failed: {r.stderr.strip()[-600:]}")
        self.resume = r.stdout.strip().splitlines()[-1] if r.stdout.strip() else ""
        # A workspace with one master group over the demo projects (skipped on trees without it).
        w = subprocess.run([self.python, "-c", SEED_WORKSPACE, str(self.tree), str(self.home), str(p)],
                           cwd=p, env=env, capture_output=True, text=True, timeout=120)
        if w.returncode != 0:
            raise ShotError(f"{self.label}: seeding the demo workspace failed: {w.stderr.strip()[-600:]}")

    def start(self) -> None:
        env = clean_env(self.home)
        if self.resume:
            env["HARNESS_RESUME_SESSION"] = self.resume
        logf = open(self.log_path, "w")
        self.proc = subprocess.Popen([self.python, "-c", BOOT, str(self.tree), str(free_port())],
                                     cwd=self.project, env=env, stdin=subprocess.DEVNULL, stdout=logf,
                                     stderr=subprocess.STDOUT, start_new_session=True)
        logf.close()

    def wait_ready(self) -> str:
        inst = self.home / ".config" / "harness-agent" / "instances"
        deadline = time.monotonic() + BOOT_TIMEOUT
        while time.monotonic() < deadline:
            if self.proc and self.proc.poll() is not None:
                raise ShotError(f"{self.label}: Jarvis exited ({self.proc.returncode}):\n{self.log_tail()}")
            for f in inst.glob("*.json") if inst.is_dir() else []:
                try:
                    rec = json.loads(f.read_text())
                except (OSError, ValueError):
                    continue
                if rec.get("port") and rec.get("token") and ("session_id" not in rec or rec.get("session_id") is not None):
                    self.url = f"http://127.0.0.1:{rec['port']}/?token={rec['token']}"
                    return self.url
            time.sleep(0.25)
        raise ShotError(f"{self.label}: Jarvis didn't start in {BOOT_TIMEOUT:.0f}s:\n{self.log_tail()}")

    def log_tail(self, n: int = 25) -> str:
        try:
            lines = [ln for ln in self.log_path.read_text(errors="replace").splitlines()
                     if ln.strip() and "\x1b" not in ln]
        except OSError:
            return ""
        return "\n".join(lines[-n:])

    def stop(self) -> None:
        if not self.proc or self.proc.poll() is not None:
            return
        for sig, wait in ((signal.SIGTERM, 6.0), (signal.SIGKILL, 3.0)):
            try:
                os.killpg(self.proc.pid, sig)
            except (OSError, ProcessLookupError):
                return
            try:
                self.proc.wait(timeout=wait)
                return
            except subprocess.TimeoutExpired:
                continue


# ─── headless Chrome over --remote-debugging-pipe (stdlib only) ───────────


def find_chrome() -> str:
    env = os.environ.get("HARNESS_CHROME", "").strip()
    if env and os.path.exists(env):
        return env
    # chrome-headless-shell first (same order as jarvis/tools/screenshot.py): it is a
    # plain binary, so unlike full Chrome it never leaves an icon in the Dock.
    if shutil.which("chrome-headless-shell"):
        return shutil.which("chrome-headless-shell")  # type: ignore[return-value]
    home = Path.home()
    roots = [home / "Library" / "Caches" / "ms-playwright", home / ".cache" / "ms-playwright"]
    pw = os.environ.get("PLAYWRIGHT_BROWSERS_PATH", "").strip()
    if pw and pw != "0":
        roots.insert(0, Path(pw).expanduser())
    searches = [(r, pat) for r in roots for pat in ("chromium_headless_shell-*/*/chrome-headless-shell",
                                                    "chromium_headless_shell-*/*/headless_shell")]
    searches.append((home / ".cache" / "puppeteer", "chrome-headless-shell/*/*/chrome-headless-shell"))
    for root, pat in searches:
        hits = [p for p in root.glob(pat) if p.is_file() and os.access(p, os.X_OK)]
        if hits:
            return str(max(hits, key=lambda p: tuple(int(n) for n in re.findall(r"\d+", str(p)))))
    for p in ("/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
              "/Applications/Chromium.app/Contents/MacOS/Chromium"):
        if os.path.exists(p):
            return p
    for name in ("chrome-headless-shell", "google-chrome", "chromium", "chromium-browser"):
        if shutil.which(name):
            return shutil.which(name)  # type: ignore[return-value]
    raise ShotError("no Chrome found — install Chrome or `npx playwright install chromium-headless-shell`")


class Browser:
    """Chrome DevTools Protocol over fds 3/4: JSON messages ended by NUL."""

    def __init__(self, chrome: str, profile: Path):
        profile.mkdir(parents=True, exist_ok=True)
        to_r, to_w = os.pipe()      # us → chrome (its fd 3)
        from_r, from_w = os.pipe()  # chrome → us (its fd 4)

        def child() -> None:
            os.dup2(to_r, 200)
            os.dup2(from_w, 201)
            os.dup2(200, 3)
            os.dup2(201, 4)
            os.setsid()

        args = [chrome, "--headless", "--remote-debugging-pipe", f"--user-data-dir={profile}",
                "--no-first-run", "--no-default-browser-check", "--disable-gpu", "--hide-scrollbars",
                "--use-mock-keychain", "--disable-extensions", "--mute-audio",
                "--force-color-profile=srgb", "--disable-background-networking", "about:blank"]
        self.proc = subprocess.Popen(args, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                                     stderr=subprocess.DEVNULL, preexec_fn=child, close_fds=False)
        os.close(to_r)
        os.close(from_w)
        self._w = os.fdopen(to_w, "wb", buffering=0)
        self._r = os.fdopen(from_r, "rb", buffering=0)
        self._id = 0
        self._cv = threading.Condition()
        self._replies: dict[int, dict] = {}
        self._events: list[dict] = []
        self._dead = False
        threading.Thread(target=self._read, daemon=True).start()
        self.call("Browser.getVersion", timeout=20)

    def _read(self) -> None:
        buf = b""
        while True:
            try:
                chunk = self._r.read(65536)
            except OSError:
                chunk = b""
            if not chunk:
                with self._cv:
                    self._dead = True
                    self._cv.notify_all()
                return
            buf += chunk
            while b"\0" in buf:
                raw, buf = buf.split(b"\0", 1)
                try:
                    msg = json.loads(raw)
                except ValueError:
                    continue
                with self._cv:
                    if "id" in msg:
                        self._replies[msg["id"]] = msg
                    else:
                        self._events.append(msg)
                        del self._events[:-500]
                    self._cv.notify_all()

    def call(self, method: str, params: dict | None = None, session: str | None = None,
             timeout: float = 30.0) -> dict:
        with self._cv:
            self._id += 1
            mid = self._id
        msg: dict[str, Any] = {"id": mid, "method": method, "params": params or {}}
        if session:
            msg["sessionId"] = session
        self._w.write(json.dumps(msg).encode() + b"\0")
        deadline = time.monotonic() + timeout
        with self._cv:
            while mid not in self._replies:
                left = deadline - time.monotonic()
                if left <= 0 or self._dead:
                    raise ShotError(f"Chrome: {method} {'died' if self._dead else 'timed out'}")
                self._cv.wait(left)
            reply = self._replies.pop(mid)
        if "error" in reply:
            raise ShotError(f"Chrome: {method}: {reply['error'].get('message')}")
        return reply.get("result", {})

    def wait_event(self, method: str, session: str, timeout: float) -> dict | None:
        deadline = time.monotonic() + timeout
        with self._cv:
            while True:
                for i, ev in enumerate(self._events):
                    if ev.get("method") == method and ev.get("sessionId") == session:
                        return self._events.pop(i)
                left = deadline - time.monotonic()
                if left <= 0 or self._dead:
                    return None
                self._cv.wait(left)

    def close(self) -> None:
        try:
            self.call("Browser.close", timeout=3)
        except Exception:
            pass
        try:
            self.proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            try:
                os.killpg(self.proc.pid, signal.SIGKILL)
            except (OSError, ProcessLookupError):
                self.proc.kill()


_KEYS = {"Enter": (13, "\r"), "Escape": (27, ""), "Tab": (9, ""), "Backspace": (8, ""),
         "Delete": (46, ""), "Space": (32, " "), "ArrowUp": (38, ""), "ArrowDown": (40, ""),
         "ArrowLeft": (37, ""), "ArrowRight": (39, ""), "Home": (36, ""), "End": (35, ""),
         "PageUp": (33, ""), "PageDown": (34, ""), "Backquote": (192, "`"), "Slash": (191, "/"),
         "Comma": (188, ","), "Period": (190, ".")}
_MODS = {"alt": 1, "option": 1, "control": 2, "ctrl": 2, "meta": 4, "cmd": 4, "command": 4, "shift": 8}

_FIND_TEXT = r"""(text) => {
  const want = text.trim().toLowerCase();
  const sel = 'button, a, [role=button], [role=tab], [role=menuitem], [role=option], summary, label, li, .chip-btn, .tool-chip';
  const seen = (el) => { const r = el.getBoundingClientRect(); const s = getComputedStyle(el);
    return r.width > 0 && r.height > 0 && s.visibility !== 'hidden' && s.display !== 'none'; };
  const label = (el) => (el.getAttribute('aria-label') || el.textContent || el.title || '').trim().toLowerCase().replace(/\s+/g, ' ');
  const all = [...document.querySelectorAll(sel)].filter(seen);
  const el = all.find((e) => label(e) === want) || all.find((e) => label(e).includes(want));
  if (!el) return false;
  document.querySelectorAll('[data-prshot]').forEach((e) => e.removeAttribute('data-prshot'));
  el.setAttribute('data-prshot', '1');
  return true;
}"""


class Page:
    def __init__(self, browser: Browser, width: int, height: int, scale: float, mobile: bool):
        self.b = browser
        # A fresh context per view: no localStorage / cookies carried over from the
        # previous view (an earlier view closing a panel must not change the next).
        self.ctx = browser.call("Target.createBrowserContext", {"disposeOnDetach": True})["browserContextId"]
        self.target = browser.call("Target.createTarget", {"url": "about:blank",
                                                          "browserContextId": self.ctx})["targetId"]
        self.s = browser.call("Target.attachToTarget", {"targetId": self.target, "flatten": True})["sessionId"]
        self.call("Page.enable")
        self.call("Runtime.enable")
        self.call("Emulation.setDeviceMetricsOverride",
                  {"width": width, "height": height, "deviceScaleFactor": scale, "mobile": mobile})
        if mobile:
            self.call("Emulation.setTouchEmulationEnabled", {"enabled": True, "maxTouchPoints": 5})
            self.call("Emulation.setUserAgentOverride", {"userAgent": MOBILE_UA})

    def call(self, method: str, params: dict | None = None, timeout: float = 30.0) -> dict:
        return self.b.call(method, params, self.s, timeout)

    def js(self, expr: str, timeout: float = 15.0) -> Any:
        r = self.call("Runtime.evaluate", {"expression": expr, "awaitPromise": True,
                                           "returnByValue": True}, timeout=timeout)
        if r.get("exceptionDetails"):
            d = r["exceptionDetails"]
            raise ShotError((d.get("exception") or {}).get("description") or d.get("text") or "JS error")
        return (r.get("result") or {}).get("value")

    def goto(self, url: str) -> None:
        self.call("Page.navigate", {"url": url})
        if self.b.wait_event("Page.loadEventFired", self.s, 45) is None:
            raise ShotError("page didn't finish loading in 45s")

    def wait_js(self, expr: str, timeout: float = STEP_TIMEOUT, what: str = "") -> None:
        deadline = time.monotonic() + timeout
        last = ""
        while time.monotonic() < deadline:
            try:
                if self.js(f"!!({expr})"):
                    return
            except ShotError as e:
                last = str(e)
            time.sleep(0.12)
        raise ShotError(f"timed out waiting for {what or expr}" + (f" ({last})" if last else ""))

    def ready(self, expr: str) -> None:
        self.wait_js(expr, timeout=30, what="the app to load")
        self.js("document.fonts ? document.fonts.ready.then(() => true) : true")
        time.sleep(SETTLE)

    # steps
    def _center(self, sel: str) -> dict | None:
        return self.js(f"""(() => {{ const el = document.querySelector({json.dumps(sel)}); if (!el) return null;
            el.scrollIntoView({{block: 'nearest', inline: 'nearest'}}); const r = el.getBoundingClientRect();
            return {{x: r.x + r.width / 2, y: r.y + r.height / 2, w: r.width, h: r.height}}; }})()""")

    def _mouse(self, kind: str, x: float, y: float) -> None:
        p = {"type": kind, "x": x, "y": y, "button": "left" if kind != "mouseMoved" else "none"}
        if kind != "mouseMoved":
            p["clickCount"] = 1
        self.call("Input.dispatchMouseEvent", p)

    def click(self, sel: str) -> None:
        deadline = time.monotonic() + STEP_TIMEOUT
        box = self._center(sel)
        while box is None and time.monotonic() < deadline:
            time.sleep(0.15)
            box = self._center(sel)
        if box is None:
            raise ShotError(f"no element matches {sel}")
        if not box["w"] or not box["h"]:
            self.js(f"document.querySelector({json.dumps(sel)}).click()")
            return
        for kind in ("mouseMoved", "mousePressed", "mouseReleased"):
            self._mouse(kind, box["x"], box["y"])

    def hover(self, sel: str) -> None:
        box = self._center(sel)
        if box is None:
            raise ShotError(f"no element matches {sel}")
        self._mouse("mouseMoved", box["x"], box["y"])

    def click_text(self, text: str) -> None:
        deadline = time.monotonic() + STEP_TIMEOUT
        while not self.js(f"({_FIND_TEXT})({json.dumps(text)})"):
            if time.monotonic() > deadline:
                raise ShotError(f"no visible button/link/tab with text {text!r}")
            time.sleep(0.15)
        self.click("[data-prshot]")

    def press(self, combo: str) -> None:
        *mods, key = combo.split("+") if combo != "+" else ["+"]
        m = 0
        for mod in mods:
            if mod.lower() not in _MODS:
                raise ShotError(f"unknown modifier {mod!r} in {combo!r}")
            m |= _MODS[mod.lower()]
        if re.fullmatch(r"Key[A-Z]", key):
            code, ch, vk = key, key[3].lower(), ord(key[3])
        elif re.fullmatch(r"Digit\d", key):
            code, ch, vk = key, key[5], ord(key[5])
        elif len(key) == 1:
            ch = key
            code = f"Key{key.upper()}" if key.isalpha() else (f"Digit{key}" if key.isdigit() else "")
            vk = ord(key.upper()) if key.isalnum() else 0
        elif key in _KEYS:
            vk, ch = _KEYS[key]
            code = key
        elif re.fullmatch(r"F\d{1,2}", key):
            code, ch, vk = key, "", 111 + int(key[1:])
        else:
            raise ShotError(f"unknown key {key!r}")
        text = ch if ch and not m & 7 else ""
        if text and m & 8:
            text = text.upper()
        name = key if len(key) > 1 and not key.startswith(("Key", "Digit")) else (text or ch)
        base = {"key": name, "code": code, "windowsVirtualKeyCode": vk, "nativeVirtualKeyCode": vk, "modifiers": m}
        self.call("Input.dispatchKeyEvent", {**base, "type": "keyDown" if text else "rawKeyDown",
                                             "text": text, "unmodifiedText": text})
        self.call("Input.dispatchKeyEvent", {**base, "type": "keyUp"})

    def step(self, st: dict) -> None:
        if "click" in st:
            self.click(st["click"])
        elif "click_text" in st:
            self.click_text(st["click_text"])
        elif "hover" in st:
            self.hover(st["hover"])
        elif "type" in st:
            if st.get("into"):
                self.click(st["into"])
            self.call("Input.insertText", {"text": str(st["type"])})
        elif "press" in st:
            self.press(str(st["press"]))
        elif "wait" in st:
            time.sleep(max(0, min(float(st["wait"]), 15000)) / 1000)
        elif "wait_for" in st:
            self.wait_js(f"""(() => {{ const el = document.querySelector({json.dumps(st['wait_for'])});
                if (!el) return false; const r = el.getBoundingClientRect(); return r.width > 0 && r.height > 0; }})()""",
                         float(st.get("timeout", STEP_TIMEOUT * 1000)) / 1000, st["wait_for"])
        elif "wait_for_text" in st:
            self.wait_js(f"document.body.innerText.includes({json.dumps(st['wait_for_text'])})",
                         float(st.get("timeout", STEP_TIMEOUT * 1000)) / 1000, repr(st["wait_for_text"]))
        elif "eval" in st:
            self.js(st["eval"])
        elif "assert" in st:
            if not self.js(f"!!({st['assert']})"):
                raise ShotError(f"assertion is false: {st['assert']}")
        elif "scroll_to" in st:
            if not self.js(f"""(() => {{ const el = document.querySelector({json.dumps(st['scroll_to'])});
                if (!el) return false; el.scrollIntoView({{block: 'center'}}); return true; }})()"""):
                raise ShotError(f"no element matches {st['scroll_to']}")
        else:
            raise ShotError(f"unknown step {st}")
        time.sleep(float(st.get("after", 250)) / 1000)

    def set_theme(self, want: str) -> None:
        cur = self.js("document.documentElement.dataset.theme || 'dark'")
        light = cur == "light"
        if (want == "light") != light:
            self.press("Alt+KeyT")
            self.wait_js(f"(document.documentElement.dataset.theme === 'light') === {str(want == 'light').lower()}",
                         5, f"{want} theme")
            time.sleep(0.3)

    def screenshot(self, out: Path, selector: str = "", full_page: bool = False, pad: int = 8) -> None:
        self.js(f"(() => {{ const s = document.createElement('style'); s.id = 'prshot-freeze';"
                f" s.textContent = {json.dumps(FREEZE_CSS)}; document.head.appendChild(s);"
                f" document.activeElement?.blur?.(); return new Promise(r => requestAnimationFrame(() => requestAnimationFrame(r))); }})()")
        params: dict[str, Any] = {"format": "png", "captureBeyondViewport": bool(full_page)}
        if selector:
            box = self.js(f"""(() => {{ const el = document.querySelector({json.dumps(selector)}); if (!el) return null;
                const r = el.getBoundingClientRect(); return {{x: r.x + scrollX, y: r.y + scrollY, w: r.width, h: r.height}}; }})()""")
            if not box or not box["w"]:
                raise ShotError(f"crop selector {selector} not found or not visible")
            x, y = max(0, box["x"] - pad), max(0, box["y"] - pad)
            params["clip"] = {"x": x, "y": y, "width": box["w"] + 2 * pad, "height": box["h"] + 2 * pad, "scale": 1}
        elif full_page:
            m = self.call("Page.getLayoutMetrics")
            size = m.get("cssContentSize") or m.get("contentSize")
            params["clip"] = {"x": 0, "y": 0, "width": size["width"], "height": size["height"], "scale": 1}
        data = self.call("Page.captureScreenshot", params, timeout=60)["data"]
        out.write_bytes(base64.b64decode(data))

    def close(self) -> None:
        try:
            self.b.call("Target.closeTarget", {"targetId": self.target}, timeout=5)
        except Exception:
            pass
        try:
            self.b.call("Target.disposeBrowserContext", {"browserContextId": self.ctx}, timeout=5)
        except Exception:
            pass


# ─── capture ──────────────────────────────────────────────────────────────


def load_spec(src: str) -> dict:
    if src == "-":
        text = sys.stdin.read()
    elif src.lstrip().startswith(("{", "[")):
        text = src
    else:
        text = Path(src).expanduser().read_text()
    spec = json.loads(text)
    if isinstance(spec, list):
        spec = {"views": spec}
    views = spec.get("views") or []
    if not views:
        raise ShotError("spec has no views")
    seen = set()
    for i, v in enumerate(views):
        name = re.sub(r"[^a-z0-9-]+", "-", str(v.get("name") or f"view-{i + 1}").lower()).strip("-") or f"view-{i + 1}"
        while name in seen:
            name += "-2"
        seen.add(name)
        v["name"] = name
        v.setdefault("title", name.replace("-", " ").capitalize())
        if not isinstance(v.get("steps", []), list):
            raise ShotError(f"view {name}: steps must be a list")
    return spec


def viewport(v: dict) -> tuple[int, int, bool]:
    vp = v.get("viewport", "desktop")
    if isinstance(vp, str):
        if vp not in VIEWPORTS:
            raise ShotError(f"unknown viewport {vp!r} ({', '.join(VIEWPORTS)} or [w, h])")
        return VIEWPORTS[vp]
    w, h = int(vp[0]), int(vp[1])
    return w, h, bool(v.get("mobile", w < 600))


def shoot(browser: Browser, inst: Instance, v: dict, out: Path, scale: float, ready: str) -> dict:
    w, h, mobile = viewport(v)
    page = Page(browser, w, h, float(v.get("scale", scale)), mobile)
    errors: list[str] = []
    try:
        page.goto(inst.url)
        page.ready(v.get("ready") or ready)
        page.set_theme(str(v.get("theme", "dark")))
        for i, st in enumerate(v.get("steps") or []):
            try:
                page.step(st)
            except ShotError as e:
                errors.append(f"step {i + 1} {json.dumps(st)}: {e}")
                if v.get("stop_on_error", True):
                    break
        time.sleep(float(v.get("settle", SETTLE * 1000)) / 1000)
        page.screenshot(out, v.get("selector", ""), bool(v.get("full_page")))
    finally:
        page.close()
    return {"path": str(out), "errors": errors}


def prune_runs() -> None:
    try:
        runs = sorted((p for p in RUNS_DIR.iterdir() if p.is_dir()), key=lambda p: p.stat().st_mtime, reverse=True)
    except OSError:
        return
    for old in runs[KEEP_RUNS:]:
        shutil.rmtree(old, ignore_errors=True)


def cmd_capture(a: argparse.Namespace) -> int:
    spec = load_spec(a.spec)
    views = spec["views"]
    if a.views:
        keep = {n.strip() for n in a.views.split(",")}
        views = [v for v in views if v["name"] in keep]
    sides = ["before", "after"] if a.only == "both" else [a.only]
    sides = [s for s in sides if any(v.get(s, True) is not False for v in views)]
    RUNS_DIR.mkdir(parents=True, exist_ok=True)
    prune_runs()
    run_dir = Path(a.out).expanduser().resolve() if a.out else RUNS_DIR / time.strftime("%Y%m%d-%H%M%S")
    run_dir.mkdir(parents=True, exist_ok=True)
    shots = run_dir / "shots"
    shots.mkdir(exist_ok=True)
    python = find_python()
    chrome = find_chrome()
    base_ref, base_sha = ("", "")
    trees: dict[str, Path] = {"after": ROOT}
    if "before" in sides:
        base_ref, base_sha = resolve_base(a.base or spec.get("base", ""))
        log(f"before = {base_ref} @ {base_sha[:10]}")
        trees["before"] = extract_tree(base_sha, run_dir / "src-before")
    instances = {s: Instance(s, trees[s], run_dir, python) for s in sides}
    browser: Browser | None = None
    manifest: dict[str, Any] = {
        "run_dir": str(run_dir), "repo": str(ROOT),
        "head": git("rev-parse", "--abbrev-ref", "HEAD", check=False),
        "head_sha": git("rev-parse", "HEAD", check=False),
        "dirty": bool(git("status", "--porcelain", check=False)),
        "base": base_ref, "base_sha": base_sha, "views": [],
    }
    try:
        for inst in instances.values():
            inst.prepare()
            inst.start()
        for inst in instances.values():
            log(f"{inst.label}: {inst.wait_ready().split('?')[0]}")
        browser = Browser(chrome, run_dir / "chrome-profile")
        ready = spec.get("ready") or DEFAULT_READY
        scale = float(spec.get("scale", a.scale))
        for v in views:
            row: dict[str, Any] = {"name": v["name"], "title": v["title"], "note": v.get("note", ""),
                                   "viewport": list(viewport(v)[:2])}
            for side in ("before", "after"):
                if side not in instances or v.get(side, True) is False:
                    row[side] = None
                    continue
                out = shots / f"{v['name']}-{side}.png"
                try:
                    row[side] = shoot(browser, instances[side], v, out, scale, ready)
                except ShotError as e:
                    row[side] = {"path": "", "errors": [str(e)]}
                res = row[side]
                status = "ok" if res["path"] and not res["errors"] else ("FAILED" if not res["path"] else "with errors")
                log(f"{v['name']} [{side}] {status}" + "".join(f"\n    - {e}" for e in res["errors"]))
            manifest["views"].append(row)
    finally:
        if browser:
            browser.close()
        for inst in instances.values():
            inst.stop()
        if not a.keep:
            shutil.rmtree(run_dir / "src-before", ignore_errors=True)
            shutil.rmtree(run_dir / "chrome-profile", ignore_errors=True)
            for inst in instances.values():
                shutil.rmtree(inst.home, ignore_errors=True)
    mpath = run_dir / "manifest.json"
    mpath.write_text(json.dumps(manifest, indent=2))
    (run_dir / "screenshots.md").write_text(markdown(manifest, local=True))
    print(json.dumps({"manifest": str(mpath), "views": [
        {"name": r["name"], **{s: (r[s] or {}).get("path") or None for s in ("before", "after")},
         "errors": [e for s in ("before", "after") for e in ((r[s] or {}).get("errors") or [])]}
        for r in manifest["views"]]}, indent=2))
    failed = any(not (r[s] or {"path": "x"}).get("path") for r in manifest["views"] for s in ("before", "after"))
    return 1 if failed else 0


# ─── upload + markdown ────────────────────────────────────────────────────


def markdown(m: dict, urls: dict[str, str] | None = None, local: bool = False) -> str:
    def cell(side: str, row: dict) -> str:
        res = row.get(side)
        if not res or not res.get("path"):
            return "_not in base_" if side == "before" else "_removed_"
        src = (urls or {}).get(res["path"]) or (Path(res["path"]).as_uri() if local else res["path"])
        return f'<img src="{src}" alt="{row["title"]} — {side}" width="100%">'

    out = ["## Screenshots", ""]
    base = f"`{m['base']}` @ `{m['base_sha'][:7]}`" if m.get("base_sha") else "—"
    out.append(f"<sub>Before: {base} · After: this branch"
               f"{' (with uncommitted changes)' if m.get('dirty') else ''}. Captured headlessly with demo data "
               f"(`scripts/pr_shots.py`).</sub>")
    out.append("")
    for row in m["views"]:
        out.append(f"### {row['title']}")
        if row.get("note"):
            out.append(row["note"])
        out += ["", "| Before | After |", "|---|---|", f"| {cell('before', row)} | {cell('after', row)} |", ""]
    return "\n".join(out).rstrip() + "\n"


def _owner_repo(remote: str) -> str:
    url = git("remote", "get-url", remote)
    m = re.search(r"github\.com[:/]([^/]+)/(.+?)(?:\.git)?/?$", url)
    if not m:
        raise ShotError(f"remote {remote} is not a GitHub URL: {url}")
    return f"{m.group(1)}/{m.group(2)}"


def cmd_upload(a: argparse.Namespace) -> int:
    m = json.loads(Path(a.manifest).expanduser().read_text())
    files = [r[s]["path"] for r in m["views"] for s in ("before", "after") if r.get(s) and r[s].get("path")]
    if not files:
        raise ShotError("manifest has no screenshots")
    slug = re.sub(r"[^A-Za-z0-9._-]+", "-", a.folder or m.get("head") or "pr").strip("-") or "pr"
    stamp = Path(m["run_dir"]).name
    prefix = f"{slug}/{stamp}"
    repo = _owner_repo(a.remote)
    branch = a.branch
    for attempt in range(3):
        tip = ""
        r = subprocess.run(["git", "fetch", "--quiet", a.remote, f"refs/heads/{branch}"], cwd=ROOT,
                           capture_output=True, text=True, timeout=120)
        if r.returncode == 0:
            tip = git("rev-parse", "FETCH_HEAD")
        with tempfile.TemporaryDirectory() as td:
            env = {**os.environ, "GIT_INDEX_FILE": str(Path(td) / "index")}
            if tip:
                git("read-tree", tip, env=env)
            else:
                git("read-tree", "--empty", env=env)
            if not tip:
                p = Path(td) / "README.md"
                p.write_text("# PR screenshots\n\nImages linked from pull request descriptions "
                             "(`scripts/pr_shots.py`). Not part of the code — safe to ignore.\n")
                blob = git("hash-object", "-w", str(p))
                git("update-index", "--add", "--cacheinfo", f"100644,{blob},README.md", env=env)
            for f in files:
                blob = git("hash-object", "-w", f)
                git("update-index", "--add", "--cacheinfo", f"100644,{blob},{prefix}/{Path(f).name}", env=env)
            tree = git("write-tree", env=env)
        msg = f"screenshots for {m.get('head') or slug}"
        commit = git("commit-tree", tree, *(["-p", tip] if tip else []), "-m", msg)
        push = subprocess.run(["git", "push", "--quiet", a.remote, f"{commit}:refs/heads/{branch}"], cwd=ROOT,
                              capture_output=True, text=True, timeout=180)
        if push.returncode == 0:
            break
        if attempt == 2 or "rejected" not in (push.stderr + push.stdout):
            raise ShotError(f"push to {branch} failed: {(push.stderr or push.stdout).strip()[:400]}")
        log("pr-assets moved on meanwhile — retrying")
    urls = {f: f"https://github.com/{repo}/blob/{commit}/{prefix}/{Path(f).name}?raw=true" for f in files}
    md = markdown(m, urls)
    out = Path(m["run_dir"]) / "screenshots.md"
    out.write_text(md)
    m["uploaded"] = {"branch": branch, "commit": commit, "urls": urls, "markdown": str(out)}
    Path(a.manifest).expanduser().write_text(json.dumps(m, indent=2))
    log(f"pushed {len(files)} images to {branch} @ {commit[:10]} — markdown in {out}")
    print(md)
    return 0


def cmd_doctor(_a: argparse.Namespace) -> int:
    ok = True
    for label, fn in (("python", find_python), ("chrome", find_chrome),
                      ("base", lambda: " @ ".join(resolve_base("")))):
        try:
            log(f"{label}: {fn()}")
        except ShotError as e:
            ok = False
            log(f"{label}: MISSING — {e}")
    gh = shutil.which("gh")
    if gh:
        r = subprocess.run([gh, "auth", "status"], capture_output=True, text=True)
        log("gh: " + ("signed in" if r.returncode == 0 else "NOT signed in — gh auth login"))
        ok &= r.returncode == 0
    else:
        ok = False
        log("gh: MISSING — brew install gh")
    try:
        log(f"github repo: {_owner_repo('origin')}")
    except ShotError as e:
        ok = False
        log(str(e))
    return 0 if ok else 1


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="pr_shots", description=__doc__.split("\n\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    c = sub.add_parser("capture", help="boot before/after Jarvis web UIs and screenshot each view")
    c.add_argument("--spec", required=True, help="JSON file, inline JSON, or - for stdin")
    c.add_argument("--base", default="", help="before side: branch/sha (default: merge-base with origin's default branch)")
    c.add_argument("--only", choices=("both", "before", "after"), default="both")
    c.add_argument("--views", default="", help="comma-separated view names to (re)capture")
    c.add_argument("--scale", type=float, default=DEFAULT_SCALE, help="device pixel ratio (default 2)")
    c.add_argument("--out", default="", help="run folder (default: $TMPDIR/jarvis-pr-shots/<time>)")
    c.add_argument("--keep", action="store_true", help="keep fake homes / profiles for debugging")
    c.set_defaults(fn=cmd_capture)
    u = sub.add_parser("upload", help="push the PNGs to an orphan assets branch, print PR Markdown")
    u.add_argument("--manifest", required=True)
    u.add_argument("--remote", default="origin")
    u.add_argument("--branch", default="pr-assets")
    u.add_argument("--folder", default="", help="folder in the assets branch (default: head branch name)")
    u.set_defaults(fn=cmd_upload)
    d = sub.add_parser("doctor", help="check Chrome, Python, gh and the GitHub remote")
    d.set_defaults(fn=cmd_doctor)
    a = ap.parse_args(argv)
    try:
        return a.fn(a)
    except ShotError as e:
        log(f"error: {e}")
        return 2


if __name__ == "__main__":
    sys.exit(main())
