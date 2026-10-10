"""Why a model request failed, in words a person can act on (UI-free).

Every way a turn can die — the laptop slept and the socket went away, a
rate limit, an expired sign-in, a retired model, a provider having a bad
day — becomes one :class:`ErrorReport`: a short title, one or two plain
sentences, the actions that fix it (``/retry``, ``/model``, ``/key`` …) and
the raw provider text kept aside as *details*.

``show(report)`` hands it to the console: the TUI draws an ``ErrorBlock``,
the web remote mirrors it as an ``error`` event (a card with buttons), and
the legacy REPL gets a framed panel. ``state.last_api_error`` keeps the
newest one so a page that reconnects (a phone waking up) still sees why
the last turn stopped.
"""
from __future__ import annotations

import ast
import json
import re
import time
from dataclasses import asdict, dataclass, field
from typing import Any

# Kinds, roughly by "whose problem is it":
#   network · timeout · server · overloaded   → transient, retrying helps
#   rate_limit · quota                        → wait, or use another model
#   auth · payment · model · permission       → the user has to change something
#   context · request · unknown               → the request itself
TRANSIENT = frozenset({"network", "timeout", "server", "overloaded", "rate_limit"})

# Glyph per kind — the TUI uses it as the block's gutter, the web maps the
# kind to an icon of its own.
GLYPHS = {
    "network": "⌁",
    "timeout": "◷",
    "server": "▲",
    "overloaded": "▲",
    "rate_limit": "◔",
    "quota": "◔",
    "auth": "⚿",
    "payment": "$",
    "model": "◇",
    "permission": "⊘",
    "context": "▤",
    "request": "✗",
    "unknown": "✗",
}

# Shown in the details line instead of a bare number.
_STATUS_NAMES = {
    400: "Bad request", 401: "Unauthorized", 402: "Payment required",
    403: "Forbidden", 404: "Not found", 408: "Request timeout",
    409: "Conflict", 413: "Too large", 422: "Unprocessable",
    429: "Too many requests", 500: "Server error", 502: "Bad gateway",
    503: "Unavailable", 504: "Gateway timeout", 529: "Overloaded",
}

DETAIL_MAX = 1600


@dataclass
class Action:
    """One way out: ``command`` is a slash command (``/retry``, ``/model``),
    or an ``https://`` link the UI opens."""

    label: str
    command: str
    primary: bool = False


@dataclass
class ErrorReport:
    kind: str
    title: str
    message: str
    actions: list[Action] = field(default_factory=list)
    provider: str = ""      # display name ("OpenAI Codex")
    model: str = ""
    status: int | None = None
    code: str = ""          # provider's own error code, if it gave one
    detail: str = ""        # raw provider text, cleaned up
    retry_after: int | None = None
    attempts: int = 0       # automatic retries already made
    ts: float = field(default_factory=time.time)

    @property
    def transient(self) -> bool:
        return self.kind in TRANSIENT

    @property
    def severity(self) -> str:
        """``warn`` for things that usually pass by themselves, else ``error``."""
        return "warn" if self.transient else "error"

    @property
    def glyph(self) -> str:
        return GLYPHS.get(self.kind, "✗")

    def status_label(self) -> str:
        if not self.status:
            return self.code or ""
        name = _STATUS_NAMES.get(self.status, "")
        return f"{self.status} {name}".strip()

    def meta(self) -> list[str]:
        """Provider · model · status — the dim line under the title."""
        return [p for p in (self.provider, self.model, self.status_label()) if p]

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["severity"] = self.severity
        d["transient"] = self.transient
        d["glyph"] = self.glyph
        d["status_label"] = self.status_label()
        return d

    def plain(self) -> str:
        """Text form (copy button, logs, tests)."""
        lines = [self.title]
        meta = self.meta()
        if meta:
            lines.append(" · ".join(meta))
        lines.append(self.message)
        if self.actions:
            lines.append("Try: " + " · ".join(
                a.command if a.command.startswith("/") else a.label for a in self.actions
            ))
        if self.detail:
            lines.append("")
            lines.append(self.detail)
        return "\n".join(lines)


# ── reading provider errors ──────────────────────────────────────────────


