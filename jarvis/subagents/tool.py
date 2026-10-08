"""The ``spawn_agents`` tool: run 1–20 subagents in parallel, return their reports."""
from __future__ import annotations

import re
import threading
import time
from typing import Any

from .. import state
from . import team as team_mod
from .runner import RequestConfig, run_agent
from .team import AgentRun, Team

MAX_AGENTS = 20
TOTAL_REPORT_CHARS = 60_000
NAME_MAX = 40
MAX_IMAGES = 4

# The user asking for agents in so many words ("using multiple sub-agents",
# "spin up 4 agents", "agents in parallel"). Weak models ignore the tool unless
# told plainly, so such a turn gets an explicit instruction (repl/system.py).
EXPLICIT_RE = re.compile(
    r"\b(?:sub[- ]?agents?|multi(?:ple)?[- ]?agents?|parallel[- ]+agents?|agents?\s+in\s+parallel"
    r"|(?:several|many|some|\d{1,2}|two|three|four|five|six|ten|twenty)\s+(?:parallel\s+)?(?:sub[- ]?)?agents"
    r"|spawn(?:ing)?\s+(?:\w+\s+)?agents?|agent\s+team|team\s+of\s+agents|spawn_agents)\b",
    re.I,
)

SPAWN_AGENTS_TOOL = {
    "name": "spawn_agents",
    "description": (
        "Run 2-20 subagents IN PARALLEL, each with its own fresh context and tools, and get "
        "their reports back in one result. Several agents working at once finish a big job "
        "far sooner than doing every part yourself in sequence.\n"
        "USE IT for big tasks that split into independent parts: exploring or auditing "
        "several areas of a codebase, answering several research questions, reviewing many "
        "files, or making changes across separate files/modules (one agent per area).\n"
        "DON'T use it for small tasks (a few reads, one edit) or for steps that depend on "
        "each other — do those yourself.\n"
        "When the user asks for (sub)agents or parallel agents, use this tool — that's the request.\n"
        "Each agent knows NOTHING of this conversation: its `task` must be a complete brief "
        "— what to do, where (absolute paths, names), constraints, and exactly what to report "
        "back. Images the user attached to their message are shown to every agent.\n"
        "BUILDING ONE APP/PAGE: first lay the shared ground yourself in a few calls (folder, "
        "file layout, shared CSS variables / data shapes / global names — e.g. a short "
        "SPEC.md), then one agent per part (each view/tab/module in its own files, mode "
        "'edit'), then wire it together and check it after they report.\n"
        "Use mode 'explore' (read-only, the default) for investigation; 'edit' lets an agent "
        "change files and run commands — give every edit agent its own files so they don't "
        "collide. Agents can't ask the user anything. The user doesn't see the reports: "
        "after it returns, check what matters, then give one combined answer."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "goal": {
                "type": "string",
                "description": "The overall goal in 1-2 sentences, shared with every agent as context.",
            },
            "agents": {
                "type": "array",
                "description": "2-20 agents (1 is allowed for one long isolated job).",
                "items": {
                    "type": "object",
                    "properties": {
                        "name": {
                            "type": "string",
                            "description": "Short title of this agent's part, 2-4 words (e.g. 'Auth flow').",
                        },
                        "task": {
                            "type": "string",
                            "description": (
                                "Self-contained brief: the job, the files/dirs/areas it covers, "
                                "constraints, and what the report must contain."
                            ),
                        },
                        "mode": {
                            "type": "string",
                            "enum": ["explore", "edit"],
                            "description": "explore = read-only research (default) · edit = may change files and run commands.",
                        },
                    },
                    "required": ["name", "task"],
                },
            },
        },
        "required": ["agents"],
    },
}

SUBAGENT_TOOLS = [SPAWN_AGENTS_TOOL]


# ── the user's turn ──────────────────────────────────────────────────────

def _turn_message() -> dict | None:
    """The message that started the current user turn (not a tool result)."""
    from ..repl.system import _turn_anchor

    i = _turn_anchor()
    msgs = state.messages or []
    return msgs[i] if 0 <= i < len(msgs) and isinstance(msgs[i], dict) else None


