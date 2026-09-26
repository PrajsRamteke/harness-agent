"""Centered command palette modal (OpenCode / Spotlight style)."""
from __future__ import annotations

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import CenterMiddle, Vertical
from textual.widgets import Input, OptionList, Static
from textual.widgets.option_list import Option


from .commands_catalog import filter_commands
from .modal_chrome import (
    TUI_MODAL_CHROME_CSS,
    TuiModalScreen,
    empty_row,
    hint_line,
    picker_row,
    section_header,
)

# Browse view groups (search view is flat). Unlisted commands land in "Other".
_GROUPS: list[tuple[str, list[str]]] = [
    ("Session", ["/new", "/session", "/retry", "/history", "/export ",
                 "/copy", "/paste", "/clear", "/reset", "/exit"]),
    ("Model & agent", ["/model", "/provider", "/think", "/agent", "/agent init",
                       "/plan", "/auto", "/verbose"]),
    ("Context & memory", ["/pin", "/unpin", "/memory", "/lesson", "/skill", "/scan"]),
    ("Tools", ["/local", "/mcp", "/command", "/loop"]),
    ("App", ["/theme", "/sidebar", "/settings", "/stats",
             "/upgrade", "/version", "/help"]),
]
from .mouse_toggle import enable_mouse, disable_mouse


class CommandPaletteScreen(TuiModalScreen[str | None]):
    """Centered overlay. Dismisses with the selected command string, or None."""

    DEFAULT_CSS = (
        TUI_MODAL_CHROME_CSS
        + """
    CommandPaletteScreen.tui-modal-screen #modal {
        width: 80%;
        max-width: 110;
        height: 80%;
        max-height: 40;
    }
    CommandPaletteScreen OptionList {
        height: 1fr;
    }
    """
    )

    BINDINGS = [
        Binding("escape", "cancel", "Cancel", show=True),
        Binding("down", "cursor_down", show=False),
        Binding("up", "cursor_up", show=False),
        Binding("pagedown", "page_down", show=False),
        Binding("pageup", "page_up", show=False),
    ]

    def __init__(self, initial: str = "/"):
        super().__init__()
        self._initial = initial or "/"

    def compose(self) -> ComposeResult:
        with CenterMiddle():
            with Vertical(id="modal"):
                yield Static("⌘  Commands", id="modal_title")
                yield Input(value=self._initial, placeholder="Search commands…", id="palette_input")
                yield OptionList(id="palette_options")
                yield Static(
                    hint_line(("↑↓", "navigate"), ("↵", "run"), ("esc", "close")),
                    id="modal_hint",
                )

    def on_mount(self):
        enable_mouse()
        self._prev_scroll_y = self.app.scroll_sensitivity_y
        self.app.scroll_sensitivity_y = 1.0
        self._refresh(self._initial)
        inp = self.query_one("#palette_input", Input)
        inp.focus()
        inp.cursor_position = len(inp.value)

    def on_unmount(self):
        disable_mouse()
        try:
            self.app.scroll_sensitivity_y = self._prev_scroll_y
        except AttributeError:
            pass

    def _refresh(self, query: str):
        opts = self.query_one("#palette_options", OptionList)
        opts.clear_options()
        q = (query or "").strip()
        bare = q.lstrip("/").lower()
        matches = filter_commands(query)
        if not matches:
            opts.add_option(empty_row(f"No commands match “{q}”"))
            return
        if not bare:
            # Browsing: grouped by category.
            by_cmd = {c: d for c, d in matches}
            placed: set[str] = set()
            options = []
            for gi, (group, cmds) in enumerate(_GROUPS):
                rows = [c for c in cmds if c in by_cmd]
                if not rows:
                    continue
                options.append(section_header(group, first=not options))
                for c in rows:
                    placed.add(c)
                    options.append(Option(picker_row(c.strip(), detail=by_cmd[c], title_width=13), id=c))
            rest = [(c, d) for c, d in matches if c not in placed]
            custom = [(c, d) for c, d in rest if d.startswith("⌘ custom")]
            other = [(c, d) for c, d in rest if not d.startswith("⌘ custom")]
            for label, rows in (("Custom commands", custom), ("Other", other)):
                if rows:
                    options.append(section_header(label, first=not options))
                    for c, d in rows:
                        options.append(Option(
                            picker_row(c.strip(), detail=d.replace("⌘ custom ", ""), title_width=13), id=c,
                        ))
            opts.add_options(options)
        else:
            # Searching: best matches first, flat, matches accented.
            starts = [(c, d) for c, d in matches if c.strip().lower().lstrip("/").startswith(bare)]
            rest = [(c, d) for c, d in matches if (c, d) not in starts]
            for c, d in starts + rest:
                opts.add_option(Option(picker_row(c.strip(), detail=d, query=bare, title_width=13), id=c))
        opts.action_first()

    # ─── events ────────────────────────────────────────────────────────
    def on_input_changed(self, event: Input.Changed) -> None:
        if event.input.id == "palette_input":
            self._refresh(event.value or "")

    def on_input_submitted(self, event: Input.Submitted) -> None:
        self._accept()

    def on_option_list_option_selected(self, event: OptionList.OptionSelected) -> None:
        self.dismiss(event.option.id)

    # ─── actions ───────────────────────────────────────────────────────
    def action_cancel(self):
        self.dismiss(None)

    def action_cursor_down(self):
        self.query_one("#palette_options", OptionList).action_cursor_down()

    def action_cursor_up(self):
        self.query_one("#palette_options", OptionList).action_cursor_up()

    def action_page_down(self):
        self.query_one("#palette_options", OptionList).action_page_down()

    def action_page_up(self):
        self.query_one("#palette_options", OptionList).action_page_up()


    def _accept(self):
        opts = self.query_one("#palette_options", OptionList)
        if opts.option_count == 0 or opts.highlighted is None:
            self.dismiss(None)
            return
        if opts.get_option_at_index(opts.highlighted).disabled:
            return
        try:
            opt = opts.get_option_at_index(opts.highlighted)
        except Exception:
            self.dismiss(None)
            return
        self.dismiss(opt.id)
