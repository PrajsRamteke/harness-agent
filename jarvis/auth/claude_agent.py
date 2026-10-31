"""Claude Code CLI as a provider — what's installed, who's signed in, which models.

UI-free. The provider (``claude_agent``) runs the user's own, unmodified
``claude`` binary through Anthropic's Claude Agent SDK; the model requests are
made by that binary, never by Jarvis (see ``claude_agent_client.py``).

**Account safety** — what this module (and the client) deliberately never do:

* read, copy, store or forward Claude Code's login (``~/.claude/.credentials.json``,
  the macOS keychain item, OAuth tokens). Sign-in status comes from asking the
  binary itself (``claude auth status --json``), which reports *whether* it is
  signed in — the fields kept here are a whitelist without any secret;
* sign anyone in: the user runs ``claude auth login`` (Anthropic's own flow) in
  a terminal; the TUI only hands its terminal to that process (``suspend``);
* modify the binary, point it at a proxy, or add/forge headers. Jarvis is
  named honestly through ``CLAUDE_AGENT_SDK_CLIENT_APP``.

Status is cached (``model_catalog/claude_agent_status.json``) so startup never
waits on the binary; ``refresh()`` asks again (worker threads only).
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from . import catalog_cache

STATUS_CACHE = "claude_agent_status"
MODELS_CACHE = "claude_agent_models"
# `claude auth status` / `--version` answer in well under a second; a slow
# disk or a first-run update check can take longer.
PROBE_TIMEOUT = 20.0
# The SDK features used (verbatim prompts, dontAsk permissions) need this.
MIN_CLI_VERSION = "2.1.248"
SDK_PACKAGE = "claude-agent-sdk"
# The SDK's PyPI wheel bundles a second copy of the CLI (~130 MB); the source
# package is a few hundred KB and uses the `claude` already installed.
SDK_INSTALL_CMD = f"pip install --no-binary {SDK_PACKAGE} {SDK_PACKAGE}"
CLI_INSTALL_CMD = "curl -fsSL https://claude.ai/install.sh | bash"
CLI_DOCS_URL = "https://code.claude.com/docs/en/setup"
LOGIN_CMD = "claude auth login"

# Only these fields of `claude auth status --json` are ever kept or shown.
_STATUS_FIELDS = ("loggedIn", "authMethod", "apiProvider", "email", "orgName", "subscriptionType")

_lock = threading.Lock()
_memo: dict[str, Any] = {}


# ─── The binary ───────────────────────────────────────────────────────────


def _candidates() -> list[str]:
    home = Path.home()
    out = []
    env = os.environ.get("HARNESS_CLAUDE_PATH", "").strip()
    if env:
        out.append(os.path.expanduser(env))
    found = shutil.which("claude")
    if found:
        out.append(found)
    out += [
        str(home / ".local" / "bin" / "claude"),
        str(home / ".claude" / "local" / "claude"),
        "/opt/homebrew/bin/claude",
        "/usr/local/bin/claude",
    ]
    return out


def find_cli() -> str | None:
    """The user's installed ``claude`` (``HARNESS_CLAUDE_PATH`` wins), or the
    one bundled with the SDK's wheel. Never a modified copy."""
    for path in _candidates():
        if path and os.path.isfile(path) and os.access(path, os.X_OK):
            return path
    bundled = _bundled_cli()
    return bundled


def _bundled_cli() -> str | None:
    try:
        import importlib.util

        spec = importlib.util.find_spec("claude_agent_sdk")
    except (ImportError, ValueError):
        return None
    if not spec or not spec.origin:
        return None
    path = Path(spec.origin).parent / "_bundled" / "claude"
    return str(path) if path.is_file() else None


def sdk_version() -> str | None:
    """Installed claude-agent-sdk version (without importing it)."""
    try:
        from importlib.metadata import version

        return version(SDK_PACKAGE)
    except Exception:
        return None


def _version_key(v: str) -> tuple[int, ...]:
    return tuple(int(p) for p in re.findall(r"\d+", v)[:3]) or (0,)


def child_env() -> dict[str, str]:
    """Variables layered over the user's environment for the ``claude`` child.

    Nothing here touches authentication: the binary signs in exactly as when
    the user runs it. Values only switch off things Jarvis does itself and
    lift limits that would cut Jarvis's tools short.
    """
    from ..constants.models import VERSION

    return {
        # Name the app honestly in Claude Code's requests (no disguise).
        "CLAUDE_AGENT_SDK_CLIENT_APP": f"jarvis/{VERSION}",
        # Jarvis's tools are offered directly — no deferred tool search.
        "ENABLE_TOOL_SEARCH": "false",
        # Jarvis caps tool output itself; Claude Code's 25K-token cut would
        # truncate a read Jarvis already sized.
        "MAX_MCP_OUTPUT_TOKENS": "200000",
        # A tool call waits on Jarvis (approvals can take a while).
        "MCP_TOOL_TIMEOUT": str(24 * 3600 * 1000),
        # Jarvis owns project context, memory and skills.
        "CLAUDE_CODE_DISABLE_CLAUDE_MDS": "1",
        "CLAUDE_CODE_DISABLE_AUTO_MEMORY": "1",
        "CLAUDE_CODE_DISABLE_BUNDLED_SKILLS": "1",
        "CLAUDE_CODE_DISABLE_ADVISOR_TOOL": "1",
        "CLAUDE_CODE_DISABLE_BACKGROUND_TASKS": "1",
    }