def _parse_blob(text: str) -> Any:
    """A JSON (or Python-repr) object embedded in ``text``, else None."""
    start = text.find("{")
    if start < 0:
        return None
    blob = text[start:]
    for loader in (json.loads, ast.literal_eval):
        try:
            return loader(blob)
        except Exception:
            continue
    return None


def _dig(body: Any) -> tuple[str, str, str]:
    """(message, code, type) from the shapes providers use:
    ``{"error": {"message", "type", "code"}}``, ``{"message", "code"}``,
    OpenRouter's ``{"error": {"message", "metadata": {"raw"}}}`` …"""
    if not isinstance(body, dict):
        return "", "", ""
    err = body.get("error", body)
    if isinstance(err, str):
        return err, str(body.get("code") or ""), str(body.get("type") or "")
    if not isinstance(err, dict):
        return "", "", ""
    msg = str(err.get("message") or err.get("detail") or "")
    meta = err.get("metadata")
    if isinstance(meta, dict) and meta.get("raw"):
        raw = meta["raw"]
        inner = _dig(_parse_blob(raw)) if isinstance(raw, str) else _dig(raw)
        if inner[0]:
            msg = f"{msg} — {inner[0]}" if msg and inner[0] not in msg else inner[0]
    code = err.get("code") or body.get("code") or ""
    etype = err.get("type") or body.get("type") or ""
    return msg, str(code), str(etype if etype != "error" else "")


def provider_message(exc: BaseException) -> tuple[str, str, str]:
    """(message, code, type) a provider put in its error, or ("", "", "")."""
    body = getattr(exc, "body", None)
    msg, code, etype = _dig(body)
    if not msg:
        msg, code2, etype2 = _dig(_parse_blob(str(exc)))
        code, etype = code or code2, etype or etype2
    if not msg:
        resp = getattr(exc, "response", None)
        try:
            text = resp.text if resp is not None else ""
        except Exception:
            text = ""
        if text:
            msg, code2, etype2 = _dig(_parse_blob(text))
            code, etype = code or code2, etype or etype2
    code = code or str(getattr(exc, "code", "") or "")
    return msg.strip(), code, etype


def status_of(exc: BaseException) -> int | None:
    for attr in ("status_code", "status"):
        v = getattr(exc, attr, None)
        if isinstance(v, int):
            return v
    resp = getattr(exc, "response", None)
    v = getattr(resp, "status_code", None)
    return v if isinstance(v, int) else None


def retry_after_of(exc: BaseException) -> int | None:
    """Seconds the provider asked us to wait (``retry-after`` header)."""
    resp = getattr(exc, "response", None)
    headers = getattr(resp, "headers", None)
    if not headers:
        return None
    for key in ("retry-after", "x-ratelimit-reset-requests", "x-ratelimit-reset"):
        try:
            raw = headers.get(key)
        except Exception:
            raw = None
        if not raw:
            continue
        try:
            secs = float(str(raw).rstrip("s"))
        except ValueError:
            continue
        if secs > 1e9:  # an epoch timestamp
            secs -= time.time()
        if 0 < secs < 86400:
            return int(round(secs))
    return None


def clean_detail(text: str) -> str:
    """Raw error text, trimmed for a details pane: no SDK prefix, at most
    ``DETAIL_MAX`` characters, request ids kept (support asks for them)."""
    t = (text or "").strip()
    t = re.sub(r"^Error code: \d+ - ", "", t)
    if t.startswith("{"):
        # The SDKs print the body as a Python repr — show it as JSON.
        body = _parse_blob(t)
        if isinstance(body, dict):
            t = json.dumps(body, ensure_ascii=False)
    if len(t) > DETAIL_MAX:
        t = t[:DETAIL_MAX].rstrip() + " …"
    return t


# ── what kind of failure is it ───────────────────────────────────────────

