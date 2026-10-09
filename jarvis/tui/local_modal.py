"""``/local-models`` — the terminal dialog for models on this computer.

One list: every running server (Ollama, LM Studio …) with its models, then
the runtimes Jarvis looks for that aren't running (with how to start them),
then "+ Add a server". A detail pane under the list says what the highlighted
row is and what ↵ does; the context bar sets the window Ollama is asked for.

The list paints from the last scan (``local_models.cached``) and rescans in a
worker on open and every few seconds while open — start Ollama with the
dialog up and it appears. Dismisses with ``"use::<provider>::<model>"`` or
``None``; everything else (scan, add, remove, context) happens in place.
"""
from __future__ import annotations

from rich.text import Text
from textual import work
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import CenterMiddle, Vertical
from textual.widgets import Input, OptionList, Static
from textual.widgets.option_list import Option

from .. import state
from ..auth import local_models as lm
from .modal_chrome import (
    TUI_MODAL_CHROME_CSS,
    TuiModalScreen,
    hint_line,
    picker_row,
    relative_time,
    section_header,
)
from .mouse_toggle import disable_mouse, enable_mouse
from . import theme as ui

USE_PREFIX = "use::"
RESCAN_SECONDS = 4.0
_ADD = "__add__"

_TAGS_W = 19  # "  " + "no tools " + "think " + "◩"


def _image_color() -> str:
    try:
        from .model_modal import _image_color as color

        return color()
    except Exception:
        return ui.ACCENT


def model_tags(m: dict) -> Text:
    t = Text("  ", no_wrap=True)
    if m.get("tools") is False:
        t.append("no tools ", style=ui.WARN)
    else:
        t.append(" " * 9)
    t.append("think " if m.get("think") else " " * 6, style=f"bold {ui.ACCENT}")
    t.append("◩" if m.get("vision") else " ", style=f"bold {_image_color()}")
    return t


def _ctx_k(n: int | None) -> str:
    if not n:
        return "?"
    return f"{n // 1024}K" if n >= 1024 else str(n)