def _text_of(content: Any) -> str:
    if isinstance(content, str):
        return content
    parts = []
    for b in content if isinstance(content, list) else []:
        kind = b.get("type") if isinstance(b, dict) else getattr(b, "type", None)
        if kind == "text":
            parts.append((b.get("text") if isinstance(b, dict) else getattr(b, "text", "")) or "")
    return "\n".join(parts)


def asked_for_agents() -> bool:
    """Did the user's current message explicitly ask for (sub)agents?"""
    m = _turn_message()
    if not m:
        return False
    from ..prompt_queue import strip_steer

    text = _text_of(m.get("content"))
    try:
        text = strip_steer(text)[0]
    except Exception:
        pass
    return bool(EXPLICIT_RE.search(text))


def turn_images() -> list[dict]:
    """Images the user attached to the current message — every agent gets
    them, so a "build what's in this picture" task can be split."""
    m = _turn_message()
    content = m.get("content") if m else None
    out: list[dict] = []
    for b in content if isinstance(content, list) else []:
        d = b if isinstance(b, dict) else (b.model_dump() if hasattr(b, "model_dump") else None)
        if isinstance(d, dict) and d.get("type") == "image":
            out.append(d)
    return out[-MAX_IMAGES:]


# ── settings ─────────────────────────────────────────────────────────────

def _setting(path: str, default: Any) -> Any:
    try:
        from ..storage.settings import get_settings

        v = get_settings().get(path, default)
        return default if v is None else v
    except Exception:
        return default


def enabled() -> bool:
    return bool(_setting("subagents.enabled", True))


def max_parallel() -> int:
    try:
        return max(1, min(MAX_AGENTS, int(_setting("subagents.max_parallel", MAX_AGENTS))))
    except (TypeError, ValueError):
        return MAX_AGENTS


def _max_turns() -> int:
    try:
        return max(4, min(200, int(_setting("subagents.max_steps", 40))))
    except (TypeError, ValueError):
        return 40


def _timeout_s() -> float:
    try:
        return max(60.0, min(7200.0, float(_setting("subagents.timeout_min", 20)) * 60))
    except (TypeError, ValueError):
        return 1200.0


# ── input checks ─────────────────────────────────────────────────────────

def _clean_name(raw: Any, n: int) -> str:
    name = " ".join(str(raw or "").split())
    name = name.replace("—", "-").strip(" -#*`")
    if len(name) > NAME_MAX:
        name = name[: NAME_MAX - 1].rstrip() + "…"
    return name or f"Agent {n}"


def _parse_agents(agents: Any) -> tuple[list[AgentRun], str | None]:
    if isinstance(agents, dict):
        agents = [agents]
    if not isinstance(agents, list) or not agents:
        return [], ("ERROR: spawn_agents needs `agents`: a list of 2-20 objects "
                    "{name, task, mode?}.")
    if len(agents) > MAX_AGENTS:
        return [], (f"ERROR: at most {MAX_AGENTS} agents run at once (got {len(agents)}). "
                    "Merge related parts, or run a second spawn_agents call after this one.")
    runs: list[AgentRun] = []
    used: dict[str, int] = {}
    for n, raw in enumerate(agents, 1):
        if isinstance(raw, str):
            raw = {"name": f"Agent {n}", "task": raw}
        if not isinstance(raw, dict):
            return [], f"ERROR: agent #{n} must be an object {{name, task, mode?}}."
        task = str(raw.get("task") or raw.get("prompt") or raw.get("description") or "").strip()
        if not task:
            return [], f"ERROR: agent #{n} has no `task` — give each agent a complete brief."
        name = _clean_name(raw.get("name") or raw.get("title"), n)
        key = name.lower()
        if key in used:
            used[key] += 1
            name = f"{name} {used[key]}"
        else:
            used[key] = 1
        mode = str(raw.get("mode") or "explore").strip().lower()
        if mode in ("write", "edit", "general", "worker", "code", "implement"):
            mode = "edit"
        else:
            mode = "explore"
        if state.plan_mode:
            mode = "explore"
        runs.append(AgentRun(len(runs), name, task, mode))
    return runs, None


# ── the tool ─────────────────────────────────────────────────────────────

