"""Conversation transcript — one widget per message, tool call, or notice.

Layout of a turn (Claude Code–style timeline, opencode-style user box)::

    ┃ fix the stall retry                       ← UserBlock (tinted, accent bar)

    ⏺ I'll check the stream module first.       ← AssistantBlock (streaming markdown)

    ⏺ Read jarvis/repl/stream.py                ← ToolBlock (live status glyph)
      ⎿  Read 758 lines
    ⏺ Bash pytest -q
      ⎿  4 passed in 0.31s

    ▣ coding · sonnet-4-6 · 12.4s               ← TurnFooter

Every block renders from its own data at paint time using the live theme
tokens, so theme switches, trace toggles and terminal resizes restyle the
existing conversation in place — nothing is re-rendered from history.

The container is anchored to the bottom (``Widget.anchor``): new output
follows automatically until the user scrolls up, and scrolling back to the
end re-attaches.
"""
from __future__ import annotations

import time
from typing import Any

from rich import box
from rich.console import Group, RenderableType
from rich.panel import Panel
from rich.rule import Rule
from rich.text import Text
from textual.containers import VerticalScroll
from textual.widget import Widget

from . import theme as ui
from . import tool_format as tf


def _markup(text: str) -> Text:
    try:
        return Text.from_markup(text)
    except Exception:
        return Text(text)


def _plain(renderable: Any) -> str:
    if isinstance(renderable, Text):
        return renderable.plain
    if isinstance(renderable, str):
        return _markup(renderable).plain
    if isinstance(renderable, Panel):
        inner = _plain(renderable.renderable)
        title = _plain(renderable.title) if renderable.title else ""
        return f"{title}\n{inner}" if title else inner
    if isinstance(renderable, Group):
        return "\n".join(_plain(r) for r in renderable.renderables)
    for attr in ("markup", "plain", "text"):
        val = getattr(renderable, attr, None)
        if isinstance(val, str):
            return val
    return ""


# ── base ──────────────────────────────────────────────────────────────────


class Block(Widget):
    """A transcript entry. Renders live from its data; selectable."""

    DEFAULT_CSS = """
    Block {
        height: auto;
        width: 1fr;
        margin: 1 0 0 0;
        padding: 0;
        background: transparent;
    }
    Block.-tight {
        margin-top: 0;
    }
    """

    can_focus = False

    def plain_text(self) -> str:
        return ""

    def tick(self, frame: int) -> None:  # pragma: no cover - overridden
        """Advance any animation (called ~20×/s while the agent is busy)."""


def shimmer(text: str, base: str, hi: str, *, speed: float = 14.0, width: float = 4.0,
            italic: bool = False, bold: bool = False) -> Text:
    """A soft highlight band sweeping across ``text`` (time-based)."""
    span = len(text) + 10
    center = (time.monotonic() * speed) % span - 5
    out = Text()
    extra = ("italic " if italic else "") + ("bold " if bold else "")
    for i, ch in enumerate(text):
        k = max(0.0, 1.0 - abs(i - center) / width)
        out.append(ch, style=f"{extra}{ui.blend(base, hi, k)}")
    return out


# ── welcome ───────────────────────────────────────────────────────────────

_LOGO = (
    "   █ ▄▀█ █▀█ █ █ █ █▀",
    " █▄█ █▀█ █▀▄ ▀▄▀ █ ▄█",
)


