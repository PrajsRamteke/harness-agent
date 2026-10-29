"""/usage — what the OpenRouter key has spent and has left.

The terminal gets a panel (theme colours when the TUI is loaded), web pages
a card (``console.show_usage`` → ``usage`` event). Data: ``auth/openrouter_usage``.
"""
from __future__ import annotations

from rich.console import Group
from rich.table import Table
from rich.text import Text

from ..auth import openrouter_usage as oru
from ..console import console, Panel


def _colors() -> dict[str, str]:
    try:
        from ..tui import theme as ui

        return {"fg": ui.FG, "mute": ui.FG_MUTE, "dim": ui.FG_DIM, "track": ui.BG_4,
                "ok": ui.OK, "warn": ui.WARN, "err": ui.ERR, "accent": ui.ACCENT, "border": ui.BORDER}
    except Exception:
        return {"fg": "bright_white", "mute": "white", "dim": "grey50", "track": "grey23",
                "ok": "green", "warn": "yellow", "err": "red", "accent": "cyan", "border": "grey37"}


def bar(fraction: float, width: int, c: dict[str, str]) -> Text:
    """A thin ``━━━━━━──`` bar: green → amber → red as the limit fills."""
    fraction = max(0.0, min(1.0, fraction))
    filled = round(fraction * width)
    if fraction and not filled:
        filled = 1  # any spend shows
    color = c["ok"] if fraction < 0.6 else c["warn"] if fraction < 0.85 else c["err"]
    out = Text()
    out.append("━" * filled, style=color)
    out.append("━" * (width - filled), style=c["track"])
    return out


def _level_color(info: dict, c: dict[str, str]) -> str:
    return {"ok": c["ok"], "warn": c["warn"], "low": c["err"]}[oru.left_level(info)]


def usage_panel(info: dict) -> Panel:
    """``/usage`` in the transcript: ``usage_body`` in a titled frame."""
    c = _colors()
    return Panel(usage_body(info), title=f"[b {c['accent']}]◎ OpenRouter usage[/]",
                 title_align="left", border_style=c["border"], padding=(1, 2), width=78)


def usage_body(info: dict) -> Group:
    """The numbers without a frame — the panel and the TUI usage dialog share it."""
    c = _colors()
    parts: list = []

    if info.get("status") == "loading" and not info.get("usage"):
        return Group(Text("Asking OpenRouter…", style=c["dim"]))
    if info.get("status") == "error" and not info.get("usage"):
        parts.append(Text(info.get("error") or "Couldn't read OpenRouter usage", style=c["err"]))
        parts.append(Text("Check the key with /provider, or see openrouter.ai/activity", style=c["dim"]))
        return Group(*parts)

    limit = info.get("limit")
    if limit:
        head = Text()
        head.append(oru.money(info.get("remaining")), style=f"bold {_level_color(info, c)}")
        head.append(" left", style=c["fg"])
        head.append(f" of {oru.money(limit)} ", style=c["mute"])
        head.append(f"{info.get('reset') or 'total'} limit", style=c["mute"])
        parts.append(head)
        frac = info.get("fraction") or 0.0
        row = bar(frac, 48, c)
        row.append(f"  {frac * 100:.0f}% used", style=c["dim"])
        parts.append(row)
        sub = Text()
        sub.append(oru.money(info.get("spent")), style=c["fg"])
        sub.append(f" spent {info.get('period') or ''}".rstrip(), style=c["dim"])
        resets = oru.when(info.get("resets_at")) if info.get("reset") else ""
        if resets:
            sub.append(" · resets ", style=c["dim"])
            sub.append(resets, style=c["mute"])
        parts.append(sub)
    else:
        head = Text()
        total = (info.get("usage") or {}).get("total")
        head.append(oru.money(total), style=f"bold {c['fg']}")
        head.append(" spent on this key", style=c["mute"])
        head.append("  · no spending limit", style=c["dim"])
        parts.append(head)
    parts.append(Text(""))

    grid = Table.grid(padding=(0, 2))
    grid.add_column(style=c["dim"], no_wrap=True)
    grid.add_column()

    usage = info.get("usage") or {}
    spend = Text()
    for i, (label, k) in enumerate((("today", "day"), ("week", "week"), ("month", "month"), ("all time", "total"))):
        if i:
            spend.append("   ")
        spend.append(f"{label} ", style=c["dim"])
        spend.append(oru.money(usage.get(k)), style=c["fg"])
    grid.add_row("Spend", spend)

    byok = info.get("byok")
    if byok:
        b = Text()
        b.append(oru.money(byok.get("month")), style=c["fg"])
        b.append(" this month · ", style=c["dim"])
        b.append(oru.money(byok.get("total")), style=c["fg"])
        b.append(" all time (your own provider keys)", style=c["dim"])
        grid.add_row("BYOK", b)

    credits = info.get("credits")
    if credits:
        a = Text()
        bal = credits["balance"]
        a.append(oru.money(bal), style=f"bold {c['err'] if bal < 1 else c['warn'] if bal < 5 else c['fg']}")
        a.append(" credit left", style=c["mute"])
        a.append(f" · {oru.money(credits['used'])} of {oru.money(credits['total'])} used", style=c["dim"])
        grid.add_row("Account", a)

    free = info.get("free_requests")
    if free and (free["used"] or info.get("free_tier") or _on_free_model()):
        f = Text()
        f.append(f"{free['used']:,}", style=c["fg"])
        f.append(f" / {free['limit']:,} free-model requests today", style=c["dim"])
        grid.add_row("Free", f)

    if info.get("expires_at"):
        grid.add_row("Expires", Text(oru.when(info["expires_at"]) or str(info["expires_at"]), style=c["warn"]))
    parts.append(grid)

    foot = Text()
    bits = [b for b in (
        f"key {info['label']}" if info.get("label") else "",
        f"updated {oru.ago(info.get('fetched_at'))}" if info.get("fetched_at") else "",
        "openrouter.ai/activity",
    ) if b]
    foot.append(" · ".join(bits), style=c["dim"])
    if info.get("error"):
        foot.append(f"\n{info['error']} — showing the last numbers", style=c["warn"])
    parts.append(Text(""))
    parts.append(foot)
    return Group(*parts)


def _on_free_model() -> bool:
    try:
        from .. import state
        from ..constants.providers import model_is_free

        return bool(model_is_free(state.MODEL, state.provider or ""))
    except Exception:
        return False


def show_usage(*, refresh: bool = True) -> None:
    """Fetch (unless told not to) and show the panel / web card."""
    if not oru.enabled():
        console.print("[dim]OpenRouter usage is turned off (HARNESS_OPENROUTER_USAGE=0).[/]")
        return
    if not oru.api_key():
        console.print("[dim]Usage is shown for OpenRouter keys — none is saved. "
                      "Add one with[/] /provider openrouter")
        return
    # A key but another provider in use still gets an answer here — only the
    # sidebar / footer wait for OpenRouter to be the provider.
    info = oru.refresh(wait=True) if refresh else oru.public(kick=True, any_provider=True)
    if not info:
        info = {"status": "error", "error": "Couldn't read OpenRouter usage"}
    panel = usage_panel(info)
    show = getattr(console, "show_usage", None)
    if callable(show):
        show(panel, info)
    else:
        console.print(panel)


def handle_usage(c: str, arg: str):
    """Return (handled, None)."""
    if c != "/usage":
        return False, None
    show_usage(refresh=(arg.strip() != "cached"))
    return True, None
