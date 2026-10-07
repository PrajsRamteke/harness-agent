"""The live board for a ``spawn_agents`` call — one row per parallel subagent.

    ⠹ Parallel agents  3 running · 1 done                         0:42
      ━━━━━━━━━━━━━━━──────────────────────────────  1/4
      ├ ⠋ Auth flow     Read jarvis/auth/client.py      7 tools   0:41
      ├ ✓ Web routes    Finished · +12 −3 in 2 files    9 tools   0:33
      ├ ✗ Tests audit   Rate limited — retrying in 5s   3 tools   0:12
      └ ◌ Docs          Queued
      ⎿ click for briefs and reports

It is a ``ToolBlock`` (same row lifecycle — running / done / cancelled, the
finish flash, click) whose body is drawn from the team snapshot that
``subagents.team.publish`` sends through ``TUIConsole.subagents_update``.
Clicking expands each agent: its brief, its latest steps and its report.
"""
from __future__ import annotations

import time
from typing import Any

from rich.text import Text

from . import theme as ui
from . import tool_format as tf
from .transcript import ToolBlock, wrap_lines

_STATUS_GLYPH = {
    "queued": "◌", "done": "✓", "error": "✗", "stopped": "■",
    "cancelled": "⊘", "timeout": "◷",
}
_REPORT_LINES = 14
_LOG_LINES = 4


# One fixed hue per agent (the web card uses the same six): theme accents
# can coincide (two reds in "red"), and agents must stay tellable apart.
AGENT_HUES = ("#60a5fa", "#c084fc", "#34d399", "#fbbf24", "#f472b6", "#22d3ee")


def agent_color(i: int) -> str:
    return AGENT_HUES[i % len(AGENT_HUES)]


def fmt_clock(secs: float | None) -> str:
    if secs is None:
        return ""
    s = int(max(0, secs))
    m, s = divmod(s, 60)
    if m >= 60:
        h, m = divmod(m, 60)
        return f"{h}:{m:02d}:{s:02d}"
    return f"{m}:{s:02d}"


def make_tool_block(tool_id: str, name: str, tool_input: Any = None) -> ToolBlock:
    """The transcript row for a tool call: the agents board for spawn_agents."""
    if name == "spawn_agents":
        return AgentsBlock(tool_id, name, tool_input)
    return ToolBlock(tool_id, name, tool_input)


def _report_lines(report: str, width: int) -> list[Text]:
    """A report as rendered markdown lines (same renderer as replies)."""
    try:
        from .md_render import render_markdown

        rendered = render_markdown(report, width)
        return rendered.split("\n") if rendered.plain else []
    except Exception:
        return [Text(ln, style=ui.FG_MUTE) for ln in report.splitlines()]