_NETWORK_NAMES = (
    "APIConnectionError", "ConnectError", "ConnectionError", "ConnectionResetError",
    "ConnectionAbortedError", "ConnectionRefusedError", "RemoteProtocolError",
    "ReadError", "WriteError", "NetworkError", "ProtocolError", "BrokenPipeError",
    "SSLError", "SSLEOFError", "gaierror", "IncompleteRead", "ChunkedEncodingError",
    "ProxyError", "LocalProtocolError",
)
_TIMEOUT_NAMES = (
    "APITimeoutError", "TimeoutError", "ReadTimeout", "WriteTimeout",
    "ConnectTimeout", "PoolTimeout", "TimeoutException",
)
_NETWORK_HINTS = (
    "connection error", "connection reset", "connection aborted", "connection refused",
    "peer closed connection", "server disconnected", "network is unreachable",
    "nodename nor servname", "name or service not known", "temporary failure in name resolution",
    "no route to host", "broken pipe", "eof occurred", "incomplete chunked read",
    "remote end closed", "getaddrinfo failed", "network is down", "socket is not connected",
    "software caused connection abort",
)
_QUOTA_HINTS = (
    "usage_limit", "usage limit", "quota", "insufficient_quota", "billing",
    "weekly limit", "daily limit", "monthly limit", "limit reached", "plan limit",
)
_MODEL_HINTS = (
    "model_not_found", "model not found", "does not exist", "unknown model",
    "no such model", "model is not supported", "not a valid model", "invalid model",
    "is not available", "model is unavailable", "has been deprecated", "decommissioned",
    "no endpoints found",
)
_AUTH_HINTS = (
    "invalid api key", "invalid x-api-key", "incorrect api key", "authentication",
    "unauthorized", "invalid_api_key", "token expired", "token has expired",
    "invalid token", "not authenticated", "oauth",
)


def _names(exc: BaseException) -> set[str]:
    out: set[str] = set()
    seen = 0
    e: BaseException | None = exc
    while e is not None and seen < 6:
        out.update(c.__name__ for c in type(e).__mro__)
        e = e.__cause__ or e.__context__
        seen += 1
    return out


def _chain_text(exc: BaseException) -> str:
    parts, seen = [], 0
    e: BaseException | None = exc
    while e is not None and seen < 6:
        parts.append(str(e))
        e = e.__cause__ or e.__context__
        seen += 1
    return " | ".join(p for p in parts if p).lower()


def kind_of(exc: BaseException, status: int | None = None) -> str:
    names = _names(exc)
    text = _chain_text(exc)
    status = status if status is not None else status_of(exc)

    if status is None:
        if names & set(_TIMEOUT_NAMES) or "timed out" in text or "stalled" in text:
            return "timeout"
        if names & set(_NETWORK_NAMES) or any(h in text for h in _NETWORK_HINTS):
            return "network"
    try:
        from .context_budget import is_context_overflow

        if is_context_overflow(exc):
            return "context"
    except Exception:
        pass
    if status == 401 or (status is None and any(h in text for h in ("unauthorized", "invalid api key"))):
        return "auth"
    if status == 402 or "insufficient credits" in text or "credit balance" in text:
        return "payment"
    if status == 429 or "rate limit" in text or "rate_limit" in text or "too many requests" in text:
        return "quota" if any(h in text for h in _QUOTA_HINTS) else "rate_limit"
    if any(h in text for h in ("usage_limit_reached", "insufficient_quota")):
        return "quota"
    if status == 529 or "overloaded" in text:
        return "overloaded"
    if status is None and any(h in text for h in ("server_error", "internal error", "internal_error",
                                                  "service unavailable", "bad gateway")):
        return "server"
    if status is not None and status >= 500:
        return "server"
    if status == 403:
        return "permission"
    if status == 404 or any(h in text for h in _MODEL_HINTS):
        return "model"
    if status in (408,):
        return "timeout"
    if status is None and any(h in text for h in _AUTH_HINTS) and "error" in text:
        return "auth"
    if status is not None and 400 <= status < 500:
        return "request"
    if names & set(_NETWORK_NAMES):
        return "network"
    return "unknown"


# ── building reports ─────────────────────────────────────────────────────

RETRY = "/retry"


def _wait_words(secs: int | None) -> str:
    if not secs:
        return ""
    if secs < 90:
        return f"{secs}s"
    if secs < 5400:
        return f"{round(secs / 60)} min"
    return f"{round(secs / 3600, 1):g} h"


def _says_nothing_new(said: str, ours: str) -> bool:
    """The provider's words only repeat ours ("Overloaded", "Not Found")."""
    words = set(re.findall(r"[a-z]{3,}", said.lower()))
    return not words or words <= set(re.findall(r"[a-z]{3,}", ours.lower()))


