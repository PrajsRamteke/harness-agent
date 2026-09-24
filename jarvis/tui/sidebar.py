"""Session sidebar (opencode-style) — shown on wide terminals, ⌃B toggles.

Sections: session title, context/tokens (+cost when the model is priced),
model & agent, MCP server health, and files modified this session.
Everything is read from ``state`` at paint time; the app calls
``refresh()`` after turns and on a slow timer.
"""
from __future__ import annotations

from rich.text import Text
from textual.containers import VerticalScroll
from textual.widget import Widget

from . import theme as ui
from .. import state


def _fmt_tokens(n: int) -> str:
    if n >= 1_000_000:
        return f"{n / 1_000_000:.1f}M"
    if n >= 1000:
        return f"{n / 1000:.1f}k"
    return str(n)


_CTX_CACHE: dict[str, int | None] = {}


def context_window(model: str) -> int | None:
    """Best-known context window for ``model`` (None when unknown)."""
    if model in _CTX_CACHE:
        return _CTX_CACHE[model]
    ctx: int | None = None
    low = (model or "").lower()
    if low.startswith("claude"):
        ctx = 200_000
    else:
        try:
            from ..auth import catalog_cache
            from ..auth.openrouter_catalog import CACHE_NAME

            payload, _fresh = catalog_cache.read(CACHE_NAME)
            for row in payload or []:
                if isinstance(row, dict) and row.get("id") == model:
                    ctx = int(row.get("context_length") or 0) or None
                    break
        except Exception:
            ctx = None
    _CTX_CACHE[model] = ctx
    return ctx


def meter(fraction: float, width: int = 16) -> Text:
    """``▰▰▰▱▱▱`` usage bar, green → amber → red as it fills."""
    fraction = max(0.0, min(1.0, fraction))
    filled = round(fraction * width)
    color = ui.OK if fraction < 0.6 else ui.WARN if fraction < 0.85 else ui.ERR
    out = Text()
    out.append("▰" * filled, style=color)
    out.append("▱" * (width - filled), style=ui.BG_4)
    return out


def session_title(limit: int = 60) -> str:
    for m in state.messages:
        if m.get("role") == "user" and isinstance(m.get("content"), str):
            t = " ".join(m["content"].split())
            if t:
                return t if len(t) <= limit else t[: limit - 1] + "…"
    return "New session"


class SidebarBody(Widget):
    DEFAULT_CSS = """
    SidebarBody {
        height: auto;
        width: 1fr;
    }
    """

    can_focus = False

    def render(self) -> Text:
        app = self.app
        out = Text()

        def section(title: str, icon: str = "") -> None:
            if out.plain:
                out.append("\n\n")
            if icon:
                out.append(f"{icon} ", style=ui.ACCENT)
            out.append(title, style=f"bold {ui.FG}")

        def line(text: str, style: str = "", prefix: str = "") -> None:
            out.append("\n")
            if prefix:
                out.append(prefix)
            out.append(text, style=style or ui.FG_MUTE)

        # session
        out.append(session_title(), style=f"bold {ui.FG}")
        sid = state.current_session_id
        n_msgs = len(state.messages)
        meta = f"#{sid} · " if sid is not None else ""
        line(f"{meta}{n_msgs} message{'s' if n_msgs != 1 else ''}", ui.FG_DIM)

        section("Context", "◔")
        total = int(getattr(state, "total_tokens", 0) or 0)
        window = context_window(state.MODEL)
        if window and total:
            used = int(state.total_in or 0) + int(state.total_out or 0)
            frac = used / window
            out.append("\n")
            out.append_text(meter(frac))
            out.append(f" {frac * 100:.0f}%", style=ui.FG_MUTE)
            line(f"{_fmt_tokens(used)} / {_fmt_tokens(window)} tokens", ui.FG_DIM)
        else:
            line(f"{_fmt_tokens(total)} tokens" if total else "no usage yet")
        if total and not window:
            line(f"↑ {_fmt_tokens(int(state.total_in or 0))}  ↓ {_fmt_tokens(int(state.total_out or 0))}",
                 ui.FG_DIM)
        try:
            from ..constants import PRICING

            price = PRICING.get(state.MODEL)
            if price and (price[0] or price[1]) and total:
                from ..repl.stats import estimated_cost

                line(f"${estimated_cost():.4f} spent", ui.FG_DIM)
        except Exception:
            pass

        section("Model", "◇")
        line(state.MODEL, ui.FG)
        bits = []
        try:
            from ..constants.providers import PROVIDER_LABELS

            bits.append(PROVIDER_LABELS.get(state.provider, str(state.provider or "")))
        except Exception:
            pass
        if state.think_mode:
            bits.append(f"think {state.think_effort}")
        if bits:
            line(" · ".join(b for b in bits if b), ui.FG_DIM)
        rec = state.active_agent
        if rec is None and state.active_agent_name:
            try:
                rec = state.resolve_active_agent()
            except Exception:
                rec = None
        if rec:
            color = (rec.get("color") or "").strip() or ui.ACCENT
            icon = (rec.get("icon") or "").strip()
            line(f"{icon + ' ' if icon else ''}{rec['name']}", f"bold {color}")
        flags = []
        if state.plan_mode:
            flags.append(("plan mode", ui.WARN))
        if state.auto_approve:
            flags.append(("auto-approve", ui.WARN))
        if flags:
            out.append("\n")
            for i, (label, color) in enumerate(flags):
                if i:
                    out.append(" · ", style=ui.FG_DIM)
                out.append(label, style=color)

        # MCP
        try:
            from ..mcp.config import get_config
            from ..mcp.registry import mcp_registry

            servers = get_config().list_servers()
        except Exception:
            servers = {}
        if servers:
            section("MCP", "◈")
            for name, cfg in list(servers.items())[:8]:
                try:
                    h = mcp_registry.get_server_health(name)
                    status = h.get("status", "idle")
                except Exception:
                    status = "idle"
                color = {
                    "live": ui.OK, "warn": ui.WARN, "failed": ui.ERR, "connecting": ui.ACCENT,
                }.get(status, ui.FG_DIM)
                out.append("\n")
                out.append("● ", style=color)
                out.append(name, style=ui.FG_MUTE)
                if status in ("failed", "connecting"):
                    out.append(f" {status}", style=ui.FG_DIM)

        # modified files
        con = getattr(app, "_tui_console", None)
        changed = getattr(con, "changed_files", None) if con is not None else None
        if changed:
            section("Modified Files", "✎")
            for path, (added, removed) in list(changed.items())[-10:]:
                out.append("\n")
                name = path if len(path) <= 26 else "…" + path[-25:]
                out.append(name, style=ui.FG_MUTE)
                out.append(" ")
                if added:
                    out.append(f"+{added}", style=ui.OK)
                if removed:
                    out.append(f" -{removed}", style=ui.ERR)

        tools = int(getattr(state, "tool_calls_count", 0) or 0)
        if tools:
            section("Tools", "$")
            line(f"{tools} call{'s' if tools != 1 else ''} this session", ui.FG_DIM)
        return out


class Sidebar(VerticalScroll):
    DEFAULT_CSS = """
    Sidebar {
        width: 38;
        height: 100%;
        background: $jv-bg-1;
        padding: 1 2;
        scrollbar-size-vertical: 0;
    }
    Sidebar.hidden {
        display: none;
    }
    """

    can_focus = False

    def compose(self):
        yield SidebarBody()

    def refresh_body(self) -> None:
        try:
            self.query_one(SidebarBody).refresh(layout=True)
        except Exception:
            pass