# Variables removed from the child's environment: they would skip the login
# check (SIMPLE), override Jarvis's effort, cap tool calls, or make the child
# think it runs inside another Claude Code session (when Jarvis was started
# from one).
STRIPPED_ENV = (
    "CLAUDECODE", "CLAUDE_CODE_SIMPLE", "CLAUDE_CODE_EFFORT_LEVEL",
    "CLAUDE_CODE_ENTRYPOINT", "CLAUDE_CODE_SSE_PORT",
)


def _probe_env() -> dict[str, str]:
    env = {k: v for k, v in os.environ.items() if k not in STRIPPED_ENV}
    env.setdefault("NO_COLOR", "1")
    return env


def _run(cli: str, *args: str) -> tuple[int, str, str]:
    try:
        p = subprocess.run(
            [cli, *args], capture_output=True, text=True, timeout=PROBE_TIMEOUT,
            env=_probe_env(), stdin=subprocess.DEVNULL,
        )
    except (OSError, subprocess.SubprocessError) as e:
        return -1, "", str(e)
    return p.returncode, p.stdout or "", p.stderr or ""


def cli_version(cli: str) -> str | None:
    code, out, _err = _run(cli, "--version")
    if code != 0:
        return None
    m = re.search(r"\d+\.\d+\.\d+", out)
    return m.group(0) if m else None


def auth_status(cli: str) -> dict[str, Any]:
    """Ask the binary whether it's signed in. Only whitelisted, secret-free
    fields are returned (never a token)."""
    code, out, err = _run(cli, "auth", "status", "--json")
    try:
        raw = json.loads(out) if out.strip() else {}
    except ValueError:
        raw = {}
    if not isinstance(raw, dict):
        raw = {}
    kept = {k: raw.get(k) for k in _STATUS_FIELDS if k in raw}
    kept["loggedIn"] = bool(raw.get("loggedIn"))
    if code not in (0, 1) and not raw:
        kept["error"] = (err or out).strip()[:300]
    return kept


# ─── Status ───────────────────────────────────────────────────────────────


@dataclass
class Status:
    cli_path: str = ""
    cli_version: str = ""
    sdk_version: str = ""
    logged_in: bool = False
    auth_method: str = ""      # claude.ai | console | api_key | …
    api_provider: str = ""     # firstParty | bedrock | vertex | …
    email: str = ""
    org: str = ""
    plan: str = ""             # pro | max | team | enterprise | …
    checked_at: float = 0.0
    error: str = ""
    extra: dict = field(default_factory=dict)

    @property
    def cli_ok(self) -> bool:
        return bool(self.cli_path) and bool(self.cli_version) and (
            _version_key(self.cli_version) >= _version_key(MIN_CLI_VERSION))

    @property
    def ready(self) -> bool:
        return self.cli_ok and bool(self.sdk_version) and self.logged_in

    @property
    def subscription(self) -> bool:
        """Signed in with a Claude plan (usage is part of the plan, not billed
        per token)."""
        return self.auth_method in ("claude.ai", "claudeai", "oauth") or bool(self.plan)

    def problem(self) -> tuple[str, str, str]:
        """``(code, message, fix)`` for the first thing that's missing, or
        ``("", "", "")`` when ready. ``fix`` is a command the user runs."""
        if not self.cli_path:
            return ("no_cli", "Claude Code isn't installed on this computer.", CLI_INSTALL_CMD)
        if not self.cli_version:
            return ("cli_broken", f"{self.cli_path} didn't answer `--version`.", "claude update")
        if not self.cli_ok:
            return ("cli_old", f"Claude Code {self.cli_version} is too old — {MIN_CLI_VERSION}+ is needed.",
                    "claude update")
        if not self.sdk_version:
            return ("no_sdk", "The Claude Agent SDK (Python package) isn't installed.", SDK_INSTALL_CMD)
        if not self.logged_in:
            return ("signed_out", "Claude Code isn't signed in.", LOGIN_CMD)
        return ("", "", "")

    def account_line(self) -> str:
        """``you@x.com · Max`` / ``API key`` — what the binary says it uses."""
        if not self.logged_in:
            return ""
        if self.subscription:
            plan = (self.plan or "").replace("_", " ").title()
            bits = [b for b in (self.email, f"Claude {plan}" if plan else "Claude plan") if b]
            return " · ".join(bits)
        if self.api_provider and self.api_provider not in ("firstParty", ""):
            return f"{self.api_provider} account"
        return "Anthropic API key" + (f" · {self.email}" if self.email else "")

    def public(self) -> dict[str, Any]:
        code, msg, fix = self.problem()
        return {
            "installed": bool(self.cli_path),
            "cli_path": self.cli_path,
            "cli_version": self.cli_version,
            "cli_ok": self.cli_ok,
            "sdk_version": self.sdk_version,
            "logged_in": self.logged_in,
            "account": self.account_line(),
            "subscription": self.subscription,
            "ready": self.ready,
            "problem": code,
            "message": msg,
            "fix": fix,
            "login_cmd": LOGIN_CMD,
            "install_cmd": CLI_INSTALL_CMD,
            "sdk_install_cmd": SDK_INSTALL_CMD,
            "docs": CLI_DOCS_URL,
            "checked_at": self.checked_at,
        }

    def to_json(self) -> dict[str, Any]:
        return {k: getattr(self, k) for k in (
            "cli_path", "cli_version", "sdk_version", "logged_in", "auth_method",
            "api_provider", "email", "org", "plan", "checked_at", "error")}

    @classmethod
    def from_json(cls, d: dict) -> "Status":
        s = cls()
        for k in s.to_json():
            if k in d:
                setattr(s, k, d[k])
        return s