class LocalModelsScreen(TuiModalScreen[str | None]):
    DEFAULT_CSS = (
        TUI_MODAL_CHROME_CSS
        + """
    LocalModelsScreen.tui-modal-screen #modal {
        width: 86%;
        max-width: 124;
        height: 88%;
        max-height: 46;
    }
    LocalModelsScreen #lm_status { color: {ui.FG_MUTE}; padding: 0 1; }
    LocalModelsScreen OptionList { height: 1fr; margin-top: 1; }
    LocalModelsScreen #lm_detail {
        height: 4;
        padding: 0 1;
        margin-top: 1;
        border-top: solid {ui.SEP};
        color: {ui.FG_MUTE};
    }
    LocalModelsScreen #lm_ctx { height: 1; padding: 0 1; }
    """
    )

    BINDINGS = [
        Binding("escape", "dismiss_cancel", "Close", show=True),
        Binding("down", "cursor_down", show=False),
        Binding("up", "cursor_up", show=False),
        Binding("r", "rescan", "Rescan", show=False),
        Binding("a", "add", "Add server", show=False),
        Binding("c", "cycle_context", "Context", show=False),
        Binding("x", "remove", "Remove", show=False),
        Binding("o", "open_site", "Website", show=False),
    ]

    def __init__(self) -> None:
        super().__init__()
        self._data: dict = {}
        self._rows: dict[str, tuple[str, dict, dict | None]] = {}  # option id → (kind, server, model)
        self._scanning = False
        self._confirm: str | None = None
        self._note = ""

    def compose(self) -> ComposeResult:
        with CenterMiddle():
            with Vertical(id="modal"):
                yield Static("⌂  Local models", id="modal_title")
                yield Static(
                    f"[{ui.FG_MUTE}]Models running on your own hardware — no key, no cost, "
                    "your code never leaves the machine.[/]",
                    id="modal_subtitle",
                )
                yield Static("", id="lm_status")
                yield OptionList(id="lm_list")
                yield Static("", id="lm_detail")
                yield Static("", id="lm_ctx")
                yield Static(
                    hint_line(("↵", "use / act"), ("r", "rescan"), ("a", "add server"),
                              ("c", "context"), ("x", "remove"), ("o", "website"), ("esc", "close")),
                    id="modal_hint",
                )

    def on_mount(self) -> None:
        enable_mouse()
        self._prev_scroll_y = getattr(self.app, "scroll_sensitivity_y", 1.0)
        try:
            self.app.scroll_sensitivity_y = 1.0
        except AttributeError:
            pass
        self._load()
        self.query_one("#lm_list", OptionList).focus()
        self._scan()
        self.set_interval(RESCAN_SECONDS, self._scan)
        self.set_interval(1.0, self._tick_status)

    def on_unmount(self) -> None:
        disable_mouse()
        try:
            self.app.scroll_sensitivity_y = self._prev_scroll_y
        except AttributeError:
            pass

    # ─── data ──────────────────────────────────────────────────────────

    def _load(self, keep: str | None = None) -> None:
        self._data = lm.public(state.provider or "", state.MODEL or "")
        self._populate(keep)
        self._paint_status()
        self._paint_ctx()

    @work(thread=True, exclusive=True, group="local-scan")
    def _scan(self) -> None:
        if self._scanning:
            return
        self._scanning = True
        self._from_thread(self._paint_status)
        try:
            lm.scan()
        except Exception:
            pass
        self._scanning = False
        self._from_thread(lambda: self._load(self._highlighted()))

    def _from_thread(self, fn) -> None:
        try:
            self.app.call_from_thread(fn)
        except Exception:
            pass

    # ─── painting ──────────────────────────────────────────────────────

    def _paint_status(self) -> None:
        d = self._data or {}
        online, n = d.get("online", 0), d.get("model_count", 0)
        if online:
            head = f"[bold {ui.OK}]●[/] [{ui.FG}]{online} running[/] [{ui.FG_DIM}]·[/] [{ui.FG}]{n} models[/]"
        else:
            head = f"[{ui.FG_DIM}]○ nothing running yet — start one below and it appears here[/]"
        when = d.get("scanned_at") or 0
        tail = (f"[{ui.ACCENT}]⟳[/] [{ui.FG_DIM}]looking…[/]" if self._scanning
                else f"[{ui.FG_DIM}]checked {relative_time(when)}[/]" if when else "")
        line = Text.from_markup(head)
        grid_tail = Text.from_markup(tail)
        from rich.table import Table

        grid = Table.grid(expand=True)
        grid.add_column(ratio=1)
        grid.add_column(justify="right")
        grid.add_row(line, grid_tail)
        try:
            self.query_one("#lm_status", Static).update(grid)
        except Exception:
            pass

    def _tick_status(self) -> None:
        if not self._scanning:
            self._paint_status()

    def _paint_ctx(self) -> None:
        cur = (self._data or {}).get("context") or lm.context_setting()
        parts = [f"[{ui.FG_MUTE}]Context window[/]  "]
        for n in (self._data or {}).get("context_choices") or lm.CONTEXT_CHOICES:
            label = _ctx_k(n)
            if n == cur:
                parts.append(f"[bold {ui.BG_0} on {ui.ACCENT}] {label} [/] ")
            else:
                parts.append(f"[{ui.FG_DIM}] {label} [/] ")
        parts.append(f"  [{ui.FG_DIM}]asked of Ollama · more = more memory · [bold]c[/] to change[/]")
        try:
            self.query_one("#lm_ctx", Static).update("".join(parts))
        except Exception:
            pass

    def _populate(self, keep: str | None = None) -> None:
        opts = self.query_one("#lm_list", OptionList)
        opts.clear_options()
        self._rows.clear()
        servers = (self._data or {}).get("servers") or []
        options: list = []
        first_model: str | None = None
        active_id: str | None = None

        running = [s for s in servers if s["status"] == "online"]
        for s in running:
            ver = f" · v{s['version']}" if s.get("version") else ""
            where = "on this computer" if not s["remote"] else s["host"]
            note = f"{where}{ver} · {len(s['models'])} model{'s' if len(s['models']) != 1 else ''}"
            options.append(section_header(s["name"], note, first=not options))
            if not s["models"]:
                oid = f"empty::{s['id']}"
                self._rows[oid] = ("empty", s, None)
                pull = f" — try: {s['pull']}" if s.get("pull") else ""
                options.append(Option(picker_row("No chat models yet", detail=pull.strip(" —"),
                                                 title_style=ui.FG_DIM), id=oid))
            for m in s["models"]:
                oid = f"{USE_PREFIX}{s['id']}::{m['id']}"
                self._rows[oid] = ("model", s, m)
                first_model = first_model or oid
                if m["active"]:
                    active_id = oid
                detail = " · ".join(x for x in (m["params"], m["quant"]) if x)
                right = []
                if m.get("loaded"):
                    right.append("loaded")
                right.append(f"{_ctx_k(m.get('served_ctx') or m.get('ctx'))} ctx")
                if m.get("size"):
                    right.append(m["size"])
                elif m.get("cloud"):
                    right.append("cloud")
                options.append(Option(picker_row(
                    m["id"], detail=detail, right=" · ".join(right), active=m["active"],
                    title_style=None if m["usable"] else ui.FG_MUTE,
                    tags=model_tags(m), tags_width=_TAGS_W,
                ), id=oid))

        added_down = [s for s in servers if s["custom"] and s["status"] != "online"]
        if added_down:
            options.append(section_header("Your servers", "not answering right now", first=not options))
            for s in added_down:
                oid = f"server::{s['id']}"
                self._rows[oid] = ("server", s, None)
                seen = f"last seen {relative_time(s['last_seen'])}" if s.get("last_seen") else s["error"]
                options.append(Option(picker_row(
                    s["name"], detail=s["host"], right=seen or "offline",
                    icon="○", icon_style=ui.WARN, title_style=ui.FG_MUTE,
                ), id=oid))

        idle = [s for s in servers if not s["custom"] and s["status"] not in ("online",)]
        looking = [s for s in idle if s["status"] != "off"]
        skipped = [s for s in idle if s["status"] == "off"]
        if looking:
            options.append(section_header(
                "Not running" if running else "Get started",
                "start one and it shows up here" if running else "install one, start it — Jarvis finds it",
                first=not options,
            ))
            for s in looking:
                oid = f"runtime::{s['id']}"
                self._rows[oid] = ("runtime", s, None)
                options.append(Option(picker_row(
                    s["name"], detail=s["blurb"], right=s["host"],
                    icon="○", icon_style=ui.FG_DIM, title_style=ui.FG_MUTE,
                ), id=oid))
        if skipped:
            options.append(section_header("Not looking for", "x to look again"))
            for s in skipped:
                oid = f"off::{s['id']}"
                self._rows[oid] = ("off", s, None)
                options.append(Option(picker_row(
                    s["name"], detail="skipped", icon="–", icon_style=ui.FG_DIM, title_style=ui.FG_DIM,
                ), id=oid))

        options.append(section_header("Add", "another computer, a custom port, an API key"))
        self._rows[_ADD] = ("add", {}, None)
        options.append(Option(picker_row("+ Add a server…", detail="Ollama or any OpenAI-compatible URL",
                                         title_style=ui.ACCENT), id=_ADD))
        opts.add_options(options)

        target = keep if keep in self._rows else (active_id or first_model or _ADD)
        try:
            opts.highlighted = opts.get_option_index(target)
        except Exception:
            opts.action_first()
        opts.scroll_to_highlight(top=False)
        self._paint_detail()

    def _highlighted(self) -> str | None:
        try:
            opts = self.query_one("#lm_list", OptionList)
            if opts.highlighted is None:
                return None
            return str(opts.get_option_at_index(opts.highlighted).id or "")
        except Exception:
            return None

    def _paint_detail(self) -> None:
        oid = self._highlighted() or ""
        kind, s, m = self._rows.get(oid, ("", {}, None))
        lines: list[str] = []
        if self._note:
            lines.append(self._note)
        elif kind == "model" and m is not None:
            served, maxc = m.get("served_ctx"), m.get("ctx")
            ctx = (f"uses {_ctx_k(served)} of its {_ctx_k(maxc)} context" if served and maxc and served < maxc
                   else f"{_ctx_k(served or maxc)} context")

            def yn(v):
                return f"[{ui.OK}]✓[/]" if v else (f"[{ui.FG_DIM}]–[/]" if v is False else f"[{ui.FG_DIM}]?[/]")

            lines.append(f"[bold {ui.FG}]{m['id']}[/] [{ui.FG_DIM}]on {s['name']} · {ctx}[/]")
            lines.append(f"tools {yn(m.get('tools'))}   images {yn(m.get('vision'))}   "
                         f"thinking {yn(m.get('think'))}"
                         + (f"   [{ui.WARN}]chat only — it can't run commands or edit files[/]"
                            if m.get("tools") is False else ""))
            lines.append(f"[{ui.FG_DIM}]↵ use this model" + (" · in use now" if m.get("active") else "")
                         + (" · x remove this server" if s.get("custom") else "") + "[/]")
        elif kind == "runtime":
            lines.append(f"[bold {ui.FG}]{s['name']}[/] [{ui.FG_DIM}]isn't running at {s['host']}[/]")
            if s.get("start") or s.get("how"):
                how = f" [{ui.FG_DIM}]{'(' + s['how'] + ')' if s.get('start') else s['how']}[/]" if s.get("how") else ""
                lines.append(f"[{ui.FG_MUTE}]Start:[/] [{ui.ACCENT}]{s.get('start') or ''}[/]{how}")
            lines.append(f"[{ui.FG_DIM}]↵ look again · o get it ({s['site'].split('//')[-1]}) · "
                         "x stop looking for it[/]")
        elif kind == "server":
            lines.append(f"[bold {ui.FG}]{s['name']}[/] [{ui.FG_DIM}]at {s['url']} — {s['error'] or 'offline'}[/]")
            lines.append(f"[{ui.FG_MUTE}]Is that computer on, and the server started?[/]")
            lines.append(f"[{ui.FG_DIM}]↵ look again · x remove[/]")
        elif kind == "off":
            lines.append(f"[{ui.FG_MUTE}]Jarvis doesn't look for {s['name']}.[/]")
            lines.append(f"[{ui.FG_DIM}]↵ or x look for it again[/]")
        elif kind == "empty":
            lines.append(f"[{ui.FG_MUTE}]{s['name']} is running but has no chat models.[/]")
            if s.get("pull"):
                lines.append(f"[{ui.FG_MUTE}]Get one:[/] [{ui.ACCENT}]{s['pull']}[/]")
        elif kind == "add":
            lines.append(f"[{ui.FG_MUTE}]Add a model server Jarvis can't find by itself: another computer "
                         "on your network, a different port, or one behind an API key.[/]")
            lines.append(f"[{ui.FG_DIM}]↵ add · works with Ollama, LM Studio, vLLM, llama.cpp, LocalAI…[/]")
        try:
            self.query_one("#lm_detail", Static).update("\n".join(lines[:3]))
        except Exception:
            pass

    def _flash(self, text: str) -> None:
        self._note = text
        self._paint_detail()
        self.set_timer(3.5, self._clear_note)

    def _clear_note(self) -> None:
        self._note = ""
        self._paint_detail()

    # ─── events ────────────────────────────────────────────────────────

    def on_option_list_option_highlighted(self, _event: OptionList.OptionHighlighted) -> None:
        self._confirm = None
        if not self._note:
            self._paint_detail()

    def on_option_list_option_selected(self, event: OptionList.OptionSelected) -> None:
        self._act(str(event.option.id or ""))

    def on_click(self, event) -> None:
        try:
            if self.query_one("#lm_ctx", Static).region.contains(event.screen_x, event.screen_y):
                self.action_cycle_context()
        except Exception:
            pass

    def _act(self, oid: str) -> None:
        kind, s, m = self._rows.get(oid, ("", {}, None))
        if kind == "model" and m is not None:
            self.dismiss(f"{USE_PREFIX}{s['id']}::{m['id']}")
        elif kind in ("runtime", "server", "empty"):
            self._flash(f"[{ui.ACCENT}]⟳[/] looking for {s['name']}…")
            self._scan()
        elif kind == "off":
            lm.set_detect(s["server"], True)
            self._load(oid.replace("off::", "runtime::"))
            self._scan()
        elif kind == "add":
            self.action_add()

    # ─── actions ───────────────────────────────────────────────────────

    def action_dismiss_cancel(self) -> None:
        self.dismiss(None)

    def action_cursor_down(self) -> None:
        self.query_one("#lm_list", OptionList).action_cursor_down()

    def action_cursor_up(self) -> None:
        self.query_one("#lm_list", OptionList).action_cursor_up()

    def action_rescan(self) -> None:
        self._scan()

    def action_cycle_context(self) -> None:
        choices = list(lm.CONTEXT_CHOICES)
        cur = lm.context_setting()
        nxt = next((c for c in choices if c > cur), choices[0])
        lm.set_context(nxt)
        self._load(self._highlighted())
        self._flash(f"[{ui.OK}]✓[/] Ollama models now get a {_ctx_k(nxt)} window "
                    f"[{ui.FG_DIM}](the model reloads once at the new size)[/]")

    def action_remove(self) -> None:
        oid = self._highlighted() or ""
        kind, s, _m = self._rows.get(oid, ("", {}, None))
        if kind in ("runtime",):
            lm.set_detect(s["server"], False)
            self._load()
            self._flash(f"Jarvis won't look for {s['name']} any more [{ui.FG_DIM}](x on it to undo)[/]")
            return
        if kind == "off":
            self._act(oid)
            return
        if s.get("custom"):
            if self._confirm != s["id"]:
                self._confirm = s["id"]
                self._flash(f"[{ui.WARN}]Remove {s['name']}?[/] press [bold]x[/] again")
                return
            self._confirm = None
            lm.remove_server(s["id"])
            self._load()
            self._flash(f"[{ui.OK}]✓[/] removed {s['name']}")

    def action_open_site(self) -> None:
        kind, s, _m = self._rows.get(self._highlighted() or "", ("", {}, None))
        url = s.get("site") if s else ""
        if not url:
            return
        try:
            import webbrowser

            webbrowser.open(url)
            self._flash(f"opened {url}")
        except Exception:
            self._flash(url)

    def action_add(self) -> None:
        def after(added: object) -> None:
            if isinstance(added, str) and added:
                self._load(None)
                for oid, (kind, s, _m) in self._rows.items():
                    if kind == "model" and s.get("id") == added:
                        try:
                            opts = self.query_one("#lm_list", OptionList)
                            opts.highlighted = opts.get_option_index(oid)
                        except Exception:
                            pass
                        break
                self._flash(f"[{ui.OK}]✓[/] added — its models are below and in /model")

        self.app.push_screen(LocalServerAddScreen(), after)


