"""One subagent's own model ↔ tool loop, on its own thread.

A subagent never touches ``state.messages``: it keeps a private conversation
(its brief → replies → tool results) and talks to the same client/model the
lead agent uses, captured when the team starts. Its tool calls run through
the normal dispatcher (``repl.render._run_tool`` — aliases, argument repair,
plan-mode guard), restricted to the tools its mode allows; their live events
go to the team board instead of the transcript (see ``tool_events``).
"""
from __future__ import annotations

import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .. import state
from . import context as ctx
from .team import AgentRun, Team

# Read-only tools every subagent gets; "edit" agents add the write set.
EXPLORE_TOOLS = (
    "read_file", "read_document", "list_dir", "glob_files", "rank_files", "fast_find",
    "search_code", "git_status", "git_diff", "git_log", "resolve_context", "read_bundle",
    "web_search", "fetch_url", "verified_search", "skill_list", "skill_load",
    "read_image_text", "read_images_text",
)
EDIT_TOOLS = ("write_file", "edit_file", "multi_edit", "run_bash")
_WRITE_TOOLS = frozenset({"write_file", "edit_file", "multi_edit"})

# Transient provider trouble: wait and try the same request again.
_RETRY_STATUS = {408, 409, 425, 429, 500, 502, 503, 504, 520, 522, 524, 529}
_RETRY_DELAYS = (2, 5, 12, 25)
REPORT_MAX_CHARS = 14_000


class _Stop(Exception):
    """The agent was stopped (user, Esc, time limit) — unwind quietly."""


@dataclass
class RequestConfig:
    """What every request of one team is sent with — fixed at spawn time, so
    switching model mid-run doesn't mix models inside one agent."""
    client: Any
    model: str
    provider: str
    auth_mode: str
    think_mode: bool
    think_effort: str
    max_turns: int
    deadline_s: float

    @classmethod
    def capture(cls, *, max_turns: int, deadline_s: float) -> "RequestConfig":
        return cls(
            client=state.client, model=state.MODEL, provider=state.provider,
            auth_mode=state.auth_mode, think_mode=bool(state.think_mode),
            think_effort=str(state.think_effort or "high"),
            max_turns=max_turns, deadline_s=deadline_s,
        )


# ── tools ────────────────────────────────────────────────────────────────

def allowed_tools(mode: str) -> set[str]:
    names = set(EXPLORE_TOOLS)
    if mode == "edit" and not state.plan_mode:
        names |= set(EDIT_TOOLS)
    return names


def tool_schemas(mode: str) -> list[dict]:
    from ..tools import TOOLS, FUNC
    from ..utils.schema import sanitize_tools

    allow = allowed_tools(mode)
    seen: set[str] = set()
    out = []
    for t in TOOLS:
        name = t.get("name")
        if name in allow and name in FUNC and name not in seen:
            seen.add(name)
            out.append(t)
    return sanitize_tools(out)


def _real_tool_name(name: str) -> str:
    from ..repl import render

    return (render._FREE_TIER_TOOL_ALIASES.get(name)
            or render._resolve_tool_alias(name) or name)


def _short(label: str) -> str:
    """Absolute paths in an activity label → the short ~/… form rows use."""
    try:
        from ..utils.display_paths import shorten_paths

        return shorten_paths(label)
    except Exception:
        return label


def _write_paths(name: str, tool_input: Any) -> list[str]:
    if not isinstance(tool_input, dict):
        return []
    if name == "multi_edit":
        return [str(e["path"]) for e in tool_input.get("edits") or []
                if isinstance(e, dict) and e.get("path")]
    p = tool_input.get("path") or tool_input.get("file_path")
    return [str(p)] if p else []


# ── prompts ──────────────────────────────────────────────────────────────