def probe() -> Status:
    """Ask the binary now (slow: runs `claude` twice). Worker threads only."""
    s = Status(checked_at=time.time(), sdk_version=sdk_version() or "")
    cli = find_cli()
    if not cli:
        return s
    s.cli_path = cli
    s.cli_version = cli_version(cli) or ""
    if not s.cli_version:
        return s
    info = auth_status(cli)
    s.logged_in = bool(info.get("loggedIn"))
    s.auth_method = str(info.get("authMethod") or "")
    s.api_provider = str(info.get("apiProvider") or "")
    s.email = str(info.get("email") or "")
    s.org = str(info.get("orgName") or "")
    s.plan = str(info.get("subscriptionType") or "") if s.logged_in else ""
    s.error = str(info.get("error") or "")
    return s


def refresh() -> Status:
    """Probe and cache. Worker threads only."""
    s = probe()
    with _lock:
        _memo["status"] = s
    catalog_cache.write(STATUS_CACHE, s.to_json())
    return s


def status() -> Status:
    """The last known status — memory, then the disk cache; never runs the
    binary. A status nobody has checked yet reads "not installed" only when
    no ``claude`` can be found at all (a cheap file check)."""
    with _lock:
        s = _memo.get("status")
    if s is not None:
        return s
    payload, _fresh = catalog_cache.read(STATUS_CACHE)
    if isinstance(payload, dict):
        s = Status.from_json(payload)
        # The SDK may have been installed (or the binary moved) since.
        s.sdk_version = sdk_version() or ""
        if s.cli_path and not os.path.isfile(s.cli_path):
            s.cli_path = find_cli() or ""
    else:
        s = Status(cli_path=find_cli() or "", sdk_version=sdk_version() or "")
    with _lock:
        _memo["status"] = s
    return s


def refresh_in_background() -> None:
    threading.Thread(target=_quiet_refresh, daemon=True, name="jarvis-claude-agent-status").start()


def _quiet_refresh() -> None:
    try:
        refresh()
    except Exception:
        pass


def status_is_stale(max_age: float = 600.0) -> bool:
    return time.time() - (status().checked_at or 0) > max_age


def is_ready() -> bool:
    """Cached: installed, new enough, SDK present, and the binary said it's
    signed in the last time it was asked."""
    try:
        return status().ready
    except Exception:
        return False


def is_usable() -> bool:
    """Ready, or close enough to try (installed + SDK, sign-in unknown yet)."""
    s = status()
    return s.ready or (bool(s.cli_path) and bool(s.sdk_version) and not s.checked_at)


def reset() -> None:
    """Forget the in-memory status (tests, `/provider claude` recheck)."""
    with _lock:
        _memo.clear()


# ─── Models ───────────────────────────────────────────────────────────────

# Before Claude Code has been asked: its own aliases, which always resolve to
# the newest model of each family for the signed-in account.
SEED_MODELS: tuple[dict[str, Any], ...] = (
    {"id": "default", "label": "Default", "desc": "Claude Code's recommended model for your account"},
    {"id": "opus", "label": "Opus", "desc": "Most capable"},
    {"id": "sonnet", "label": "Sonnet", "desc": "Fast and capable"},
    {"id": "haiku", "label": "Haiku", "desc": "Fastest"},
)
DEFAULT_MODEL = "default"