def _sign_in_action(provider: str, auth_mode: str) -> Action:
    from ..constants import PROVIDER_ANTHROPIC, PROVIDER_ANTIGRAVITY, PROVIDER_OPENAI_CODEX

    if provider in (PROVIDER_OPENAI_CODEX, PROVIDER_ANTIGRAVITY) or (
            provider == PROVIDER_ANTHROPIC and auth_mode == "oauth"):
        return Action("Sign in again", "/login", primary=True)
    return Action("Replace key", "/key", primary=True)


def describe(kind: str, *, provider: str = "", provider_id: str = "", model: str = "",
             auth_mode: str = "", status: int | None = None, code: str = "",
             detail: str = "", provider_said: str = "", retry_after: int | None = None,
             attempts: int = 0, mid_reply: bool = False) -> ErrorReport:
    """Title, plain-English message and actions for one kind of failure."""
    who = provider or "The provider"
    said = provider_said.strip().rstrip(".")
    if len(said) > 220:
        said = said[:217].rstrip() + "…"
    tried = f" Jarvis tried {attempts + 1} times." if attempts else ""
    retry = Action("Retry", RETRY, primary=True)
    pick = Action("Switch model", "/model")

    if kind == "network":
        title = "Connection lost"
        msg = (f"The connection to {who} dropped"
               + (" in the middle of the reply" if mid_reply else "")
               + " — this usually happens when the computer sleeps or the network changes."
               + tried + " Check you're online, then retry.")
        actions = [retry, pick]
    elif kind == "timeout":
        title = "The model stopped responding"
        msg = (f"{who} went quiet and the request timed out." + tried
               + " Free and queued models often stall under load — retry, or pick a faster model."
               " HARNESS_HTTP_READ_TIMEOUT gives slow models more time.")
        actions = [retry, pick]
    elif kind == "overloaded":
        title = f"{who} is overloaded"
        msg = "The provider is turning requests away right now." + tried + " It usually clears within a minute."
        actions = [retry, pick]
    elif kind == "server":
        title = f"{who} had a server error"
        msg = "Something broke on the provider's side, not in your request." + tried + " Retrying usually works."
        actions = [retry, pick]
    elif kind == "rate_limit":
        title = "Rate limited"
        wait = _wait_words(retry_after)
        msg = (f"{who} is limiting how fast {model or 'this model'} can be called."
               + (f" It asked to wait {wait}." if wait else " Wait a little and retry.")
               + " Switching to another model works right away.")
        actions = [retry, pick]
    elif kind == "quota":
        title = "Usage limit reached"
        wait = _wait_words(retry_after)
        msg = (f"Your {who} plan has used up its allowance for now"
               + (f" — it resets in about {wait}." if wait else ".")
               + " Pick another model or provider to keep going.")
        actions = [pick, Action("Change provider", "/provider")]
        if retry_after:
            actions.append(Action("Retry", RETRY))
    elif kind == "auth":
        from ..constants import PROVIDER_ANTHROPIC, PROVIDER_ANTIGRAVITY, PROVIDER_OPENAI_CODEX

        oauth = provider_id in (PROVIDER_OPENAI_CODEX, PROVIDER_ANTIGRAVITY) or (
            provider_id == PROVIDER_ANTHROPIC and auth_mode == "oauth")
        title = "Signed out" if oauth else "API key rejected"
        msg = (f"Your {who} sign-in has expired and couldn't be refreshed. Sign in again — no restart needed."
               if oauth else
               f"{who} didn't accept the API key. Replace it — no restart needed.")
        actions = [_sign_in_action(provider_id, auth_mode), Action("Change provider", "/provider")]
    elif kind == "payment":
        from ..constants import PROVIDER_OPENROUTER

        title = "Out of credits"
        msg = f"The {who} account has no credit left for {model or 'this model'}. Top it up, or pick a free model."
        actions = [Action("Pick a free model", "/model", primary=True)]
        if provider_id == PROVIDER_OPENROUTER:
            actions.append(Action("Add credits", "https://openrouter.ai/settings/credits"))
    elif kind == "permission":
        title = "Model not available to you"
        msg = (f"{who} refused {model or 'this model'} for this account — your plan or key may not include it.")
        actions = [Action("Switch model", "/model", primary=True), Action("Change provider", "/provider")]
    elif kind == "model":
        title = "Model not found"
        msg = (f"{who} doesn't serve {model or 'this model'} (any more). Model line-ups change often —"
               " refresh the list and pick another.")
        actions = [Action("Switch model", "/model", primary=True), Action("Refresh models", "/model refresh")]
    elif kind == "context":
        title = "Conversation too long"
        msg = ("This chat no longer fits in the model's context window, even after trimming older parts."
               " Start a new chat, or pick a model with a bigger window.")
        actions = [Action("New chat", "/new", primary=True), pick]
    elif kind == "request":
        title = "Request rejected"
        msg = f"{who} refused the request." + (f" It said: “{said}”." if said else "")
        said = ""  # already in the message
        actions = [retry, pick]
    else:
        title = "Something went wrong"
        msg = "The request failed for a reason Jarvis doesn't recognise. The details below say what happened."
        actions = [retry, pick]

    if provider_id.startswith("local:"):
        title, msg, actions = _local_wording(kind, provider_id, who, model, title, msg, actions,
                                             mid_reply=mid_reply, said=said)

    if said and kind not in ("request",) and not _says_nothing_new(said, f"{title} {msg}"):
        msg = f"{msg}\n{who} said: “{said}”."
    return ErrorReport(
        kind=kind, title=title, message=msg, actions=actions, provider=provider, model=model,
        status=status, code=code, detail=clean_detail(detail), retry_after=retry_after,
        attempts=attempts,
    )


