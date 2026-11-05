"""``ask_project``: hand a task to the Jarvis running in another workspace project.

The other project's own Jarvis does the work (its context, skills, approvals) in a
NEW chat tab, so the user's chat there is never disturbed. We talk to it only
through the web API the page itself uses (``web.instance_client``): open a tab,
send the task, poll the tab until it is idle, read the last assistant message.

Guardrails: off unless ``workspace.delegation`` is on; only projects the current
one is linked to; depth 1 (a delegated task can't delegate); a timeout that stops
*waiting*, never the work; Esc stops waiting too. Every failure is an ``ERROR: …``
string — the tool never raises.
"""
from __future__ import annotations

import os
import time
from typing import Any

from .. import state

PREFIX = "Delegated from project "     # first words of every delegated task: the depth guard reads them
DELEGATED_ENV = "HARNESS_DELEGATED"     # set on a Jarvis started for delegation (launcher env_extra)
START_CAP = 45.0                        # a cold project start (seconds)
POLL_SECS = 1.0
NOT_STARTED_SECS = 20.0                 # sent, but the tab never went busy / got a reply
MIN_TIMEOUT, MAX_TIMEOUT, DEFAULT_TIMEOUT = 5, 3600, 600
MAX_FILES_LISTED = 20

OFF_MESSAGE = ("ERROR: Delegation is off. Turn on Workspace settings → Delegation "
               "or `/project settings delegation true`")

DELEGATE_TOOLS = [{
    "name": "ask_project",
    "description": (
        "Hand a task to the Jarvis running in another LINKED workspace project and get its result. "
        "That project's own agent does the work (its own files, skills and approvals) in a new chat "
        "tab; use it when a change must happen in a different repo. Write the task so it stands "
        "alone — the other agent cannot see this conversation. Returns its final message and the "
        "files it changed. wait=false returns at once with the tab id. Delegated work cannot "
        "delegate again."
    ),
    "input_schema": {"type": "object", "properties": {
        "project": {"type": "string", "description": "Workspace project id (or exact name) — must be linked to this project."},
        "task": {"type": "string", "description": "Self-contained instructions for the other project's agent."},
        "wait": {"type": "boolean", "description": "Wait for it to finish (default true). false: start it and return."},
        "timeout": {"type": "integer", "description": "Seconds to wait (default 600). On timeout the work keeps running there."},
    }, "required": ["project", "task"]},
}]


def _err(msg: str) -> str:
    return f"ERROR: {msg}"


def _workspace():
    from ..storage import workspace

    return workspace


def exposed() -> bool:
    """Show the tool to the model: delegation is on and this project has someone to ask."""
    try:
        ws = _workspace()
        data = ws.load()
        if not data["settings"].get("delegation"):
            return False
        here = ws.current()
        return here is not None and bool(ws.links_of(here, data))
    except Exception:
        return False


def _as_bool(value: Any, default: bool = True) -> bool:
    if isinstance(value, str):
        return value.strip().lower() not in ("false", "0", "no", "off", "")
    return default if value is None else bool(value)


def _as_timeout(value: Any) -> int:
    try:
        secs = int(value)
    except (TypeError, ValueError):
        secs = DEFAULT_TIMEOUT
    return max(MIN_TIMEOUT, min(MAX_TIMEOUT, secs))


def _first_user_text() -> str:
    for msg in state.messages or []:
        if isinstance(msg, dict) and msg.get("role") == "user":
            content = msg.get("content")
            if isinstance(content, list):
                content = " ".join(str(b.get("text") or "") for b in content if isinstance(b, dict))
            return str(content or "").strip()
    return ""


def is_delegated() -> bool:
    """This Jarvis was started for delegation, or this chat is a delegated task."""
    return os.environ.get(DELEGATED_ENV) == "1" or _first_user_text().startswith(PREFIX)


def _pick_target(ref: str, here: dict[str, Any], data: dict[str, Any]) -> tuple[dict[str, Any] | None, str]:
    """``(project, "")`` or ``(None, error)`` — a workspace project this one is linked to."""
    ws = _workspace()
    ref = (ref or "").strip()
    if not ref:
        return None, _err("`project` is empty. Pass a linked project's id.")
    proj = ws.get(ref, data)
    if proj is None:
        low = ref.lower()
        proj = next((p for p in ws.projects(data) if str(p.get("name") or "").lower() == low), None)
    linked = ws.links_of(here, data)
    names = ", ".join(p["id"] for p in linked) or "none"
    if proj is None:
        return None, _err(f"No workspace project '{ref}'. Linked to {here['id']}: {names}.")
    if proj["id"] == here["id"]:
        return None, _err("That is this project — edit its files directly.")
    if proj["id"] not in {p["id"] for p in linked}:
        return None, _err(f"'{proj['id']}' is not linked to {here['id']}, so I won't send it work. "
                          f"Link it in Workspace → {here['id']} → Links. Linked: {names}.")
    return proj, ""


