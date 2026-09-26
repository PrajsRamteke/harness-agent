"""Modal model picker — replaces console.input-based /model flow in the TUI."""
from __future__ import annotations

from textual import work
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import CenterMiddle, Vertical
from textual.widgets import Input, OptionList, Static
from textual.widgets.option_list import Option

from ..constants import (
    MODEL_SOURCE_LABELS, all_model_picker_rows,
    model_option_id, PROVIDER_HARNESS_AGENT,
    PROVIDER_ANTHROPIC, PROVIDER_ANTHROPIC_API, PROVIDER_ANTHROPIC_AUTH,
    PROVIDER_OPENAI_CODEX, PROVIDER_OPENAI_CODEX_AUTH,
    PROVIDER_OPENCODE_ZEN,
    AUTH_API_KEY, AUTH_OAUTH,
)
from .. import state
from .modal_chrome import (
    TUI_MODAL_CHROME_CSS,
    TuiModalScreen,
    empty_row,
    hint_line,
    picker_row,
    section_header,
)
from .mouse_toggle import enable_mouse, disable_mouse
from . import theme as ui

# Hard-coded so /model always lists Harness Agent even on stale installs (pre-pip-sync).
_BUILTIN_HARNESS_ROWS: tuple[tuple[str, str], ...] = (
    ("mimo-v2.5-free", "MiMo V2.5 Free — default"),
    ("nemotron-3-ultra-free", "Nemotron 3 Ultra Free"),
    ("big-pickle", "Big Pickle"),
    ("nemotron-3.5-lightning-free", "Nemotron 3.5 Lightning Free"),
    ("muse-spark-1.2-contributor-free", "Muse Spark 1.2 Free"),
    ("muse-spark-1.3-contributor-free", "Muse Spark 1.3 Free"),
    ("ling-3.0-flash-fin-free", "Ling 3.0 Flash Fin Free"),
)


def model_picker_rows(live: bool = False) -> list[tuple[str, str, str]]:
    """(source, model_id, description) rows — Harness Agent guaranteed first.

    ``live=False`` (the default, and what the picker uses on open) reads the
    on-disk catalog cache, so building the list never touches the network and
    the modal appears immediately. ``live=True`` refreshes over the network
    first — call it from a worker thread, never from the UI thread.

    Either way the free models discovered from OpenCode Zen and OpenRouter are
    included, so newly released free models show up without a code change.
    """
    try:
        rows = all_model_picker_rows(live=live, cached=not live)
        if any(src == PROVIDER_HARNESS_AGENT for src, _, _ in rows):
            return rows
    except Exception:
        rows = []
    harness = [
        (PROVIDER_HARNESS_AGENT, mid, desc)
        for mid, desc in _BUILTIN_HARNESS_ROWS
    ]
    seen = {mid for _, mid, _ in harness}
    extra = [(src, mid, desc) for src, mid, desc in rows if mid not in seen]
    return harness + extra


def _recent_models() -> list[str]:
    try:
        from ..storage.settings import get_settings

        val = get_settings().get("ui.recent_models") or []
        return [str(v) for v in val if isinstance(v, str)]
    except Exception:
        return []


def _remember_model(option_id: str) -> None:
    """Keep the last 5 picked models (option ids) for the Recent group."""
    try:
        from ..storage.settings import get_settings

        recent = [option_id] + [r for r in _recent_models() if r != option_id]
        get_settings().set("ui.recent_models", recent[:5])
    except Exception:
        pass