def spawn_agents(agents: Any = None, goal: str = "", **_: Any) -> str:
    from ..console import console
    from ..repl.tool_events import current_tool_id
    from . import context as ctx

    if ctx.current() is not None:
        return "ERROR: subagents can't start their own subagents — do this part yourself."
    if not enabled():
        return ("ERROR: parallel subagents are turned off (/subagents on enables them). "
                "Do the work yourself.")
    if state.client is None:
        return "ERROR: no model client — sign in first."
    runs, err = _parse_agents(agents)
    if err:
        return err

    team_id = current_tool_id() or f"team-{int(time.time() * 1000)}"
    team = Team(team_id, " ".join(str(goal or "").split())[:600], runs,
                parent_thread=threading.get_ident())
    try:
        team.images = turn_images()
    except Exception:
        team.images = []
    cfg = RequestConfig.capture(max_turns=_max_turns(), deadline_s=_timeout_s())
    team_mod.register(team)
    team.start_notifier()
    team.changed()

    gate = threading.Semaphore(max_parallel())

    def _worker(agent: AgentRun) -> None:
        with gate:
            run_agent(team, agent, cfg)
        team.changed()

    threads = [
        threading.Thread(target=_worker, args=(a,), name=f"subagent-{a.index + 1}", daemon=True)
        for a in runs
    ]
    legacy = not callable(getattr(console, "subagents_update", None))
    if legacy:
        console.print(f"[cyan]⇉ {len(runs)} agents working in parallel…[/]")
    announced: set[int] = set()
    for t in threads:
        t.start()
    try:
        while not team.all_finished():
            if state.turn_cancelled():
                team.stop_all("cancelled")
                break
            team.wait_change(0.25)
            if legacy:
                _print_finished(console, team, announced)
    finally:
        if not team.all_finished():
            team.stop_all("cancelled")
        deadline = time.monotonic() + 6.0
        for t in threads:
            t.join(timeout=max(0.0, deadline - time.monotonic()))
        now = time.time()
        with team.lock:
            for a in team.agents:  # a thread that didn't unwind in time
                if not a.is_finished:
                    a.status, a.error, a.finished = "cancelled", "Cancelled", now
                    a.activity = "Cancelled"
        team.close()
        if legacy:
            _print_finished(console, team, announced)
    return format_result(team)


def _print_finished(console: Any, team: Team, announced: set[int]) -> None:
    from rich.markup import escape

    for a in team.agents:
        if a.is_finished and a.index not in announced:
            announced.add(a.index)
            mark = "[green]✓[/]" if a.status == "done" else "[red]✗[/]"
            console.print(f"  {mark} {escape(a.name)} [dim]· {a.steps} tool calls · "
                          f"{fmt_secs(a.elapsed())}[/]")


# ── result text (the model reads this; the board is rebuilt from it) ────

_WORD = {"done": "done", "error": "failed", "stopped": "stopped",
         "cancelled": "cancelled", "timeout": "timed out"}
_HEADER_RE = re.compile(
    r"^### (\d+)\. (.+?) — (done|failed|stopped|cancelled|timed out)"
    r"(?: \((explore|edit)\))? · (\d+) tool calls? · ([^\n]+)$",
    re.M,
)


def fmt_secs(s: float) -> str:
    s = int(round(s))
    if s < 60:
        return f"{s}s"
    m, s = divmod(s, 60)
    if m < 60:
        return f"{m}m {s:02d}s"
    h, m = divmod(m, 60)
    return f"{h}h {m:02d}m"


