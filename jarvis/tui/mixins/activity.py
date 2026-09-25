"""Live activity feedback for the Jarvis TUI.

One timer (~24 fps, only while a turn runs) drives everything that moves:

* drains streamed reply / thinking deltas into the transcript (``pump``),
* the activity line above the composer — a breathing star, the current
  phase with a light shimmer sweeping across it, elapsed time and a rough
  token count,
* spinners on running tool rows and slash-command blocks.

Mixed into ``JarvisTUI``.
"""
from __future__ import annotations

import re
import time

from rich.text import Text
from textual.widget import Widget

from .. import theme as ui


# Internal phase labels from the agent loop → what the user should read.
_PHASE_MAP = (
    (re.compile(r"^jarvis:\s*building request", re.I), "Preparing"),
    (re.compile(r"^jarvis:\s*api waiting", re.I), "Waiting for model"),
    (re.compile(r"^waiting for model", re.I), "Waiting for model"),
    (re.compile(r"^jarvis:\s*applying model output", re.I), "Working"),
    (re.compile(r"^jarvis:\s*finalizing", re.I), "Finishing"),
    (re.compile(r"^jarvis:\s*buffering full reply", re.I), "Receiving reply"),
)


def clean_phase(label: str) -> str:
    """Friendly activity text: drop ``Jarvis:`` prefixes and trailing dots."""
    raw = (label or "").strip()
    if not raw:
        return ""
    for rx, friendly in _PHASE_MAP:
        if rx.search(raw):
            return friendly
    raw = re.sub(r"^jarvis:\s*", "", raw, flags=re.I)
    raw = raw.rstrip(".… ")
    from ...utils.display_paths import shorten_paths

    raw = shorten_paths(raw)
    return raw[:1].upper() + raw[1:] if raw else ""


def _fmt_tokens(n: int) -> str:
    if n >= 1000:
        return f"{n / 1000:.1f}k"
    return str(n)


def _fmt_elapsed(s: float) -> str:
    if s < 60:
        return f"{int(s)}s"
    m, sec = divmod(int(s), 60)
    return f"{m}m {sec:02d}s"