def _local_wording(kind: str, provider_id: str, who: str, model: str, title: str, msg: str,
                   actions: list[Action], *, mid_reply: bool, said: str) -> tuple[str, str, list[Action]]:
    """A model server on this computer failed: the fix is on this computer
    (start the app, pull the model, free memory), never a key or a plan."""
    try:
        from ..auth import local_models

        srv = local_models.get_server(provider_id)
    except Exception:
        srv = None
    rt = srv.runtime if srv is not None else None
    where = srv.host if srv is not None else "this computer"
    manage = Action("Local models", "/local-models")
    retry = Action("Retry", RETRY, primary=True)
    pick = Action("Switch model", "/model")
    if kind == "network" and not mid_reply:
        start = f" Start it: {rt.start_hint}" if rt is not None and rt.start_hint else " Start it"
        if srv is not None and srv.remote:
            start = " Check that computer is on and the server is running"
        return (f"{who} isn't running",
                f"Jarvis couldn't reach {who} at {where}.{start}, then retry.",
                [retry, manage, pick])
    if kind == "model":
        pull = f" Get it with: {rt.pull.rsplit(' ', 1)[0]} {model}" if rt is not None and rt.pull else ""
        return (f"{model or 'This model'} isn't on {who}",
                f"{who} doesn't have {model or 'this model'} (any more).{pull} — or pick another model.",
                [manage, pick])
    if kind == "timeout":
        return ("The local model is taking too long",
                f"{who} went quiet. A big model on the CPU, or a long chat, can take minutes per reply."
                " A smaller model or a smaller context (in Local models) answers faster;"
                " HARNESS_LOCAL_TIMEOUT gives it more time.",
                [retry, manage, pick])
    if kind == "server":
        return (f"{who} couldn't run {model or 'the model'}",
                "This usually means the model doesn't fit in memory. Pick a smaller model,"
                " or lower the context size in Local models.",
                [manage, pick, retry])
    if kind == "auth":
        return (f"{who} wants an API key", f"{who} at {where} refused the request without a valid key."
                " Add the key to the server in Local models.", [manage, pick])
    return title, msg, actions


def _context() -> tuple[str, str, str, str]:
    """(provider label, provider id, model, auth mode) of the session."""
    from .. import state

    pid = str(getattr(state, "provider", "") or "")
    try:
        from ..constants.providers import provider_label

        label = provider_label(pid)
    except Exception:
        label = pid
    return label, pid, str(getattr(state, "MODEL", "") or ""), str(getattr(state, "auth_mode", "") or "")