class WelcomeBlock(Block):
    """Compact wordmark + where you are + how to start."""

    DEFAULT_CSS = """
    WelcomeBlock {
        padding: 0 0 0 0;
        margin: 1 0 0 0;
    }
    """

    SHINE_SECS = 1.1

    def __init__(self, info: dict[str, Any]) -> None:
        super().__init__()
        self.info = info
        self._shine_t0: float | None = None

    def start_shine(self) -> None:
        """One diagonal light sweep across the wordmark after launch."""
        self._shine_t0 = time.monotonic()
        timer = self.set_interval(1 / 30, self._shine_tick)
        self._shine_timer = timer

    def _shine_tick(self) -> None:
        self.refresh()
        if self._shine_t0 is None or time.monotonic() - self._shine_t0 > self.SHINE_SECS:
            self._shine_t0 = None
            self._shine_timer.stop()
            self.refresh()

    def plain_text(self) -> str:
        return str(self.render())

    def render(self) -> Text:
        i = self.info
        out = Text()
        width = max(len(line) for line in _LOGO)
        band = None
        if self._shine_t0 is not None:
            p = (time.monotonic() - self._shine_t0) / self.SHINE_SECS
            band = -6 + p * (width + 12)
        for row, line in enumerate(_LOGO):
            for col, ch in enumerate(line):
                if ch == " ":
                    out.append(" ")
                    continue
                t = col / max(1, width - 1)
                color = ui.blend(ui.ACCENT, ui.ACCENT_3, t)
                if band is not None:
                    k = max(0.0, 1.0 - abs(col + row * 2 - band) / 3.5)
                    color = ui.blend(color, "#ffffff", 0.75 * k)
                out.append(ch, style=f"bold {color}")
            if row == 0:
                out.append("   ")
                out.append(f"v{i.get('version', '')}", style=ui.FG_DIM)
            else:
                out.append("   ")
                out.append(str(i.get("model", "")), style=f"bold {ui.FG}")
                prov = i.get("provider")
                if prov:
                    out.append(f"  {prov}", style=ui.FG_DIM)
            out.append("\n")
        out.append("\n")
        out.append("  ")
        out.append(str(i.get("cwd", "")), style=ui.FG_MUTE)
        if i.get("branch"):
            out.append("  ⎇ ", style=ui.FG_DIM)
            out.append(str(i["branch"]), style=ui.ACCENT_2)
        ctx = [c for c in (i.get("context") or []) if c]
        if ctx:
            out.append("\n  ")
            for n, bit in enumerate(ctx):
                if n:
                    out.append(" · ", style=ui.FG_DIM)
                out.append(bit, style=ui.FG_DIM)
        if i.get("warning"):
            out.append("\n\n  ")
            out.append("● ", style=ui.WARN)
            out.append_text(_markup(i["warning"]))
        out.append("\n\n")
        hints = (
            ("/", "commands"),
            ("@", "files"),
            ("!", "shell"),
            ("tab", "agents"),
            ("?", "shortcuts"),
        )
        out.append("  ")
        for n, (key, label) in enumerate(hints):
            if n:
                out.append("   ")
            out.append(key, style=f"bold {ui.FG_MUTE}")
            out.append(f" {label}", style=ui.FG_DIM)
        return out


# ── user ──────────────────────────────────────────────────────────────────

_USER_MAX_LINES = 24


class UserBlock(Block):
    """The user's message: tinted box with a heavy accent bar (opencode)."""

    DEFAULT_CSS = """
    UserBlock {
        background: $jv-user-bg;
        border-left: heavy $jv-accent;
        padding: 0 2 0 1;
        margin: 1 0 0 0;
        color: $jv-fg;
    }
    UserBlock.-shell {
        border-left: heavy $jv-warn;
    }
    UserBlock.-queued {
        border-left: heavy $jv-fg-dim;
        color: $jv-fg-mute;
    }
    """

    def __init__(self, text: str, *, shell: bool = False, flash: bool = False) -> None:
        super().__init__()
        self.text = text or ""
        self.phase = ""  # live spinner label (slash commands like /upgrade)
        self._frame = 0
        self._flash = flash
        if shell:
            self.add_class("-shell")

    def on_mount(self) -> None:
        if not self._flash:
            return
        # Brief accent glow when a message is sent, easing into the box color.
        try:
            from textual.color import Color

            base_hex = ui.blend(ui.BG_0, ui.BG_3, 0.8)
            base = Color.parse(base_hex)
            self.styles.background = Color.parse(ui.blend(base_hex, ui.ACCENT, 0.35))
            self.styles.animate(
                "background", base, duration=0.55, easing="out_cubic",
                on_complete=lambda: self.styles.clear_rule("background"),
            )
        except Exception:
            pass

    def plain_text(self) -> str:
        return self.text

    def set_phase(self, phase: str) -> None:
        self.phase = phase or ""
        self.refresh(layout=True)

    def tick(self, frame: int) -> None:
        if self.phase:
            self._frame = frame
            self.refresh()

    def render(self) -> Text:
        lines = self.text.split("\n")
        hidden = 0
        if len(lines) > _USER_MAX_LINES:
            hidden = len(lines) - (_USER_MAX_LINES - 4)
            lines = lines[: _USER_MAX_LINES - 4]
        out = Text()
        for n, line in enumerate(lines):
            if n:
                out.append("\n")
            _append_with_refs(out, line)
        if hidden:
            out.append(f"\n… +{hidden} more lines", style=ui.FG_DIM)
        if self.phase:
            sp = ui.SPINNER_FRAMES[self._frame % len(ui.SPINNER_FRAMES)]
            out.append(f"  {sp} ", style=ui.ACCENT)
            out.append(self.phase, style=ui.FG_DIM)
        return out


def _append_with_refs(out: Text, line: str) -> None:
    """Append ``line`` with ``@file`` refs and ``[image 1]`` chips accented."""
    import re

    pos = 0
    for m in re.finditer(r"(?<![\w@])@[\w./~\-]+|\[(?:image|document|file) \d+\]|\[Pasted text #\d+[^\]]*\]", line):
        if m.start() > pos:
            out.append(line[pos:m.start()], style=ui.FG)
        out.append(m.group(0), style=f"bold {ui.ACCENT_2}")
        pos = m.end()
    if pos < len(line):
        out.append(line[pos:], style=ui.FG)