def _system_prompt(team: Team, agent: AgentRun, cfg: RequestConfig) -> Any:
    from ..constants import AUTH_OAUTH, OAUTH_IDENTITY
    from ..constants.system_prompt import build_base_system
    from ..repl.system import _OAUTH_IDENTITY_OVERRIDE, _get_git_branch
    from ..storage import pin as pin_store

    cwd = Path.cwd()
    try:
        body = build_base_system(cwd, git_branch=_get_git_branch(str(cwd)))
    except Exception:
        body = f"You are Jarvis, a coding agent. Working directory: {cwd}"
    try:
        pinned = pin_store.injection_text()
    except Exception:
        pinned = ""
    if pinned:
        body += "\n\nPINNED CONTEXT (user-supplied, always remember):\n" + pinned
    if state.project_context_file:
        body += (f"\n\nPROJECT CONTEXT: {state.project_context_file} holds this repository's "
                 "instructions — read it first when your task touches code conventions.")
    try:
        from ..storage.skills import as_prompt_block as skills_block

        sk = skills_block()
        if sk:
            body += "\n\n" + sk + "\nLoad a matching skill with skill_load('<name>')."
    except Exception:
        pass

    if agent.mode == "edit" and not state.plan_mode:
        scope = (
            "- You MAY change files, but only the ones your task is about. Other agents are "
            "editing other files at the same time: if a write is refused because another agent "
            "owns that file, leave it and describe the change it needs in your report.\n"
            "- Never run commands that rewrite git history or discard work (commit, push, "
            "reset, stash, checkout --, clean, rebase), never start servers or watchers that "
            "keep running, and never delete files outside your task.\n"
            "- If your task names a folder outside this project, the user asked for it: pass "
            "allow_outside_project=true to write_file / edit_file / multi_edit there.\n"
            "- Check your work (run the relevant tests / a syntax check) before reporting."
        )
    else:
        scope = (
            "- You are READ-ONLY: investigate and report. Do not try to change files or run "
            "shell commands — you don't have those tools."
        )
    body += (
        "\n\nSUBAGENT MODE\n"
        f"You are subagent \"{agent.name}\" ({agent.index + 1} of {len(team.agents)}), started by "
        "the lead Jarvis agent. The other subagents work on other parts of the same goal at "
        "the same time.\n"
        "- Do only YOUR task, completely. There is no user to talk to: never ask questions — "
        "make sensible assumptions and list them in your report.\n"
        "- Be fast: call independent tools together in one step, read only what you need, "
        "never re-read a file you already have.\n"
        f"{scope}\n"
        "- When done, reply with your final report and NO tool calls. The lead agent sees "
        "only this report, so make it self-contained:\n"
        "  ## Result — the answer / outcome in a few sentences\n"
        "  ## Details — findings with file paths and line numbers, or exactly what you changed\n"
        "  ## Open issues — anything unfinished, risky or needing a decision (or \"None\")\n"
        "  Keep it under ~700 words: facts, paths, names — no filler."
    )
    body += f"\n\nCURRENT DATE: {time.strftime('%A, %B %d, %Y')}"
    if cfg.auth_mode == AUTH_OAUTH:
        return [
            {"type": "text", "text": OAUTH_IDENTITY},
            {"type": "text", "text": _OAUTH_IDENTITY_OVERRIDE + body},
        ]
    return body


def brief_content(team: Team, agent: AgentRun) -> str | list:
    """The agent's first message: the user's images (if any) + its brief."""
    text = brief(team, agent)
    images = list(getattr(team, "images", None) or [])
    if not images:
        return text
    note = (f"\n\n(The {'image above is' if len(images) == 1 else f'{len(images)} images above are'} "
            "what the user attached to their request — use it as the reference.)")
    return [*images, {"type": "text", "text": text + note}]


def brief(team: Team, agent: AgentRun) -> str:
    lines = [f"You are subagent \"{agent.name}\" — {agent.index + 1} of {len(team.agents)} "
             "running in parallel."]
    if team.goal:
        lines += ["", "OVERALL GOAL (context only — do just your part):", team.goal]
    others = [a for a in team.agents if a is not agent]
    if others:
        lines += ["", "THE OTHER AGENTS (don't duplicate their work):"]
        for a in others:
            first = " ".join(a.task.split())[:160]
            lines.append(f"- \"{a.name}\": {first}")
    lines += ["", "YOUR TASK:", agent.task]
    return "\n".join(lines)


# ── one model request ────────────────────────────────────────────────────