class LocalServerAddScreen(TuiModalScreen[str | None]):
    """Name + URL + optional key → test → save. Dismisses with the new
    provider id, or None."""

    DEFAULT_CSS = (
        TUI_MODAL_CHROME_CSS
        + """
    LocalServerAddScreen #modal { width: 70%; max-width: 96; }
    LocalServerAddScreen .lm-label { color: {ui.FG_MUTE}; padding: 0 1; margin-top: 1; }
    LocalServerAddScreen Input { margin: 0 1; }
    LocalServerAddScreen #lm_add_status { padding: 0 1; margin-top: 1; height: 2; }
    """
    )

    BINDINGS = [
        Binding("escape", "cancel", "Cancel", show=True),
        Binding("ctrl+s", "save_anyway", "Add without testing", show=False),
    ]

    def __init__(self) -> None:
        super().__init__()
        self._busy = False

    def compose(self) -> ComposeResult:
        with CenterMiddle():
            with Vertical(id="modal"):
                yield Static("⌂  Add a model server", id="modal_title")
                yield Static(f"[{ui.FG_MUTE}]Ollama, LM Studio, vLLM, llama.cpp, LocalAI — anything that "
                             "speaks Ollama's API or OpenAI's.[/]", id="modal_subtitle")
                yield Static("Address", classes="lm-label")
                yield Input(placeholder="http://192.168.1.20:11434", id="lm_url")
                yield Static("Name  [dim](optional)[/]", classes="lm-label")
                yield Input(placeholder="Studio Mac", id="lm_name")
                yield Static("API key  [dim](only if the server asks for one)[/]", classes="lm-label")
                yield Input(placeholder="", password=True, id="lm_key")
                yield Static("", id="lm_add_status")
                yield Static(hint_line(("↵", "test & add"), ("⇥", "next field"),
                                       ("⌃s", "add without testing"), ("esc", "cancel")), id="modal_hint")

    def on_mount(self) -> None:
        enable_mouse()
        self.query_one("#lm_url", Input).focus()

    def on_unmount(self) -> None:
        disable_mouse()

    def on_input_submitted(self, _event: Input.Submitted) -> None:
        self._submit(force=False)

    def action_save_anyway(self) -> None:
        self._submit(force=True)

    def action_cancel(self) -> None:
        self.dismiss(None)

    def _status(self, text: str) -> None:
        try:
            self.query_one("#lm_add_status", Static).update(text)
        except Exception:
            pass

    def _submit(self, *, force: bool) -> None:
        if self._busy:
            return
        url = self.query_one("#lm_url", Input).value.strip()
        if not url:
            self._status(f"[{ui.WARN}]Type the server's address first.[/]")
            self.query_one("#lm_url", Input).focus()
            return
        self._busy = True
        self._status(f"[{ui.ACCENT}]⟳[/] [{ui.FG_MUTE}]checking {url}…[/]")
        self._run_add(url, self.query_one("#lm_name", Input).value,
                      self.query_one("#lm_key", Input).value, force)

    @work(thread=True, exclusive=True)
    def _run_add(self, url: str, name: str, key: str, force: bool) -> None:
        try:
            res = lm.add_server(name, url, key=key, force=force)
        except Exception as e:
            res = {"ok": False, "error": str(e)}
        self.app.call_from_thread(self._done, res)

    def _done(self, res: dict) -> None:
        self._busy = False
        if res.get("ok"):
            self.dismiss(res.get("id") or "")
            return
        self._status(f"[{ui.ERR}]✗[/] {res.get('error') or 'Could not add it'}\n"
                     f"[{ui.FG_DIM}]Fix the address and press ↵, or ⌃s to add it anyway.[/]")