# ── gutter blocks: one widget, pre-wrapped lines, hanging indent ─────────


class _NullFile:
    def write(self, *_a) -> int:
        return 0

    def flush(self) -> None:
        pass


_WRAP_CONSOLE = None


def _wrap_console():
    global _WRAP_CONSOLE
    if _WRAP_CONSOLE is None:
        from rich.console import Console

        _WRAP_CONSOLE = Console(width=1000, color_system="truecolor",
                                force_terminal=True, file=_NullFile())
    return _WRAP_CONSOLE


def wrap_lines(text: Text, width: int) -> list[Text]:
    """Split ``text`` on newlines and soft-wrap every line to ``width``."""
    if not text.plain:
        return [Text("")]
    return list(text.wrap(_wrap_console(), max(4, width), overflow="fold"))


def gutter_join(glyph: Text, lines: list[Text]) -> Text:
    """``glyph`` in columns 0–1 of the first line; the rest indented by 2."""
    out = Text(no_wrap=True, overflow="crop", end="")
    for i, line in enumerate(lines or [Text("")]):
        if i:
            out.append("\n  ")
        else:
            out.append_text(glyph)
            out.append(" " * max(0, 2 - glyph.cell_len))
        out.append_text(line)
    return out


class LineBlock(Block):
    """A block that renders itself as pre-wrapped lines behind a glyph.

    One widget per entry (cheap layout / scrolling), exact hanging indent,
    output cached per (width, theme, version). Subclasses implement
    ``glyph()`` and ``body_lines(width)``; call ``touch()`` after changes.
    """

    def __init__(self) -> None:
        super().__init__()
        self._ver = 0
        self._cache_key: tuple | None = None
        self._cache_text = Text("")

    def touch(self, *, layout: bool = True) -> None:
        self._ver += 1
        self.refresh(layout=layout)

    def repaint(self, *, layout: bool = True) -> None:
        self.touch(layout=layout)

    def glyph(self) -> Text:
        return Text(" ")

    def body_lines(self, width: int) -> list[Text]:
        return [Text("")]

    def build(self, width: int) -> Text:
        return gutter_join(self.glyph(), self.body_lines(max(8, width - 2)))

    def _text(self, width: int) -> Text:
        from .. import state

        width = max(10, int(width or 0) or 100)
        key = (width, ui.active_theme(), self._ver, bool(state.show_internal))
        if key != self._cache_key:
            self._cache_text = self.build(width)
            self._cache_key = key
        return self._cache_text

    def get_content_width(self, container, viewport) -> int:
        return container.width

    def get_content_height(self, container, viewport, width: int) -> int:
        return self._text(width).plain.count("\n") + 1

    def render(self) -> Text:
        return self._text(self.content_region.width or self.size.width)


# ── assistant ─────────────────────────────────────────────────────────────