def _request_kwargs(cfg: RequestConfig, system: Any, messages: list, tools: list,
                    target: float | None = None) -> dict:
    from anthropic import Anthropic

    from ..constants import (
        PROVIDER_ANTHROPIC, PROVIDER_OPENCODE, PROVIDER_OPENCODE_ZEN, PROVIDER_OPENROUTER,
    )
    from ..constants.models import API_MAX_TOKENS, THINKING_BUDGET_TOKENS
    from ..constants.providers import (
        claude_thinking_kwargs, claude_uses_adaptive_thinking, is_catalog_provider,
        model_supports_images,
    )
    from ..media import materialize_uploads
    from ..repl import context_budget, prompt_cache
    from ..repl.trim import anthropic_wire_messages, prune_tool_images

    vision = model_supports_images(cfg.model)
    fixed = context_budget.estimate_tokens([], system=system, tools=tools)
    limit = context_budget.input_limit(cfg.model)
    wire = list(messages)
    if target is not None or fixed + context_budget.estimate_tokens(wire) > limit * context_budget.TRIGGER:
        wire, _ = context_budget.fit_to_window(
            wire, limit=limit, fixed_tokens=fixed,
            target=target if target is not None else context_budget.TARGET)
    wire = prune_tool_images(wire, vision=vision)
    wire = materialize_uploads(wire, vision=vision)
    if cfg.provider in (PROVIDER_ANTHROPIC, PROVIDER_OPENROUTER) or (
        is_catalog_provider(cfg.provider) and isinstance(cfg.client, Anthropic)
    ):
        wire = anthropic_wire_messages(wire)
    kwargs: dict[str, Any] = dict(model=cfg.model, max_tokens=API_MAX_TOKENS, system=system,
                                  messages=wire, tools=tools)
    if prompt_cache.enabled_for(cfg.provider, cfg.model, cfg.client):
        prompt_cache.apply(kwargs)
    if cfg.provider == PROVIDER_ANTHROPIC and claude_uses_adaptive_thinking(cfg.model):
        kwargs.update(claude_thinking_kwargs(cfg.think_mode, cfg.think_effort))
    elif cfg.think_mode:
        kwargs["thinking"] = {"type": "enabled", "budget_tokens": THINKING_BUDGET_TOKENS}
        if cfg.provider in (PROVIDER_OPENCODE, PROVIDER_OPENCODE_ZEN):
            kwargs["thinking"]["effort"] = cfg.think_effort
    elif cfg.provider in (PROVIDER_OPENCODE, PROVIDER_OPENCODE_ZEN):
        kwargs["thinking"] = {"type": "disabled"}
    return kwargs


def _status_code(err: BaseException) -> int:
    for attr in ("status_code", "status"):
        v = getattr(err, attr, None)
        if isinstance(v, int):
            return v
    resp = getattr(err, "response", None)
    v = getattr(resp, "status_code", None)
    return v if isinstance(v, int) else 0


def _retryable(err: BaseException) -> bool:
    import httpx

    name = type(err).__name__
    if "RateLimit" in name or "Timeout" in name or "Overloaded" in name or "Connection" in name:
        return True
    if isinstance(err, (httpx.TimeoutException, httpx.NetworkError, httpx.RemoteProtocolError)):
        return True
    return _status_code(err) in _RETRY_STATUS


def _pause(team: Team, agent: AgentRun, seconds: float, why: str) -> None:
    end = time.monotonic() + seconds
    while True:
        left = end - time.monotonic()
        if left <= 0:
            return
        if team.stopped(agent):
            raise _Stop()
        team.set_activity(agent, f"{why} — retrying in {left:.0f}s")
        time.sleep(min(0.5, left))


def _call_model(team: Team, agent: AgentRun, cfg: RequestConfig, system: Any,
                messages: list, tools: list) -> Any:
    from ..repl import prompt_cache
    from ..repl.stream import _iter_live_deltas, check_tool_inputs, tool_input_label

    kwargs = _request_kwargs(cfg, system, messages, tools)
    cache_retried = thinking_retried = False
    attempt = 0
    while True:
        if team.stopped(agent):
            raise _Stop()
        team.set_activity(agent, "Thinking…")
        try:
            with cfg.client.messages.stream(**kwargs) as stream:
                with team.lock:
                    agent.stream = stream
                try:
                    chars = 0
                    tool_chars = 0
                    tool_name = ""
                    tool_head = ""  # start of the arguments: the path / command shows early
                    for kind, chunk in _iter_live_deltas(stream):
                        if team.stopped(agent):
                            raise _Stop()
                        if kind == "thinking":
                            team.set_activity(agent, "Thinking…")
                        elif kind == "text":
                            chars += len(chunk or "")
                            team.set_activity(agent, f"Writing… {chars:,} chars"
                                              if chars > 400 else "Writing…")
                        elif kind == "tool_input":
                            _key, name, delta = chunk
                            if name:
                                tool_name, tool_chars, tool_head = name, 0, ""
                            tool_chars += len(delta or "")
                            if len(tool_head) < 600:
                                tool_head += (delta or "")[:600]
                            if tool_name:
                                team.set_activity(agent, _short(tool_input_label(
                                    _real_tool_name(tool_name), tool_head, tool_chars)))
                    final = stream.get_final_message()
                finally:
                    with team.lock:
                        agent.stream = None
            check_tool_inputs(final)
            return final
        except _Stop:
            raise
        except BaseException as e:  # noqa: BLE001 — KeyboardInterrupt from a closed stream too
            if team.stopped(agent):
                raise _Stop() from None
            if isinstance(e, (KeyboardInterrupt, SystemExit)):
                raise
            code = _status_code(e)
            if code == 400 and not cache_retried and prompt_cache.has_markers(kwargs) \
                    and prompt_cache.refused_markers(e):
                cache_retried = True
                prompt_cache.disable(str(e)[:200])
                prompt_cache.strip(kwargs)
                continue
            if code == 400 and not thinking_retried and prompt_cache.thinking_binding_error(e):
                thinking_retried = True
                prompt_cache.drop_thinking_blocks(messages)
                kwargs = _request_kwargs(cfg, system, messages, tools)
                continue
            if _is_overflow(e) and attempt < 2:
                from ..repl import context_budget

                attempt += 1
                kwargs = _request_kwargs(cfg, system, messages, tools,
                                         target=context_budget.RETRY_TARGET / attempt)
                continue
            if _retryable(e) and attempt < len(_RETRY_DELAYS):
                why = "Rate limited" if code == 429 or "RateLimit" in type(e).__name__ else (
                    "Provider busy" if code in (503, 529) else "Connection trouble")
                _pause(team, agent, _RETRY_DELAYS[attempt], why)
                attempt += 1
                continue
            raise