def format_result(team: Team) -> str:
    snap = team.snapshot()
    c = snap["counts"]
    agents = team.agents
    parts = []
    if c["done"]:
        parts.append(f"{c['done']} done")
    if c["failed"]:
        parts.append(f"{c['failed']} not finished")
    head = (f"Parallel agents: {len(agents)} ran in {fmt_secs(snap['elapsed'])} — "
            f"{', '.join(parts) or 'none finished'} · {snap['steps']} tool calls")
    cap = max(3000, TOTAL_REPORT_CHARS // max(1, len(agents)))
    out = [head]
    for a in agents:
        word = _WORD.get(a.status, a.status)
        calls = f"{a.steps} tool call{'' if a.steps == 1 else 's'}"
        out.append("")
        out.append(f"### {a.index + 1}. {a.name} — {word} ({a.mode}) · {calls} · "
                   f"{fmt_secs(a.elapsed())}")
        if a.files:
            out.append("Files changed: " + ", ".join(
                f"{p} (+{ad} −{rm})" for p, (ad, rm) in a.files.items()))
        if a.error:
            out.append(f"Note: {a.error}")
        report = a.report.strip()
        if len(report) > cap:
            report = report[:cap].rstrip() + "\n… (report shortened)"
        out.append("")
        out.append(report or "(no report)")
    out.append("")
    out.append("---")
    if c["failed"]:
        out.append("Some agents didn't finish: their parts may be incomplete — do the missing "
                   "work yourself or start agents again for just those parts.")
    out.append("The user has NOT seen these reports. Check claims that matter against the code, "
               "resolve any conflicts between agents, then give the user one combined answer.")
    return "\n".join(out)


def board_for(tool_id: str, tool_input: Any = None, output: str | None = None) -> dict | None:
    """A board snapshot for a spawn_agents call: live from memory, or rebuilt
    from its input + result text (after a restart / for an old session)."""
    team = team_mod.get(tool_id)
    if team is not None:
        return team.snapshot()
    raw = (tool_input or {}).get("agents") if isinstance(tool_input, dict) else None
    runs, _err = _parse_agents(raw if raw else [])
    by_index: dict[int, dict] = {}
    text = output or ""
    matches = list(_HEADER_RE.finditer(text))
    for k, m in enumerate(matches):
        end = matches[k + 1].start() if k + 1 < len(matches) else text.find("\n---\n", m.end())
        body = text[m.end(): end if end > 0 else None].strip()
        files: list[dict] = []
        error = ""
        lines = []
        for ln in body.splitlines():
            if ln.startswith("Files changed: ") and not lines:
                for f in re.finditer(r"([^,]+?) \(\+(\d+) −(\d+)\)", ln[len("Files changed: "):]):
                    files.append({"path": f.group(1).strip(), "added": int(f.group(2)),
                                  "removed": int(f.group(3))})
                continue
            if ln.startswith("Note: ") and not lines and not error:
                error = ln[6:]
                continue
            lines.append(ln)
        report = "\n".join(lines).strip()
        status = {v: k2 for k2, v in _WORD.items()}.get(m.group(3), "done")
        by_index[int(m.group(1)) - 1] = {
            "name": m.group(2), "status": status, "mode": m.group(4) or "explore",
            "steps": int(m.group(5)), "elapsed_label": m.group(6).strip(),
            "report": "" if report == "(no report)" else report,
            "files": files, "error": error,
        }
    if not runs and not by_index:
        return None
    count = max(len(runs), (max(by_index) + 1) if by_index else 0)
    agents = []
    for i in range(count):
        base = runs[i] if i < len(runs) else AgentRun(i, f"Agent {i + 1}", "", "explore")
        got = by_index.get(i, {})
        status = got.get("status") or ("cancelled" if output is not None else "queued")
        agents.append({
            "i": i, "name": got.get("name") or base.name, "task": base.task,
            "mode": got.get("mode") or base.mode, "status": status,
            "activity": team_mod.STATUS_LABELS.get(status, status),
            "steps": got.get("steps", 0), "turns": 0, "tokens": 0, "tokens_out": 0,
            "started": None, "finished": None, "elapsed": None,
            "elapsed_label": got.get("elapsed_label", ""),
            "error": got.get("error", ""), "log": [], "files": got.get("files", []),
            "report": got.get("report", ""), "has_report": bool(got.get("report")),
        })
    counts = {"queued": 0, "running": 0, "done": 0, "failed": 0}
    for a in agents:
        counts["done" if a["status"] == "done" else "queued" if a["status"] == "queued"
               else "failed"] += 1
    m = re.search(r"ran in ([^—]+?) —", text)
    return {
        "id": tool_id, "goal": str((tool_input or {}).get("goal") or "") if isinstance(tool_input, dict) else "",
        "status": "done" if output is not None else "running", "created": None, "finished": None,
        "now": time.time(), "elapsed": None, "elapsed_label": m.group(1).strip() if m else "",
        "counts": counts, "total": len(agents), "steps": sum(a["steps"] for a in agents),
        "tokens": 0, "agents": agents, "restored": True,
    }