def _clean_model(m: Any) -> dict[str, Any] | None:
    if not isinstance(m, dict):
        return None
    mid = str(m.get("value") or m.get("id") or "").strip()
    if not mid:
        return None
    efforts = m.get("supportedEffortLevels") or m.get("efforts") or []
    off = m.get("supportsThinkingOff", m.get("thinking_off"))
    return {
        "id": mid,
        "label": str(m.get("displayName") or m.get("label") or mid),
        "desc": str(m.get("description") or m.get("desc") or ""),
        "resolved": str(m.get("resolvedModel") or m.get("resolved") or ""),
        "efforts": [str(e) for e in efforts if isinstance(e, str)],
        "adaptive": bool(m.get("supportsAdaptiveThinking") or m.get("adaptive")),
        "thinking_off": None if off is None else bool(off),
    }


def store_models(raw: list[Any]) -> list[dict[str, Any]]:
    rows = [r for r in (_clean_model(m) for m in raw or []) if r]
    seen: set[str] = set()
    rows = [r for r in rows if not (r["id"] in seen or seen.add(r["id"]))]
    if rows:
        catalog_cache.write(MODELS_CACHE, rows)
        with _lock:
            _memo.pop("models", None)
    return rows


def models() -> list[dict[str, Any]]:
    """Claude Code's model list for this account (cached), else the aliases."""
    with _lock:
        cached = _memo.get("models")
    if cached is not None:
        return cached
    payload, _fresh = catalog_cache.read(MODELS_CACHE)
    rows = [r for r in (_clean_model(m) for m in payload) if r] if isinstance(payload, list) else []
    rows = rows or [_clean_model(m) for m in SEED_MODELS]
    with _lock:
        _memo["models"] = rows
    return rows


def models_are_live() -> bool:
    payload, _fresh = catalog_cache.read(MODELS_CACHE)
    return isinstance(payload, list) and bool(payload)


def models_cache_is_fresh() -> bool:
    payload, fresh = catalog_cache.read(MODELS_CACHE)
    return bool(payload) and fresh


def get_model(model_id: str) -> dict[str, Any] | None:
    mid = (model_id or "").strip()
    for m in models():
        if m["id"] == mid:
            return m
    return None


def default_model() -> str:
    rows = models()
    ids = [m["id"] for m in rows]
    if DEFAULT_MODEL in ids:
        return DEFAULT_MODEL
    return ids[0] if ids else DEFAULT_MODEL


def models_for_picker() -> list[tuple[str, str]]:
    out = []
    for m in models():
        label = m["label"] if m["label"] != m["id"] else ""
        desc = " · ".join(b for b in (label, m["desc"]) if b)
        out.append((m["id"], desc or m["id"]))
    return out


CONTEXT_CACHE = "claude_agent_context"


def note_context(model: str, info: Any) -> None:
    """``get_context_usage`` for ``model``: its window and the real model id."""
    if not isinstance(info, dict) or not info.get("maxTokens"):
        return
    try:
        row = {"max": int(info["maxTokens"]), "model": str(info.get("model") or "")}
    except (TypeError, ValueError):
        return
    with _lock:
        known = dict(_memo.get("context") or {})
        known[model or DEFAULT_MODEL] = row
        _memo["context"] = known
    catalog_cache.write(CONTEXT_CACHE, known)


def _contexts() -> dict:
    with _lock:
        known = _memo.get("context")
    if known is None:
        payload, _fresh = catalog_cache.read(CONTEXT_CACHE)
        known = payload if isinstance(payload, dict) else {}
        with _lock:
            _memo["context"] = known
    return known


def context_window(model: str) -> int | None:
    """Tokens the model's window holds, as Claude Code said (None: not asked yet)."""
    row = _contexts().get(model or DEFAULT_MODEL)
    return int(row["max"]) if isinstance(row, dict) and row.get("max") else None


def display_name(model: str) -> str:
    """``default`` → ``Opus 5.5``: the name of the model the alias runs."""
    m = get_model(model) or {}
    real = (_contexts().get(model or DEFAULT_MODEL) or {}).get("model") or m.get("resolved") or ""
    if real:
        for row in models():
            if (row["id"] == real or row.get("resolved") == real) and not row["label"].lower().startswith("default"):
                return row["label"]
    label = m.get("label") or model or "Claude"
    return label.split(" (")[0]


def refresh_models() -> bool:
    """Ask Claude Code which models this account has (starts the binary once,
    no model request). Worker threads only. True when a list arrived."""
    if not is_ready():
        return False
    try:
        from .claude_agent_client import fetch_server_info

        info = fetch_server_info()
    except Exception:
        return False
    raw = (info or {}).get("models") if isinstance(info, dict) else None
    return bool(store_models(raw or []))