class AssistantBlock(LineBlock):
    """Assistant reply: ``⏺`` + markdown that streams in place.

    While streaming, text before the last safe paragraph break is frozen
    into chunks rendered once (cached per width); only the short live tail
    re-renders on each update.
    """

    # A streaming reply is split into continuation blocks once this much
    # text is frozen, so each update only re-joins a bounded amount.
    SPLIT_AT = 3500

    def __init__(self, text: str = "", *, streaming: bool = False,
                 continuation: bool = False) -> None:
        super().__init__()
        self.text = text or ""
        self.streaming = streaming
        self.continuation = continuation
        self.interrupted = False
        self._chunks: list[str] = []
        self._frozen_upto = 0
        self._chunk_cache: dict[tuple, list[Text]] = {}

    def should_split(self) -> bool:
        return self.streaming and self._frozen_upto >= self.SPLIT_AT

    def split_off(self) -> "AssistantBlock":
        """Seal the frozen part here; return a block holding the live tail."""
        tail = self.text[self._frozen_upto:]
        self.text = self.text[:self._frozen_upto].rstrip("\n")
        self._frozen_upto = len(self.text)
        self.streaming = False
        self.touch()
        return AssistantBlock(tail, streaming=True, continuation=True)

    def plain_text(self) -> str:
        return self.text + ("\n⏹ interrupted" if self.interrupted else "")

    def repaint(self, *, layout: bool = True) -> None:
        self._chunk_cache.clear()
        self.touch(layout=layout)

    def tick_pulse(self) -> None:
        """Breathe the ⏺ while this reply is still streaming (glyph only)."""
        if self.streaming and not self.continuation:
            self.touch(layout=False)

    def glyph(self) -> Text:
        if self.continuation and not self.interrupted:
            return Text(" ")
        if self.interrupted:
            return Text(ui.GUTTER, style=ui.WARN)
        if self.has_class("-flagged"):
            return Text(ui.GUTTER, style=ui.ERR)
        if self.streaming:
            import math

            k = (math.sin(time.monotonic() * 5.0) + 1) / 2
            return Text(ui.GUTTER, style=ui.blend(ui.FG_DIM, ui.ACCENT, k))
        return Text(ui.GUTTER, style=ui.FG)

    def _md_lines(self, source: str, width: int) -> list[Text]:
        from .md_render import render_markdown

        rendered = render_markdown(source, width)
        return rendered.split("\n") if rendered.plain else []

    def body_lines(self, width: int) -> list[Text]:
        lines: list[Text] = []
        theme = ui.active_theme()
        for i, chunk in enumerate(self._chunks):
            key = (i, width, theme)
            cached = self._chunk_cache.get(key)
            if cached is None:
                cached = self._md_lines(chunk, width)
                self._chunk_cache[key] = cached
            if lines and cached:
                lines.append(Text(""))
            lines.extend(cached)
        live = self.text[self._frozen_upto:]
        if self.interrupted:
            live = live.rstrip() + "\n\n*⏹ interrupted*"
        tail = self._md_lines(live, width) if live.strip() else []
        if lines and tail:
            lines.append(Text(""))
        lines.extend(tail)
        return lines or [Text("")]

    def append(self, fragment: str) -> None:
        if not fragment:
            return
        from .md_render import split_stable

        self.text += fragment
        live = self.text[self._frozen_upto:]
        cut = split_stable(live)
        if cut:
            self._chunks.append(live[:cut].strip("\n"))
            self._frozen_upto += cut
            while self.text[self._frozen_upto:self._frozen_upto + 1] == "\n":
                self._frozen_upto += 1
        self.touch()

    def finalize(self, text: str | None = None, *, interrupted: bool = False) -> None:
        """Stop streaming; re-render once if the final text differs."""
        self.streaming = False
        if text is not None and text != self.text:
            self.text = text
            self._chunks = []
            self._frozen_upto = 0
            self._chunk_cache.clear()
        if interrupted and not self.interrupted:
            self.interrupted = True
            self.add_class("-interrupted")
        self.touch()


class PlanBlock(AssistantBlock):
    """A proposed plan (plan mode): framed card, same markdown renderer."""

    DEFAULT_CSS = """
    PlanBlock {
        border: round $jv-accent;
        padding: 0 1;
    }
    """

    def glyph(self) -> Text:
        return Text("≡", style=f"bold {ui.ACCENT}")

    def body_lines(self, width: int) -> list[Text]:
        head = Text("Proposed plan", style=f"bold {ui.ACCENT}")
        head.append("  approve or revise below", style=ui.FG_DIM)
        return [head, Text("")] + super().body_lines(width)


# ── thinking ──────────────────────────────────────────────────────────────

_THINK_LIVE_LINES = 6
_THINK_MAX_LINES = 20


class ThinkingBlock(LineBlock):
    """Reasoning trace (dim). Hidden unless trace is on (CSS on transcript).

    While streaming only the last few lines show (no wall of moving text);
    once finished, long reasoning is capped — click to expand / collapse.
    """

    def __init__(self, text: str = "", *, live: bool = False) -> None:
        super().__init__()
        self.text = text or ""
        self.live = live
        self.expanded = False
        self.t0 = time.monotonic()
        self.t1: float | None = None if live else self.t0

    def on_click(self) -> None:
        if not self.live:
            self.expanded = not self.expanded
            self.touch()

    def plain_text(self) -> str:
        return self.text

    def append(self, fragment: str) -> None:
        if fragment:
            self.text += fragment
            self.touch()

    def finish(self, text: str | None = None) -> None:
        if text is not None:
            self.text = text
        if self.live:
            self.live = False
            self.t1 = time.monotonic()
        self.touch()

    def glyph(self) -> Text:
        return Text("∴", style=ui.ACCENT_3 if self.live else ui.FG_DIM)

    def tick_pulse(self) -> None:
        if self.live:
            self.touch(layout=False)

    def body_lines(self, width: int) -> list[Text]:
        out = Text()
        if self.live:
            out.append_text(shimmer("Thinking…", ui.FG_MUTE, ui.FG, italic=True))
        elif self.t1 is not None and self.t1 - self.t0 >= 0.5:
            out.append(f"Thought for {self.t1 - self.t0:.1f}s", style=f"italic {ui.FG_MUTE}")
        else:
            out.append("Thinking", style=f"italic {ui.FG_MUTE}")
        text = self.text.strip()
        if text:
            note = ""
            if self.live:
                shown = "\n".join(text.split("\n")[-_THINK_LIVE_LINES:])
                if len(shown) > 700:
                    shown = shown[-700:]
                if shown != text:
                    shown = "…" + shown.lstrip()
            elif not self.expanded and (text.count("\n") >= _THINK_MAX_LINES or len(text) > 2400):
                shown = "\n".join(text.split("\n")[:_THINK_MAX_LINES])[:2400].rstrip()
                note = f"… {len(text) - len(shown):,} more chars · click to expand"
            else:
                shown = text
                if self.expanded:
                    note = "click to collapse"
            out.append("\n")
            out.append(shown, style=f"italic {ui.FG_DIM}")
            if note:
                out.append(f"\n{note}", style=ui.FG_DIM)
        return wrap_lines(out, width)