def classify(exc: BaseException, *, kind: str | None = None, attempts: int = 0,
             mid_reply: bool = False) -> ErrorReport:
    """An :class:`ErrorReport` for any exception a model request raised."""
    label, pid, model, auth_mode = _context()
    status = status_of(exc)
    said, code, etype = provider_message(exc)
    k = kind or kind_of(exc, status)
    raw = str(exc) or type(exc).__name__
    detail = f"{type(exc).__name__}: {clean_detail(raw)}"
    # Network errors carry nothing a person can use beyond the type + text;
    # keep provider wording only where it adds something.
    if k in ("network", "timeout"):
        said = ""
    elif said and said.lower() in (raw.lower(), "error", "internal server error"):
        said = said if k in ("request", "unknown") else ""
    return describe(
        k, provider=label, provider_id=pid, model=model, auth_mode=auth_mode,
        status=status, code=code or etype, detail=detail, provider_said=said,
        retry_after=retry_after_of(exc), attempts=attempts, mid_reply=mid_reply,
    )


def report(kind: str, *, detail: str = "", exc: BaseException | None = None,
           title: str = "", message: str = "", actions: list[Action] | None = None,
           attempts: int = 0) -> ErrorReport:
    """A report for a failure the caller already understands (``kind``),
    optionally overriding the wording. ``exc`` fills status/details."""
    if exc is not None:
        rep = classify(exc, kind=kind, attempts=attempts)
    else:
        label, pid, model, auth_mode = _context()
        rep = describe(kind, provider=label, provider_id=pid, model=model,
                       auth_mode=auth_mode, detail=detail, attempts=attempts)
    if title:
        rep.title = title
    if message:
        rep.message = message
    if actions is not None:
        rep.actions = actions
    if detail and exc is not None:
        rep.detail = clean_detail(detail)
    return rep


# ── showing one ──────────────────────────────────────────────────────────


def rich_panel(rep: ErrorReport):
    """Framed Rich panel — the legacy REPL and any console without a hook."""
    from rich.markup import escape
    from rich.panel import Panel
    from rich.text import Text

    color = "yellow" if rep.severity == "warn" else "red"
    body = Text()
    meta = rep.meta()
    if meta:
        body.append(" · ".join(meta) + "\n\n", style="dim")
    body.append(rep.message)
    if rep.actions:
        body.append("\n\n")
        for i, a in enumerate(rep.actions):
            if i:
                body.append("   ")
            body.append(f" {a.command if a.command.startswith('/') else a.label} ",
                        style=f"bold {color} reverse" if a.primary else "bold")
            if a.command.startswith("/") and a.label.lower() not in a.command:
                body.append(f" {a.label.lower()}", style="dim")
    if rep.detail:
        body.append("\n\n" + rep.detail, style="dim")
    return Panel(body, title=f"[bold {color}]{escape(rep.glyph)}  {escape(rep.title)}[/]",
                 title_align="left", border_style=color, padding=(1, 2), expand=True)


def show(rep: ErrorReport, *, console: Any = None) -> ErrorReport:
    """Put ``rep`` on screen (TUI block / web card / Rich panel) and remember it."""
    from .. import state
    from .turn_progress import report_turn_phase

    if console is None:
        from ..console import console

    try:
        state.last_api_error = {
            **rep.to_dict(),
            "session_id": getattr(state, "current_session_id", None),
            "at": len(getattr(state, "messages", []) or []),
        }
    except Exception:
        pass
    try:
        report_turn_phase(rep.title)
    except Exception:
        pass
    hook = getattr(console, "show_error", None)
    if callable(hook):
        hook(rep)
    else:
        console.print(rich_panel(rep))
    return rep


def current_error() -> dict | None:
    """The newest error, while nothing has happened in the chat since."""
    from .. import state

    err = getattr(state, "last_api_error", None)
    if not err:
        return None
    if err.get("session_id") != getattr(state, "current_session_id", None):
        return None
    if err.get("at") != len(getattr(state, "messages", []) or []):
        return None
    return err


def clear() -> None:
    from .. import state

    state.last_api_error = None


def can_retry() -> tuple[bool, str]:
    """Whether ``/retry`` has something to resend: the conversation must end
    on a user message (a prompt or tool results) the model never answered."""
    from .. import state

    msgs = getattr(state, "messages", None) or []
    if not msgs:
        return False, "Nothing to retry yet — send a message first."
    if msgs[-1].get("role") != "user":
        return False, "Nothing to retry — the last reply finished."
    return True, ""