def _instance_for(path: str) -> dict[str, Any] | None:
    from ..web import registry

    real = os.path.realpath(path)
    rows = [r for r in registry.list_instances()
            if r.get("cwd") and os.path.realpath(str(r["cwd"])) == real]
    rows.sort(key=lambda r: (r.get("session_id") is None, bool(r.get("busy"))))
    return rows[0] if rows else None


def _cancelled() -> bool:
    try:
        return bool(state.turn_cancelled())
    except Exception:
        return False


def _ensure_running(proj: dict[str, Any]) -> tuple[str, str]:
    """``(instance_id, "")`` of the project's Jarvis, starting one if none runs; else ``("", error)``."""
    found = _instance_for(proj["path"])
    if found is not None:
        return str(found["id"]), ""
    from ..web import launcher

    res = launcher.open_project(proj["path"], reuse=True, env_extra={DELEGATED_ENV: "1"})
    if not res.get("ok"):
        return "", _err(f"Couldn't start {proj['id']}: {res.get('error') or 'unknown reason'}")
    launch = res.get("launch") or {}
    if launch.get("status") == "ready" and launch.get("instance_id"):
        return str(launch["instance_id"]), ""
    lid = str(launch.get("id") or "")
    deadline = time.monotonic() + START_CAP
    while time.monotonic() < deadline:
        if _cancelled():
            return "", _err(f"Stopped while {proj['id']} was starting.")
        got = launcher.launch_status(lid) if lid else None
        if got is None:
            return "", _err(f"Lost track of {proj['id']}'s start.")
        if got.get("status") == "ready" and got.get("instance_id"):
            return str(got["instance_id"]), ""
        if got.get("status") == "failed":
            return "", _err(f"{proj['id']} didn't start: {got.get('error') or 'unknown reason'}")
        time.sleep(min(0.3, POLL_SECS))
    return "", _err(f"{proj['id']} didn't start within {int(START_CAP)} seconds.")


def _tab_row(snap: dict[str, Any], tab_id: int) -> dict[str, Any] | None:
    for row in snap.get("tabs") or []:
        if isinstance(row, dict) and row.get("id") == tab_id:
            return row
    return None


def _open_tab(iid: str) -> tuple[int | None, int, str]:
    """``(tab id, messages already in it, error)`` — a new tab in that project."""
    from ..web import instance_client as ic

    before = ic.get_json(iid, "/api/state")
    known = {r.get("id") for r in before.get("tabs") or [] if isinstance(r, dict)}
    res = ic.post_json(iid, "/api/tabs", {"op": "new"})
    if not res.get("ok"):
        return None, 0, str(res.get("error") or "couldn't open a new chat tab")
    rows = [r for r in res.get("tabs") or [] if isinstance(r, dict)]
    fresh = [r for r in rows if r.get("id") not in known] or [r for r in rows if r.get("active")]
    if not fresh:
        return None, 0, "the new tab didn't show up"
    row = fresh[-1]
    return int(row["id"]), int(row.get("messages") or 0), ""


def _last_reply(snap: dict[str, Any]) -> str:
    for entry in reversed(snap.get("messages") or []):
        if isinstance(entry, dict) and entry.get("role") == "assistant" and str(entry.get("text") or "").strip():
            return str(entry["text"]).strip()
    return ""


def _reply_from_session(iid: str, session_id: Any) -> str:
    """The tab isn't the one shown there: the tail of its saved transcript."""
    from ..web import instance_client as ic

    try:
        sid = int(session_id or 0)
        if not sid:
            return ""
        res = ic.get_json(iid, f"/api/sessions/export?id={sid}")
    except Exception:
        return ""
    md = str(res.get("markdown") or "").strip()
    return md[-3000:] if md else ""


def _changed_files(snap: dict[str, Any]) -> list[str]:
    files = ((snap.get("changes") or {}).get("files")) or []
    out: list[str] = []
    for f in files:
        if isinstance(f, dict):
            name = str(f.get("path") or f.get("name") or "")
            if name:
                out.append(f"{name} ({f['status']})" if f.get("status") else name)
    return out