# ── tools ─────────────────────────────────────────────────────────────────


_FLASH_SECS = 0.7
_EXPANDED_LINES = 40


class ToolBlock(LineBlock):
    """One tool call: status-colored icon, title, args, and a ⎿ result line.

    Click to expand the raw output inline; the icon flashes briefly when
    the call finishes.
    """

    DEFAULT_CSS = """
    ToolBlock:hover {
        background: $jv-bg-1;
    }
    """

    def __init__(self, tool_id: str, name: str, tool_input: Any = None) -> None:
        super().__init__()
        self.tool_id = tool_id
        self.tool_name = name
        self.tool_input = tool_input if tool_input is not None else {}
        self.status = "running"  # running | done | error | cancelled
        self.summary: list[str] = []
        self.output = ""
        self.repaired = False
        self.expanded = False
        self.diff_added = 0
        self.diff_removed = 0
        self.t0 = time.monotonic()
        self.t1: float | None = None
        self._frame = 0
        self._running_line = False
        self.attached: list[Widget] = []  # diff blocks mounted under this row

    def plain_text(self) -> str:
        head = f"{tf.tool_title(self.tool_name)} {tf.tool_args(self.tool_name, self.tool_input)}".strip()
        return "\n".join([head] + [f"⎿ {s}" for s in self.summary])

    @property
    def animating(self) -> bool:
        """Running, or inside the short finish flash."""
        if self.status == "running":
            return True
        return self.t1 is not None and time.monotonic() - self.t1 < _FLASH_SECS

    def finish(self, output: str, *, error: bool | None = None, repaired: bool = False) -> None:
        self.output = output or ""
        lines, is_err = tf.tool_summary(self.tool_name, self.tool_input, self.output)
        if error is None:
            error = is_err
        self.summary = lines
        self.status = "error" if (error or is_err) else "done"
        self.repaired = repaired
        self.t1 = time.monotonic()
        self.touch()

    def cancel(self) -> None:
        if self.status == "running":
            self.status = "cancelled"
            self.summary = ["cancelled"]
            self.t1 = time.monotonic()
            self.touch()

    def add_diff_stats(self, added: int, removed: int) -> None:
        self.diff_added += added
        self.diff_removed += removed
        self.touch()

    def on_click(self) -> None:
        if self.status == "running" or not self.output.strip():
            return
        self.expanded = not self.expanded
        self.touch()

    def tick(self, frame: int) -> None:
        if not self.animating:
            return
        self._frame = frame
        if self.status != "running":
            self.touch(layout=False)  # finish flash
            return
        want_line = time.monotonic() - self.t0 > 1.0
        self.touch(layout=want_line and not self._running_line)
        self._running_line = want_line

    def glyph(self) -> Text:
        if self.status == "running":
            return Text(ui.SPINNER_FRAMES[self._frame % len(ui.SPINNER_FRAMES)], style=ui.ACCENT)
        icon = tf.tool_icon(self.tool_name)
        if self.status == "error":
            return Text(icon, style=f"bold {ui.ERR}")
        if self.status == "cancelled":
            return Text(icon, style=ui.FG_DIM)
        color = ui.OK
        if self.t1 is not None:
            k = (time.monotonic() - self.t1) / _FLASH_SECS
            if k < 1:
                color = ui.blend(ui.blend(ui.OK, "#ffffff", 0.7), ui.OK, k)
        return Text(icon, style=f"bold {color}")

    def as_text(self) -> Text:
        """Glyph + body as one Text (tests, copy)."""
        return self.build(100)

    def body_lines(self, width: int) -> list[Text]:
        from .. import state

        title = tf.tool_title(self.tool_name)
        args = tf.tool_args(self.tool_name, self.tool_input, width=max(10, width - len(title) - 2))
        head = Text()
        head.append(title, style=f"bold {ui.FG}")
        if args:
            head.append(" ")
            head.append(args, style=ui.FG_MUTE)
        if self.repaired:
            head.append("  ⚒ repaired", style=ui.WARN)
        lines = wrap_lines(head, width)

        def sub(prefix: str, text: str, style: str, extra: Text | None = None) -> None:
            row = Text(prefix, style=ui.FG_DIM)
            row.append(tf.clip(text, max(8, width - len(prefix) - (extra.cell_len if extra else 0))),
                       style=style)
            if extra is not None:
                row.append_text(extra)
            lines.append(row)

        if self.status == "running":
            elapsed = time.monotonic() - self.t0
            if elapsed > 1.0:
                sub("⎿  ", f"running… {elapsed:.0f}s", ui.FG_DIM)
            return lines

        stats = None
        if self.diff_added or self.diff_removed:
            stats = Text("  ")
            stats.append(f"+{self.diff_added}", style=ui.OK)
            stats.append(" ")
            stats.append(f"-{self.diff_removed}", style=ui.ERR)
        summary_style = ui.ERR if self.status == "error" else ui.FG_MUTE
        for n, line in enumerate(self.summary or []):
            style = ui.FG_DIM if line.startswith("… +") else summary_style
            sub("⎿  " if n == 0 else "   ", line, style, stats if n == 0 else None)

        show_preview = self.expanded or (
            state.show_internal
            and self.status != "error"
            and self.tool_name not in ("read_file", "write_file", "edit_file", "multi_edit", "run_bash")
        )
        if show_preview and self.output.strip():
            limit = _EXPANDED_LINES if self.expanded else 8
            preview = tf.preview_lines(self.output, max_lines=limit, width=max(16, width - 6),
                                       more_hint="click to collapse" if self.expanded else "click to expand")
            for line in preview:
                sub("   │ ", line, ui.FG_DIM)
        elif self.output.strip() and self.status == "done" and self.tool_name not in (
            "write_file", "edit_file", "multi_edit",
        ):
            n = len(self.output.strip().splitlines())
            if n > 1 and self.is_mouse_over:
                sub("   ", "click to show output", ui.FG_DIM)
        return lines

    def on_enter(self) -> None:
        self.touch()

    def on_leave(self) -> None:
        self.touch()