def _is_overflow(err: BaseException) -> bool:
    try:
        from ..repl import context_budget

        return context_budget.is_context_overflow(err)
    except Exception:
        return False


# ── tools inside the agent ───────────────────────────────────────────────

def _run_tools(team: Team, agent: AgentRun, blocks: list, allow: set[str]) -> list[dict]:
    from ..repl.render import _cap_tool_output, _run_tool
    from ..utils.tool_images import tool_result_content

    results = []
    for b in blocks:
        if team.stopped(agent):
            out = "ERROR: not run — the agent was stopped"
        else:
            name = _real_tool_name(getattr(b, "name", "") or "")
            if name not in allow:
                hint = (" This agent is read-only — describe the change in your report instead."
                        if name in EDIT_TOOLS else "")
                out = f"ERROR: tool '{b.name}' isn't available to subagents.{hint}"
            else:
                out = _claim_check(team, agent, name, getattr(b, "input", None))
                if out is None:
                    with team.lock:
                        agent.steps += 1
                    _, _, _, out = _run_tool(b)
        results.append({
            "type": "tool_result",
            "tool_use_id": b.id,
            "content": tool_result_content(_cap_tool_output(getattr(b, "name", ""), str(out))),
        })
    return results


def _claim_check(team: Team, agent: AgentRun, name: str, tool_input: Any) -> str | None:
    if name not in _WRITE_TOOLS:
        return None
    for path in _write_paths(name, tool_input):
        owner = team.claim(agent, path)
        if owner is not None:
            team.log(agent, f"Skipped {path} — {owner.name} is editing it", kind="note",
                     status="error")
            return (f"ERROR: {path} is being changed by the parallel agent \"{owner.name}\". "
                    "Don't edit it — leave it to that agent and put the change it needs in "
                    "your final report (the lead agent will apply it).")
    return None


# ── the loop ─────────────────────────────────────────────────────────────

def _text_of(resp: Any) -> str:
    parts = []
    for b in getattr(resp, "content", None) or []:
        if getattr(b, "type", None) == "text" and (getattr(b, "text", "") or "").strip():
            parts.append(b.text.strip())
    return "\n\n".join(parts)


def _usage(resp: Any) -> tuple[int, int]:
    from ..repl import prompt_cache

    usage = getattr(resp, "usage", None)
    a, b, c = prompt_cache.usage_counts(usage)
    try:
        out = int(getattr(usage, "output_tokens", 0) or 0)
    except (TypeError, ValueError):
        out = 0
    return a + b + c, out