class ActivityLine(Widget):
    """``✻ Reading stream.py… (12s · ↓ 1.2k tokens · esc to interrupt)``

    Right side: ``↓ 42 lines below`` when you've scrolled up (click to jump
    back to the live end).
    """

    DEFAULT_CSS = """
    ActivityLine {
        height: 1;
        width: 1fr;
    }
    """

    can_focus = False

    def on_click(self) -> None:
        try:
            self.app.query_one("#transcript").follow()
        except Exception:
            pass

    def _left(self) -> Text:
        app = self.app
        out = Text(no_wrap=True, overflow="ellipsis")
        label = getattr(app, "_activity_label", "")
        if getattr(app, "_busy", False) and label:
            from ..transcript import shimmer

            frame = getattr(app, "_frame", 0)
            star = ui.PULSE_FRAMES[(frame // 2) % len(ui.PULSE_FRAMES)]
            out.append(star, style=f"bold {ui.ACCENT}")
            out.append(" ")
            text = f"{label}…"
            out.append_text(shimmer(text, ui.ACCENT, ui.blend(ui.ACCENT, "#ffffff", 0.65),
                                    speed=20.0, width=5.0))
            meta: list[str] = []
            t0 = getattr(app, "_turn_t0", 0.0) or getattr(app, "_activity_t0", 0.0)
            if t0:
                meta.append(_fmt_elapsed(max(0.0, time.monotonic() - t0)))
            con = getattr(app, "_tui_console", None)
            chars = getattr(con, "stream_chars", 0) if con is not None else 0
            narrow = (self.size.width or 100) < len(text) + 60
            if chars:
                meta.append(f"↓ {_fmt_tokens(chars // 4)}" + ("" if narrow else " tokens"))
            meta.append("esc" if narrow else "esc to interrupt")
            out.append(f"  ({' · '.join(meta)})", style=ui.FG_DIM)
            return out
        loop_txt = self._loop_status()
        if loop_txt:
            out.append("  ")
            out.append("⟳ ", style=ui.ACCENT_3)
            out.append(loop_txt, style=ui.FG_DIM)
            return out
        msg = self._idle_status()
        if msg:
            out.append("  ")
            out.append(msg, style=ui.FG_DIM)
        return out

    def _loop_status(self) -> str:
        fn = getattr(self.app, "_loop_status_text", None)
        try:
            return fn() if callable(fn) and not getattr(self.app, "_busy", False) else ""
        except Exception:
            return ""

    def _idle_status(self) -> str:
        msg = getattr(self.app, "_status_msg", "") or ""
        return "" if msg.strip().lower() in ("", "ready", "thinking…", "processing…") else msg

    def _lines_below(self) -> int:
        try:
            return self.app.query_one("#transcript").more_below
        except Exception:
            return 0

    @property
    def wanted(self) -> bool:
        """Anything to show? When not, the row and its gap collapse."""
        app = self.app
        return bool(
            (getattr(app, "_busy", False) and getattr(app, "_activity_label", ""))
            or self._idle_status()
            or self._loop_status()
            or self._lines_below() > 2
        )

    def _right(self) -> Text:
        below = self._lines_below()
        if below <= 2:
            return Text("")
        out = Text()
        out.append("↓ ", style=f"bold {ui.ACCENT}")
        out.append(f"{below} lines below", style=ui.FG_MUTE)
        out.append(" · end", style=ui.FG_DIM)
        return out

    def render(self) -> Text:
        left, right = self._left(), self._right()
        width = self.size.width or 100
        if not right.plain:
            return left
        room = width - right.cell_len - 2
        if left.cell_len > room:
            left.truncate(max(0, room), overflow="ellipsis")
        line = Text(no_wrap=True)
        line.append_text(left)
        line.append(" " * max(1, width - left.cell_len - right.cell_len))
        line.append_text(right)
        return line


class ActivityMixin:
    """Spinner + streaming pump behaviour for ``JarvisTUI``."""

    # Legacy parallel-files dock hooks — tool rows in the transcript replace it.
    def reset_tool_activity_panel(self) -> None:
        return None

    def _refresh_tool_dock(self) -> None:
        return None

    # ── activity phase ─────────────────────────────────────────────
    def _sync_activity_phase(self, label: str) -> None:
        label = clean_phase(label)
        if label != self._activity_label:
            self._activity_t0 = time.monotonic()
            self._activity_spinner_i = 0
        self._activity_label = label
        self._refresh_activity_widgets()

    def _refresh_activity_widgets(self) -> None:
        sticky = getattr(self, "_sync_sticky_prompt", None)
        if callable(sticky):
            sticky()
        try:
            line = self.query_one("#activity", ActivityLine)
        except Exception:
            return
        line.set_class(not line.wanted, "-idle")
        line.refresh()

    async def _tick_activity(self) -> None:
        self._frame += 1
        self._activity_spinner_i = self._frame
        con = getattr(self, "_tui_console", None)
        if con is not None:
            try:
                await con.pump()
            except Exception:
                pass
            for blk in con.running_tool_blocks():
                blk.tick(self._frame)
            cmd = getattr(con, "_cmd_block", None)
            if cmd is not None:
                cmd.tick(self._frame)
        self._refresh_activity_widgets()
        self._breathe_composer()
        if self._frame % 12 == 0:
            self._render_footer()

    def _breathe_composer(self) -> None:
        """Composer bar gently pulses between the agent color and dim while busy."""
        try:
            import math

            from ..app import _agent_color

            k = (math.sin(time.monotonic() * 3.2) + 1) / 2
            color = ui.blend(_agent_color(), ui.FG_DIM, 0.15 + 0.55 * k)
            self.query_one("#composer").styles.border_left = ("outer", color)
        except Exception:
            pass

    # Back-compat name used by older call sites.
    def _tick_activity_spinner(self) -> None:
        self.call_later(self._tick_activity)

    def _start_activity_pulse(self) -> None:
        self._stop_activity_pulse()
        self._activity_timer = self.set_interval(1 / 24, self._tick_activity)
        try:
            self.query_one("#composer").add_class("-busy")
        except Exception:
            pass

    def _stop_activity_pulse(self) -> None:
        if self._activity_timer is not None:
            self._activity_timer.stop()
            self._activity_timer = None
        try:
            self.query_one("#composer").remove_class("-busy")
        except Exception:
            pass
        sync = getattr(self, "_sync_agent_color", None)
        if callable(sync):
            sync()
        self._refresh_activity_widgets()