class ModelPickerScreen(TuiModalScreen[str | None]):
    """Lists configured models. Dismisses with the selected model id, or None."""

    DEFAULT_CSS = (
        TUI_MODAL_CHROME_CSS
        + """
    ModelPickerScreen.tui-modal-screen #modal {
        width: 82%;
        max-width: 120;
        height: 85%;
        max-height: 44;
    }
    ModelPickerScreen OptionList {
        height: 1fr;
    }
    """
    )

    BINDINGS = [
        Binding("escape", "dismiss_cancel", "Cancel", show=True),
        Binding("enter", "accept_selection", "Select", show=True),
        Binding("down", "cursor_down", show=False),
        Binding("up", "cursor_up", show=False),
        Binding("pagedown", "page_down", show=False),
        Binding("pageup", "page_up", show=False),
        Binding("slash", "focus_search", "Search", show=True),
    ]

    def compose(self) -> ComposeResult:
        with CenterMiddle():
            with Vertical(id="modal"):
                yield Static("✦  Select model", id="modal_title")
                yield Static(self._subtitle(), id="model_subtitle")
                yield Input(value="", placeholder="Search models…", id="model_search")
                yield OptionList(id="model_list")
                yield Static(
                    hint_line(("↑↓", "navigate"), ("↵", "select"),
                              ("type", "to search"), ("esc", "close")),
                    id="modal_hint",
                )

    @staticmethod
    def _subtitle(busy: bool = False) -> str:
        from ..constants.providers import PROVIDER_LABELS

        prov = PROVIDER_LABELS.get(state.provider, state.provider or "")
        line = (
            f"[{ui.FG_DIM}]current[/] [bold {ui.FG}]{state.MODEL}[/]"
            + (f" [{ui.FG_DIM}]· {prov}[/]" if prov else "")
        )
        if busy:
            line += f"   [{ui.ACCENT}]⟳[/] [{ui.FG_DIM}]refreshing free models…[/]"
        return line

    def on_mount(self) -> None:
        enable_mouse()
        self._prev_scroll_y = self.app.scroll_sensitivity_y
        self.app.scroll_sensitivity_y = 1.0
        # Cached rows only — never a network call on the UI thread, so the
        # modal paints immediately. The live refresh runs below in a worker.
        try:
            self._all_rows = model_picker_rows()
        except Exception:
            self._all_rows = []
        self._populate()
        self.query_one("#model_search", Input).focus()
        self._refresh_catalogs()

    # ─── background catalog refresh ────────────────────────────────────
    @work(thread=True, exclusive=True)
    def _refresh_catalogs(self) -> None:
        """Pull the live free-model catalogs, then swap the rows in place.

        Runs off the UI thread: opening /model stays instant even on a cold
        cache or a slow link, and newly released free models appear a moment
        later without the user reopening the picker.
        """
        from ..constants.providers import (
            model_catalogs_are_fresh,
            refresh_model_catalogs,
        )

        if model_catalogs_are_fresh():
            return
        self._from_thread(lambda: self._set_busy(True))
        rows: list[tuple[str, str, str]] | None = None
        try:
            if refresh_model_catalogs():
                rows = model_picker_rows()
        except Exception:
            rows = None
        self._from_thread(lambda r=rows: self._apply_refreshed_rows(r))

    def _from_thread(self, fn) -> None:
        """call_from_thread that tolerates the picker being closed mid-refresh."""
        try:
            self.app.call_from_thread(fn)
        except Exception:
            pass

    def _set_busy(self, busy: bool) -> None:
        try:
            self.query_one("#model_subtitle", Static).update(self._subtitle(busy))
        except Exception:
            pass

    def _apply_refreshed_rows(self, rows) -> None:
        self._set_busy(False)
        if not rows or rows == getattr(self, "_all_rows", None):
            return
        self._all_rows = rows
        try:
            query = self.query_one("#model_search", Input).value or ""
        except Exception:
            query = ""
        self._populate(query, keep=self._highlighted_option_id())

    def _highlighted_option_id(self) -> str | None:
        try:
            opts = self.query_one("#model_list", OptionList)
            if opts.highlighted is None:
                return None
            return opts.get_option_at_index(opts.highlighted).id
        except Exception:
            return None

    def on_unmount(self) -> None:
        disable_mouse()
        try:
            self.app.scroll_sensitivity_y = self._prev_scroll_y
        except AttributeError:
            pass

    def _is_active(self, source: str, model_id: str) -> bool:
        if model_id != state.MODEL:
            return False
        if source == PROVIDER_HARNESS_AGENT:
            return state.provider == PROVIDER_OPENCODE_ZEN and state.harness_agent_free
        if source == PROVIDER_OPENCODE_ZEN:
            return state.provider == PROVIDER_OPENCODE_ZEN and not state.harness_agent_free
        if source == PROVIDER_ANTHROPIC_AUTH:
            return state.provider == PROVIDER_ANTHROPIC and state.auth_mode == AUTH_OAUTH
        if source == PROVIDER_ANTHROPIC_API:
            return state.provider == PROVIDER_ANTHROPIC and state.auth_mode == AUTH_API_KEY
        if source == PROVIDER_OPENAI_CODEX_AUTH:
            return state.provider == PROVIDER_OPENAI_CODEX and state.auth_mode == AUTH_OAUTH
        return state.provider == source

    def _populate(self, query: str = "", keep: str | None = None) -> None:
        q = query.strip().lower()
        opts = self.query_one("#model_list", OptionList)
        opts.clear_options()
        rows = list(getattr(self, "_all_rows", []))
        # Never show an empty picker — Harness Agent free tier is always first.
        if not any(src == PROVIDER_HARNESS_AGENT for src, _, _ in rows):
            harness = [
                (PROVIDER_HARNESS_AGENT, mid, desc)
                for mid, desc in _BUILTIN_HARNESS_ROWS
            ]
            seen = {mid for _, mid, _ in rows}
            rows = [r for r in harness if r[1] not in seen] + rows

        groups: dict[str, list[tuple[str, str]]] = {}
        for src, m, desc in rows:
            label = "Harness Agent" if src == PROVIDER_HARNESS_AGENT else MODEL_SOURCE_LABELS.get(src, src)
            if q and not any(q in part.lower() for part in (m, desc, label)):
                if q not in ("harness", "agent", "free") or src != PROVIDER_HARNESS_AGENT:
                    continue
            groups.setdefault(src, []).append((m, desc))

        options = []
        active_id: str | None = None
        recent_first: str | None = None
        if not q:
            # Recent: the current model + the last few picks (unique ids —
            # "recent:" prefix, stripped again on select).
            by_id = {model_option_id(src, m): (src, m, desc) for src, m, desc in rows}
            recent: list[str] = []
            for src, m, _d in rows:
                if self._is_active(src, m):
                    recent.append(model_option_id(src, m))
            for oid in _recent_models():
                if oid in by_id and oid not in recent:
                    recent.append(oid)
            recent = recent[:5]
            if recent:
                options.append(section_header("Recent", first=True))
                for oid in recent:
                    src, m, desc = by_id[oid]
                    label = "Harness Agent" if src == PROVIDER_HARNESS_AGENT else MODEL_SOURCE_LABELS.get(src, src)
                    options.append(Option(
                        picker_row(m, right=label, active=self._is_active(src, m)),
                        id=f"recent:{oid}",
                    ))
                recent_first = f"recent:{recent[0]}"
        for i, (src, items) in enumerate(groups.items()):
            label = "Harness Agent" if src == PROVIDER_HARNESS_AGENT else MODEL_SOURCE_LABELS.get(src, src)
            note = "free · no key needed" if src == PROVIDER_HARNESS_AGENT else f"{len(items)} models"
            options.append(section_header(label, note, first=i == 0 and not options))
            for m, desc in items:
                active = self._is_active(src, m)
                oid = model_option_id(src, m)
                if active:
                    active_id = oid
                desc_short = desc.split(" — ", 1)[-1] if " — " in desc else desc
                options.append(Option(
                    picker_row(m, right=desc_short[:48], active=active, query=q),
                    id=oid,
                ))
        if not options:
            opts.add_option(empty_row(f"No models match “{query.strip()}”"))
            return
        opts.add_options(options)
        target = keep or (recent_first or active_id if not q else None)
        try:
            opts.highlighted = opts.get_option_index(target) if target else None
        except Exception:
            opts.highlighted = None
        if opts.highlighted is None:
            opts.action_first()
        opts.scroll_to_highlight(top=False)

    # ─── events ────────────────────────────────────────────────────────
    def on_input_changed(self, event: Input.Changed) -> None:
        if event.input.id == "model_search":
            self._populate(event.value or "")

    def on_input_submitted(self, event: Input.Submitted) -> None:
        if event.input.id == "model_search":
            self._accept()  # Enter in the search box picks the highlighted model

    def on_option_list_option_selected(self, event: OptionList.OptionSelected) -> None:
        oid = event.option.id
        if not oid or oid == "__none__":
            return
        self._choose(str(oid))

    def _choose(self, oid: str) -> None:
        oid = oid.removeprefix("recent:")
        _remember_model(oid)
        self.dismiss(oid)

    # ─── actions ───────────────────────────────────────────────────────
    def action_dismiss_cancel(self) -> None:
        try:
            sb = self.query_one("#model_search", Input)
            if sb.value:
                sb.value = ""
                self._populate()
                sb.focus()
                return
        except Exception:
            pass
        self.dismiss(None)

    def action_focus_search(self) -> None:
        self.query_one("#model_search", Input).focus()

    def action_cursor_down(self) -> None:
        self.query_one("#model_list", OptionList).action_cursor_down()

    def action_cursor_up(self) -> None:
        self.query_one("#model_list", OptionList).action_cursor_up()

    def action_page_down(self) -> None:
        self.query_one("#model_list", OptionList).action_page_down()

    def action_page_up(self) -> None:
        self.query_one("#model_list", OptionList).action_page_up()

    def action_accept_selection(self) -> None:
        self._accept()

    def _accept(self) -> None:
        opts = self.query_one("#model_list", OptionList)
        if opts.option_count == 0 or opts.highlighted is None:
            self.dismiss(None)
            return
        try:
            opt = opts.get_option_at_index(opts.highlighted)
        except Exception:
            self.dismiss(None)
            return
        if not opt.id or opt.id == "__none__":
            self.dismiss(None)
            return
        self._choose(str(opt.id))