class DiffBlock(Block):
    """Unified diff under an edit/write row — line numbers + tinted +/- rows."""

    DEFAULT_CSS = """
    DiffBlock {
        margin: 0 0 0 0;
        padding: 0 0 0 5;
    }
    """

    def __init__(self, path: str, before: str = "", after: str = "", action: str = "edit",
                 max_lines: int = 60, *, rows: list | None = None, added: int = 0,
                 removed: int = 0, hidden: int = 0) -> None:
        super().__init__()
        self.path = path
        self.action = action
        if rows is None:
            rows, added, removed, hidden = diff_rows(before, after, max_lines)
        self.rows, self.added, self.removed, self.hidden = rows, added, removed, hidden

    def plain_text(self) -> str:
        return "\n".join(f"{sign}{txt}" for _n, sign, txt in self.rows)

    def render(self) -> Text:
        width = max(20, self.size.width or 100)
        out = Text()
        num_w = max((len(str(n)) for n, _s, _t in self.rows if n), default=1)
        add_bg = ui.blend(ui.BG_0, ui.OK, 0.16)
        del_bg = ui.blend(ui.BG_0, ui.ERR, 0.16)
        for i, (num, sign, txt) in enumerate(self.rows):
            if i:
                out.append("\n")
            if sign == "@":
                out.append(" " * (num_w + 1) + "⋮", style=ui.FG_DIM)
                continue
            gutter = f"{num:>{num_w}} " if num else " " * (num_w + 1)
            body = f"{sign} {txt}"
            pad = max(0, width - len(gutter) - len(body))
            if sign == "+":
                out.append(gutter, style=f"{ui.FG_DIM} on {add_bg}")
                out.append(body + " " * pad, style=f"{ui.FG} on {add_bg}")
            elif sign == "-":
                out.append(gutter, style=f"{ui.FG_DIM} on {del_bg}")
                out.append(body + " " * pad, style=f"{ui.FG} on {del_bg}")
            else:
                out.append(gutter, style=ui.FG_DIM)
                out.append(body, style=ui.FG_MUTE)
        if self.hidden:
            out.append(f"\n… {self.hidden} more diff lines", style=ui.FG_DIM)
        return out