def _needs_approval(iid: str) -> bool:
    from ..web import registry

    try:
        rec = registry.get(iid)
    except Exception:
        return False
    return bool(rec and rec.get("needs_approval"))


def _still_running(name: str, tab_id: int, why: str, approval: bool) -> str:
    note = f" Waiting for your approval in {name}." if approval else ""
    return (f"STILL RUNNING in {name} tab {tab_id} ({why}) — check it there.{note} "
            "It was not stopped; don't re-send the task.")


def _wait(iid: str, proj_name: str, tab_id: int, base_msgs: int, timeout: int) -> str:
    from ..web import instance_client as ic

    start = time.monotonic()
    deadline = start + timeout
    seen_busy = False
    asked = False
    snap: dict[str, Any] = {}
    while True:
        if _cancelled():
            return _still_running(proj_name, tab_id, "you stopped waiting", asked)
        try:
            snap = ic.get_json(iid, "/api/state")
        except ic.InstanceError as exc:
            return _err(f"{proj_name} stopped answering while the task ran: {exc} "
                        f"Its tab {tab_id} may still hold partial work.")
        row = _tab_row(snap, tab_id)
        if row is None:
            return _err(f"The delegated tab in {proj_name} was closed before it finished.")
        busy = bool(row.get("busy"))
        seen_busy = seen_busy or busy
        if _needs_approval(iid):
            asked = True
        if not busy and (seen_busy or int(row.get("messages") or 0) > base_msgs):
            break
        now = time.monotonic()
        if not seen_busy and now - start > NOT_STARTED_SECS:
            return _still_running(proj_name, tab_id, "the task hasn't started yet", asked)
        if now >= deadline:
            return _still_running(proj_name, tab_id, f"no answer after {timeout}s", _needs_approval(iid))
        time.sleep(POLL_SECS)

    if row.get("active"):
        reply = _last_reply(snap)
        files = _changed_files(snap)
    else:  # the user moved to another tab there: only the saved transcript is reachable
        reply, files = _reply_from_session(iid, row.get("session_id")), []
    out = [f"Finished in {proj_name} (tab {tab_id}).", reply or "(it produced no final message)"]
    if files:
        shown = files[:MAX_FILES_LISTED]
        more = f" … +{len(files) - len(shown)} more" if len(files) > len(shown) else ""
        out.append(f"Files changed in {proj_name}: " + ", ".join(shown) + more)
    if asked:
        out.append(f"(It waited for your approval in {proj_name} during the task.)")
    return "\n".join(out)


def ask_project(project: str = "", task: str = "", wait: Any = True, timeout: Any = DEFAULT_TIMEOUT, **_: Any) -> str:
    """Send ``task`` to the Jarvis in linked project ``project``. Never raises."""
    try:
        return _ask_project(project, task, wait, timeout)
    except Exception as exc:  # the tool layer must always get a string
        return _err(f"ask_project failed: {exc}")


def _ask_project(project: str, task: str, wait: Any, timeout: Any) -> str:
    from ..web import instance_client as ic

    ws = _workspace()
    data = ws.load()
    if not data["settings"].get("delegation"):
        return OFF_MESSAGE
    if is_delegated():
        return _err("This chat is itself delegated work, and delegated work can't delegate again. "
                    "Finish it here, or report back what the other project needs.")
    task = str(task or "").strip()
    if not task:
        return _err("`task` is empty. Say what the other project should do.")
    here = ws.current()
    if here is None:
        return _err("This folder isn't a workspace project, so it has no linked projects to ask.")
    proj, problem = _pick_target(project, here, data)
    if proj is None:
        return problem

    name = proj["id"]
    try:
        iid, problem = _ensure_running(proj)
        if problem:
            return problem
        tab_id, base_msgs, problem = _open_tab(iid)
        if tab_id is None:
            return _err(f"Couldn't open a tab in {name}: {problem}.")
        sent = ic.post_json(iid, "/api/prompt", {"text": f"{PREFIX}{here['id']}: {task}"})
        if not sent.get("ok"):
            return _err(f"{name} didn't take the task: {sent.get('error') or 'rejected'}. "
                        f"Its empty tab {tab_id} is still open.")
    except ic.InstanceError as exc:
        return _err(f"{name}: {exc}")

    if not _as_bool(wait):
        return (f"Started in {name} tab {tab_id}. It is working there; the user can watch or stop it. "
                "Not waiting — don't re-send the task.")
    try:
        return _wait(iid, name, tab_id, base_msgs, _as_timeout(timeout))
    except ic.InstanceError as exc:
        return _err(f"{name}: {exc}")