def run_agent(team: Team, agent: AgentRun, cfg: RequestConfig) -> None:
    """Run one subagent to completion. Never raises; the outcome lands on ``agent``."""
    me = threading.get_ident()
    with team.lock:
        agent.thread_id = me
        if agent.is_finished:  # stopped while still queued
            return
        agent.status = "running"
        agent.started = time.time()
        agent.activity = "Starting…"
    team.changed()
    state.link_thread(me, team.parent_thread)
    ctx.set_current(team, agent)
    if agent.stop_reason:  # stopped between the check above and linking
        state.cancel_thread(me)
    last_text = ""
    try:
        system = _system_prompt(team, agent, cfg)
        tools = tool_schemas(agent.mode)
        allow = {t["name"] for t in tools}
        messages: list[dict] = [{"role": "user", "content": brief_content(team, agent)}]
        t0 = time.monotonic()
        wrap_sent = False
        empty_retries = 0
        for turn in range(cfg.max_turns):
            if team.stopped(agent):
                raise _Stop()
            if time.monotonic() - t0 > cfg.deadline_s:
                agent.stop_reason = agent.stop_reason or "timeout"
                raise _Stop()
            resp = _call_model(team, agent, cfg, system, messages, tools)
            tin, tout = _usage(resp)
            with team.lock:
                agent.turns += 1
                agent.tokens_in += tin
                agent.tokens_out += tout
            text = _text_of(resp)
            if text:
                last_text = text
            uses = [b for b in getattr(resp, "content", None) or []
                    if getattr(b, "type", None) == "tool_use"]
            if not uses and not text and empty_retries < 1:
                empty_retries += 1
                continue
            messages.append({"role": "assistant", "content": resp.content})
            if not uses:
                if getattr(resp, "stop_reason", None) == "max_tokens" and turn + 1 < cfg.max_turns:
                    messages.append({"role": "user", "content":
                                     "Your report was cut off by the output limit. Continue "
                                     "exactly where it stopped (no repetition)."})
                    continue
                _finish(team, agent, "done", report=_joined_report(messages) or text)
                return
            results = _run_tools(team, agent, uses, allow)
            near_turns = turn + 2 >= cfg.max_turns
            near_time = time.monotonic() - t0 > cfg.deadline_s * 0.85
            if (near_turns or near_time) and not wrap_sent:
                wrap_sent = True
                why = "the step limit" if near_turns else "the time limit"
                results.append({"type": "text", "text":
                                f"[Jarvis] You're about to hit {why}. Stop calling tools now and "
                                "write your final report with what you have (mark anything "
                                "unfinished under Open issues)."})
            messages.append({"role": "user", "content": results})
            team.set_activity(agent, "Thinking…")
        # Out of steps without a final message.
        _finish(team, agent, "done", report=(last_text or "") + (
            "\n\n_(Stopped at the step limit before a final report — the notes above are "
            "partial.)_"))
    except _Stop:
        reason = agent.stop_reason or "cancelled"
        note = {"stopped": "Stopped by the user", "timeout": "Ran out of time",
                "cancelled": "Cancelled"}.get(reason, "Stopped")
        _finish(team, agent, reason, report=last_text, error=note)
    except KeyboardInterrupt:
        _finish(team, agent, agent.stop_reason or "cancelled", report=last_text, error="Cancelled")
    except Exception as e:  # noqa: BLE001
        msg = f"{type(e).__name__}: {e}"
        _finish(team, agent, "error", report=last_text, error=msg[:400])
    finally:
        ctx.clear_current()
        state.unlink_thread(me)
        state.release_thread(me)


def _joined_report(messages: list) -> str:
    """The final report, joining a reply continued after a max_tokens cut."""
    tail: list[str] = []
    for m in reversed(messages):
        if m.get("role") == "assistant":
            content = m.get("content")
            blocks = content if isinstance(content, list) else []
            if any(getattr(b, "type", None) == "tool_use" or
                   (isinstance(b, dict) and b.get("type") == "tool_use") for b in blocks):
                break
            texts = [getattr(b, "text", None) or (b.get("text") if isinstance(b, dict) else None)
                     for b in blocks if (getattr(b, "type", None) or
                                         (b.get("type") if isinstance(b, dict) else None)) == "text"]
            tail.insert(0, "".join(t for t in texts if t))
        elif isinstance(m.get("content"), list):
            break
    return "".join(tail).strip()


def _finish(team: Team, agent: AgentRun, status: str, *, report: str = "", error: str = "") -> None:
    report = (report or "").strip()
    if len(report) > REPORT_MAX_CHARS:
        report = report[:REPORT_MAX_CHARS].rstrip() + "\n\n… (report truncated)"
    with team.lock:
        agent.status = status
        agent.report = report
        agent.error = error
        agent.finished = time.time()
        agent.activity = {"done": "Finished", "error": "Failed"}.get(status, error or status)
        agent.stream = None
    team.changed()