def diff_rows(before: str, after: str, max_lines: int) -> tuple[list[tuple[int, str, str]], int, int, int]:
    """Rows of ``(line_no, sign, text)`` with sign in ``+``/``-``/`` ``/``@``."""
    import difflib
    import re

    a, b = before.splitlines(), after.splitlines()
    rows: list[tuple[int, str, str]] = []
    added = removed = 0
    old_n = new_n = 0
    for ln in difflib.unified_diff(a, b, lineterm="", n=2):
        if ln.startswith("---") or ln.startswith("+++"):
            continue
        if ln.startswith("@@"):
            m = re.match(r"@@ -(\d+)(?:,\d+)? \+(\d+)", ln)
            if m:
                old_n, new_n = int(m.group(1)), int(m.group(2))
            if rows:
                rows.append((0, "@", ""))
            continue
        text = ln[1:].rstrip("\n")
        if len(text) > 400:
            text = text[:400] + " …"
        text = text.replace("\t", "    ")
        if ln.startswith("+"):
            rows.append((new_n, "+", text))
            new_n += 1
            added += 1
        elif ln.startswith("-"):
            rows.append((old_n, "-", text))
            old_n += 1
            removed += 1
        else:
            rows.append((new_n, " ", text))
            old_n += 1
            new_n += 1
    hidden = max(0, len(rows) - max_lines)
    return rows[:max_lines], added, removed, hidden


# ── generic output ────────────────────────────────────────────────────────


def soften(renderable: Any) -> Any:
    """Restyle legacy boxed panels (commands, pickers) to the quiet look."""
    if isinstance(renderable, Panel):
        title = renderable.title
        if isinstance(title, str):
            title = _markup(f" {title} ") if title else None
            if title is not None:
                title.stylize(f"bold {ui.FG}")
        return Panel(
            renderable.renderable,
            title=title,
            title_align="left",
            subtitle=renderable.subtitle,
            box=box.ROUNDED,
            border_style=ui.BORDER,
            padding=renderable.padding,
            expand=renderable.expand,
        )
    if isinstance(renderable, Rule):
        return Rule(renderable.title, style=ui.BORDER, characters="─")
    try:
        from rich.table import Table

        if isinstance(renderable, Table):
            # Command output tables (/history, /stats, …): quiet header + rules.
            renderable.header_style = f"bold {ui.FG_MUTE}"
            renderable.border_style = ui.BORDER
            renderable.title_style = f"bold {ui.FG}"
            if renderable.box is not None:
                renderable.box = box.SIMPLE_HEAD
            renderable.pad_edge = False
    except Exception:
        pass
    return renderable


class NoticeBlock(Block):
    """Plain console output (command results, status lines, errors).

    Consecutive short prints coalesce into one block so chatty commands
    don't explode the widget count.
    """

    DEFAULT_CSS = """
    NoticeBlock {
        padding: 0 0 0 2;
        color: $jv-fg-mute;
    }
    """

    def __init__(self, renderable: RenderableType) -> None:
        super().__init__()
        self.items: list[RenderableType] = [renderable]

    @property
    def text_only(self) -> bool:
        return all(isinstance(i, Text) for i in self.items)

    def add(self, renderable: RenderableType) -> None:
        self.items.append(renderable)
        self.refresh(layout=True)

    def plain_text(self) -> str:
        return "\n".join(_plain(i) for i in self.items)

    def render(self):
        if self.text_only:
            return Text("\n").join(self.items)  # type: ignore[arg-type]
        return Group(*self.items)


class TurnFooter(Block):
    """``▣ agent · model · 12.4s`` after a completed turn."""

    DEFAULT_CSS = """
    TurnFooter {
        padding: 0 0 0 0;
        color: $jv-fg-dim;
        width: auto;
    }
    """

    def __init__(self, agent: str, color: str, model: str, seconds: float,
                 extra: str = "", interrupted: bool = False, reply: str = "") -> None:
        super().__init__()
        self.agent = agent
        self.color = color
        self.model = model
        self.seconds = seconds
        self.extra = extra
        self.interrupted = interrupted
        self.reply = reply or ""
        self._copied_at = 0.0

    def plain_text(self) -> str:
        return self._line(hover=False).plain

    def on_enter(self) -> None:
        self.refresh()

    def on_leave(self) -> None:
        self.refresh()

    def on_click(self) -> None:
        """Copy this turn's final reply (clean text)."""
        if not self.reply.strip():
            return
        try:
            from ..utils.clipboard import normalize_copy_text

            text = normalize_copy_text(self.reply)
            copy = getattr(self.app, "_copy_text", None)
            if callable(copy):
                copy(text)
            self._copied_at = time.monotonic()
            self.refresh()
            self.set_timer(1.6, self.refresh)
            self.app.notify(f"Copied reply ({len(text):,} chars)", timeout=1.8)
        except Exception:
            pass

    def render(self) -> Text:
        return self._line(hover=self.is_mouse_over)

    def _line(self, *, hover: bool) -> Text:
        out = Text()
        out.append("▣ ", style=ui.WARN if self.interrupted else (self.color or ui.ACCENT))
        out.append(self.agent, style=ui.FG_MUTE)
        out.append(" · ", style=ui.FG_DIM)
        out.append(self.model, style=ui.FG_DIM)
        out.append(" · ", style=ui.FG_DIM)
        out.append(_fmt_secs(self.seconds), style=ui.FG_DIM)
        if self.extra:
            out.append(" · ", style=ui.FG_DIM)
            out.append(self.extra, style=ui.FG_DIM)
        if self.interrupted:
            out.append(" · interrupted", style=ui.WARN)
        if self.reply.strip():
            if time.monotonic() - self._copied_at < 1.5:
                out.append("  ✓ copied", style=ui.OK)
            elif hover:
                out.append("  ⎘ copy reply", style=ui.ACCENT)
        return out