class AgentsBlock(ToolBlock):
    def __init__(self, tool_id: str, name: str, tool_input: Any = None) -> None:
        super().__init__(tool_id, name, tool_input)
        self.board: dict | None = None
        self._report_cache: dict[tuple, list[Text]] = {}

    # ── data ─────────────────────────────────────────────────────────
    def set_board(self, board: dict | None) -> None:
        if board:
            self.board = board
            self.touch()

    def finish(self, output: str, *, error: bool | None = None, repaired: bool = False) -> None:
        if self.board is None or self.board.get("status") != "done":
            try:
                from ..subagents import board_for

                self.board = board_for(self.tool_id, self.tool_input, output) or self.board
            except Exception:
                pass
        super().finish(output, error=error, repaired=repaired)

    def on_click(self) -> None:
        self.expanded = not self.expanded
        self.touch()

    def _agents(self) -> list[dict]:
        if self.board:
            return list(self.board.get("agents") or [])
        raw = self.tool_input.get("agents") if isinstance(self.tool_input, dict) else None
        out = []
        for i, a in enumerate(raw if isinstance(raw, list) else []):
            a = a if isinstance(a, dict) else {}
            out.append({"i": i, "name": str(a.get("name") or f"Agent {i + 1}"),
                        "task": str(a.get("task") or ""), "status": "queued",
                        "activity": "Starting…", "steps": 0, "log": [], "files": []})
        return out

    def _agent_status(self, a: dict) -> str:
        st = str(a.get("status") or "queued")
        if self.status in ("cancelled", "done", "error") and st in ("queued", "running"):
            return "cancelled"
        return st

    # ── paint ────────────────────────────────────────────────────────
    def plain_text(self) -> str:
        lines = [f"{tf.tool_title(self.tool_name)} {tf.tool_args(self.tool_name, self.tool_input)}"]
        for a in self._agents():
            lines.append(f"  {a.get('name')}: {self._agent_status(a)} — {a.get('activity') or ''}")
        return "\n".join(lines)

    def _counts_text(self, agents: list[dict]) -> Text:
        n = {"running": 0, "done": 0, "queued": 0, "failed": 0}
        for a in agents:
            st = self._agent_status(a)
            key = st if st in ("running", "done", "queued") else "failed"
            n[key] += 1
        t = Text()
        parts = []
        if n["running"]:
            parts.append((f"{n['running']} running", ui.ACCENT))
        if n["queued"]:
            parts.append((f"{n['queued']} queued", ui.FG_DIM))
        if n["done"]:
            parts.append((f"{n['done']} done", ui.OK))
        if n["failed"]:
            parts.append((f"{n['failed']} not finished", ui.WARN))
        for k, (label, style) in enumerate(parts):
            if k:
                t.append(" · ", style=ui.FG_DIM)
            t.append(label, style=style)
        return t

    def _elapsed(self, a: dict) -> str:
        started = a.get("started")
        if started:
            end = a.get("finished") or (time.time() if self._agent_status(a) == "running" else None)
            if end is None:
                return fmt_clock(a.get("elapsed"))
            return fmt_clock(end - started)
        if a.get("elapsed") is not None:
            return fmt_clock(a.get("elapsed"))
        return str(a.get("elapsed_label") or "")

    def _team_elapsed(self, agents: list[dict]) -> str:
        if self.board and self.board.get("elapsed_label"):
            return str(self.board["elapsed_label"])
        starts = [a["started"] for a in agents if a.get("started")]
        if not starts:
            return fmt_clock(time.monotonic() - self.t0) if self.status == "running" else ""
        live = self.status == "running"
        ends = [a.get("finished") or (time.time() if live else a.get("finished") or 0)
                for a in agents if a.get("started")]
        ends = [e for e in ends if e]
        return fmt_clock((max(ends) if ends else time.time()) - min(starts))

    def _activity(self, a: dict, st: str) -> tuple[str, str]:
        if st == "done":
            files = a.get("files") or []
            if files:
                add = sum(int(f.get("added") or 0) for f in files)
                rem = sum(int(f.get("removed") or 0) for f in files)
                return (f"Finished · +{add} −{rem} in {len(files)} file"
                        f"{'' if len(files) == 1 else 's'}", ui.FG_MUTE)
            return ("Finished · report ready" if a.get("has_report") or a.get("report")
                    else "Finished", ui.FG_MUTE)
        if st == "running":
            return (str(a.get("activity") or "Working…"), ui.FG)
        if st == "queued":
            return ("Queued — waits for a free slot", ui.FG_DIM)
        label = {"error": "Failed", "stopped": "Stopped", "cancelled": "Cancelled",
                 "timeout": "Ran out of time"}.get(st, st)
        err = str(a.get("error") or "")
        if err and err.lower() != label.lower():
            # "Stopped by the user" already says "Stopped"
            same_word = err.lower().startswith(label.lower().split(" ")[0])
            label = err if same_word else f"{label} · {err}"
        return (label, ui.ERR if st == "error" else ui.WARN if st in ("stopped", "timeout")
                else ui.FG_DIM)

    def _status_glyph(self, a: dict, st: str) -> Text:
        if st == "running":
            frames = ui.SPINNER_FRAMES
            return Text(frames[(self._frame + 3 * int(a.get("i") or 0)) % len(frames)],
                        style=agent_color(int(a.get("i") or 0)))
        style = {"done": ui.OK, "error": ui.ERR, "stopped": ui.WARN, "timeout": ui.WARN}.get(st, ui.FG_DIM)
        return Text(_STATUS_GLYPH.get(st, "•"), style=f"bold {style}")

    def body_lines(self, width: int) -> list[Text]:
        agents = self._agents()
        total = len(agents)
        lines: list[Text] = []

        # headline: title · counts · clock
        head = Text()
        head.append(tf.tool_title(self.tool_name), style=f"bold {ui.FG}")
        head.append("  ")
        head.append_text(self._counts_text(agents))
        clock = self._team_elapsed(agents)
        if clock:
            pad = width - head.cell_len - len(clock)
            if pad >= 2:
                head.append(" " * pad)
                head.append(clock, style=ui.FG_DIM)
        lines.extend(wrap_lines(head, width))

        goal = str((self.board or {}).get("goal") or
                   (self.tool_input.get("goal") if isinstance(self.tool_input, dict) else "") or "")
        if goal and self.expanded:
            lines.append(Text(tf.clip("goal: " + " ".join(goal.split()), width), style=ui.FG_DIM))

        # progress bar
        finished = sum(1 for a in agents if self._agent_status(a) not in ("running", "queued"))
        if total:
            label = f"  {finished}/{total}"
            bar_w = max(6, min(48, width - len(label) - 1))
            filled = round(bar_w * finished / total)
            bar = Text()
            bar.append("━" * filled, style=ui.OK if finished == total else ui.ACCENT)
            running = sum(1 for a in agents if self._agent_status(a) == "running")
            run_w = min(bar_w - filled, round(bar_w * running / total))
            if run_w:
                # running share shimmers gently
                k = (self._frame % 20) / 20
                bar.append("━" * run_w, style=ui.blend(ui.SEP, ui.ACCENT, 0.35 + 0.25 * abs(1 - 2 * k)))
            bar.append("─" * (bar_w - filled - run_w), style=ui.SEP)
            bar.append(label, style=ui.FG_DIM)
            lines.append(bar)

        if not agents:
            return lines

        name_w = min(20, max(6, max(len(str(a.get("name") or "")) for a in agents)))
        for n, a in enumerate(agents):
            last = n == total - 1
            st = self._agent_status(a)
            i = int(a.get("i") or n)
            branch = "└ " if last else "├ "
            row = Text(branch, style=ui.FG_DIM)
            row.append_text(self._status_glyph(a, st))
            row.append(" ")
            name = tf.clip(str(a.get("name") or f"Agent {n + 1}"), name_w)
            row.append(name.ljust(name_w), style=f"bold {agent_color(i)}")
            if a.get("mode") == "edit":
                row.append(" ✎", style=ui.FG_DIM)
            else:
                row.append("  ")
            row.append(" ")
            steps = int(a.get("steps") or 0)
            right = f"{steps} tool{'' if steps == 1 else 's'}" if steps or st != "queued" else ""
            el = self._elapsed(a)
            right_txt = f"{right:>8}  {el:>5}" if (right or el) else ""
            act, act_style = self._activity(a, st)
            room = width - row.cell_len - len(right_txt) - 2
            if room >= 8:
                act_clip = tf.clip(act, room)
                row.append(act_clip, style=act_style)
                row.append(" " * max(1, width - row.cell_len - len(right_txt)))
                row.append(right_txt, style=ui.FG_DIM)
            else:
                row.append(tf.clip(act, max(4, width - row.cell_len)), style=act_style)
            lines.append(row)
            if self.expanded:
                lines.extend(self._detail_lines(a, st, width, last))

        if self.status != "running" or self.expanded:
            hint = Text("⎿  ", style=ui.FG_DIM)
            if self.expanded:
                hint.append("click to collapse", style=ui.FG_DIM)
            else:
                summary = (self.summary or [""])[0]
                if summary:
                    hint.append(tf.clip(summary, max(8, width - 30)),
                                style=ui.ERR if self.status == "error" else ui.FG_MUTE)
                    hint.append("  ·  ", style=ui.FG_DIM)
                hint.append("click for briefs and reports", style=ui.FG_DIM)
            lines.append(hint)
        elif self._hover:
            lines.append(Text("⎿  click to see what each agent is doing", style=ui.FG_DIM))
        return lines

    def _detail_lines(self, a: dict, st: str, width: int, last: bool) -> list[Text]:
        rail = "    " if last else "│   "
        inner = max(10, width - len(rail))
        out: list[Text] = []

        def add(text: str | Text, style: str = ui.FG_MUTE, *, prefix: str = "") -> None:
            body = text if isinstance(text, Text) else Text(text, style=style)
            if prefix:
                body = Text(prefix, style=ui.FG_DIM) + body
            for ln in wrap_lines(body, inner):
                out.append(Text(rail, style=ui.FG_DIM) + ln)

        task = " ".join(str(a.get("task") or "").split())
        if task:
            add(tf.clip(task, inner * 2), ui.FG_DIM, prefix="brief  ")
        log = list(a.get("log") or [])[-_LOG_LINES:]
        if log and st == "running":
            for e in log:
                mark = {"running": "⋯", "done": "✓", "error": "✗"}.get(e.get("status"), "·")
                mark_style = {"done": ui.OK, "error": ui.ERR}.get(e.get("status"), ui.FG_DIM)
                row = Text(f"{mark} ", style=mark_style)
                row.append(tf.clip(str(e.get("label") or ""), inner - 2), style=ui.FG_MUTE)
                add(row)
        files = a.get("files") or []
        if files:
            row = Text("files  ", style=ui.FG_DIM)
            for k, f in enumerate(files[:6]):
                if k:
                    row.append(", ", style=ui.FG_DIM)
                row.append(tf.short_path(str(f.get("path") or "")), style=ui.FG_MUTE)
                row.append(f" +{f.get('added', 0)}", style=ui.OK)
                row.append(f" −{f.get('removed', 0)}", style=ui.ERR)
            if len(files) > 6:
                row.append(f" +{len(files) - 6} more", style=ui.FG_DIM)
            add(row)
        report = str(a.get("report") or "").strip()
        if report:
            key = (int(a.get("i") or 0), inner, ui.active_theme(), len(report))
            rlines = self._report_cache.get(key)
            if rlines is None:
                rlines = [ln for ln in _report_lines(report, inner) if ln.plain.strip()]
                self._report_cache[key] = rlines
            for ln in rlines[:_REPORT_LINES]:
                out.append(Text(rail, style=ui.FG_DIM) + ln)
            if len(rlines) > _REPORT_LINES:
                add(f"… +{len(rlines) - _REPORT_LINES} more lines (the lead agent read it all)",
                    ui.FG_DIM)
        elif st not in ("running", "queued"):
            add("(no report)", ui.FG_DIM)
        out.append(Text(rail.rstrip() or " ", style=ui.FG_DIM))
        return out

    def tick(self, frame: int) -> None:
        if self.status == "running":
            self._frame = frame
            self.touch(layout=False)
            return
        super().tick(frame)