def _fmt_secs(s: float) -> str:
    if s < 60:
        return f"{s:.1f}s"
    m, sec = divmod(int(s), 60)
    return f"{m}m {sec:02d}s"


# ── container ─────────────────────────────────────────────────────────────


class Transcript(VerticalScroll):
    """Scrollable, bottom-anchored list of blocks."""

    DEFAULT_CSS = """
    Transcript {
        height: 1fr;
        min-height: 0;
        background: $jv-bg-0;
        padding: 0 3 0 2;
        scrollbar-size-vertical: 1;
        scrollbar-background: $jv-bg-0;
        scrollbar-background-hover: $jv-bg-0;
        scrollbar-background-active: $jv-bg-0;
        scrollbar-color: $jv-bg-2;
        scrollbar-color-hover: $jv-border;
        scrollbar-color-active: $jv-accent;
    }
    Transcript:focus {
        background-tint: transparent;
    }
    Transcript.-trace-off ThinkingBlock {
        display: none;
    }
    """

    can_focus = False

    def on_mount(self) -> None:
        self.anchor()

    def watch_scroll_y(self, old_value: float, new_value: float) -> None:
        super().watch_scroll_y(old_value, new_value)
        if round(old_value) != round(new_value):
            fn = getattr(self.app, "_refresh_activity_widgets", None)
            if callable(fn):
                fn()

    @property
    def more_below(self) -> int:
        """Lines below the viewport once the user has scrolled away (0 while
        following — the anchor may briefly lag behind new content)."""
        if not getattr(self, "_anchor_released", False):
            return 0
        try:
            return max(0, int(self.max_scroll_y - self.scroll_y))
        except Exception:
            return 0

    # ── mounting ─────────────────────────────────────────────────────
    @property
    def blocks(self) -> list[Widget]:
        return list(self.children)

    def last_block(self) -> Widget | None:
        kids = self.children
        return kids[-1] if kids else None

    def add(self, block: Widget, *, after: Widget | None = None,
            before: Widget | None = None) -> Widget:
        """Mount ``block`` (at the end unless ``after``/``before`` given)."""
        prev = after if after is not None else (None if before is not None else self.last_block())
        if isinstance(block, ToolBlock) and isinstance(prev, (ToolBlock, DiffBlock)):
            block.add_class("-tight")
        if after is not None and after.parent is self:
            self.mount(block, after=after)
        elif before is not None and before.parent is self:
            self.mount(block, before=before)
        else:
            self.mount(block)
        return block

    def add_many(self, blocks: list[Widget]) -> None:
        """Mount many blocks in one layout pass (session replay)."""
        prev = self.last_block()
        for block in blocks:
            if isinstance(block, ToolBlock) and isinstance(prev, (ToolBlock, DiffBlock)):
                block.add_class("-tight")
            prev = block
        if blocks:
            self.mount_all(blocks)

    def write(self, content: Any, *, expand: bool = True, scroll_end: bool | None = None,
              shrink: bool = True) -> Widget:
        """Append arbitrary console output (RichLog-compatible signature)."""
        del expand, shrink, scroll_end
        if isinstance(content, str):
            content = _markup(content)
        content = soften(content)
        last = self.last_block()
        if (
            isinstance(content, Text)
            and isinstance(last, NoticeBlock)
            and last.text_only
            and len(last.items) < 200
        ):
            last.add(content)
            return last
        return self.add(NoticeBlock(content))

    def clear(self):
        return self.remove_children()

    # ── queries ──────────────────────────────────────────────────────
    def plain_text(self) -> str:
        parts: list[str] = []
        for child in self.children:
            fn = getattr(child, "plain_text", None)
            if callable(fn):
                try:
                    parts.append(fn())
                except Exception:
                    pass
        return "\n".join(parts)

    @property
    def following(self) -> bool:
        return (not self._anchor_released) or self.is_vertical_scroll_end

    def follow(self) -> None:
        """Jump to the end and re-attach auto-follow."""
        self.anchor()
        self.scroll_end(animate=False)

    def restyle(self, *, layout: bool = True) -> None:
        """Repaint every block. Theme changes keep line counts, so they pass
        ``layout=False`` and only visible blocks re-render."""
        for child in self.children:
            repaint = getattr(child, "repaint", None)
            if callable(repaint):
                repaint(layout=layout)
            else:
                child.refresh(layout=layout)
