"""Modal model picker — replaces console.input-based /model flow in the TUI."""
from __future__ import annotations

import re

from rich.text import Text
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
    is_catalog_provider, provider_label,
)
from ..constants.providers import (
    HARNESS_AGENT_FALLBACK_MODEL, VISION_SEARCH_WORDS, free_model_ids,
    harness_agent_models_for_picker, model_is_free, model_sees_images,
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


def _harness_rows() -> list[tuple[str, str, str]]:
    """Harness Agent rows from the live list (never empty: before any catalog
    arrives, the fallback model alone) — so /model always lists the free tier."""
    try:
        rows = harness_agent_models_for_picker()
    except Exception:
        rows = []
    return [(PROVIDER_HARNESS_AGENT, mid, desc)
            for mid, desc in rows or [(HARNESS_AGENT_FALLBACK_MODEL, "Big Pickle")]]


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
    harness = _harness_rows()
    seen = {mid for _, mid, _ in harness}
    extra = [(src, mid, desc) for src, mid, desc in rows if mid not in seen]
    return harness + extra


# ─── Tags: free to use · can see images (same rules as the web picker) ─────

IMAGE_MARK = "◩"
_FREE_TAIL = re.compile(r"(?:,\s*|\s+[—–-]\s+|\s+|^)free\s*$", re.IGNORECASE)


def _free_less(desc: str) -> str:
    """"1M ctx, free" → "1M ctx", "Big Model Free" → "Big Model" — the tag says it now."""
    return _FREE_TAIL.sub("", desc or "").strip()


def _image_color() -> str:
    """Sky blue, darker on a light palette (as on the web)."""
    bg = (ui.BG_0 or "#000000").lstrip("#")
    try:
        r, g, b = (int(bg[i:i + 2], 16) for i in (0, 2, 4))
    except ValueError:
        return "#38bdf8"
    return "#0284c7" if (0.299 * r + 0.587 * g + 0.114 * b) > 150 else "#38bdf8"


def _source_label(src: str) -> str:
    """Group / row label for a model source ("Anthropic API", not "anthropic_api")."""
    if src == PROVIDER_HARNESS_AGENT:
        return "Harness Agent"
    return MODEL_SOURCE_LABELS.get(src) or provider_label(src)


_FREE_TAG = "  free"
_IMAGE_TAG = f"  {IMAGE_MARK}"


def tags_width(free_slot: bool, image_slot: bool) -> int:
    """Width of the tag column: room for each tag some row in the list has."""
    return (len(_FREE_TAG) if free_slot else 0) + (len(_IMAGE_TAG) if image_slot else 0)


def model_tags(*, free: bool, images: bool, free_slot: bool = False) -> Text:
    """``  free  ◩`` for a model row's tag column (``picker_row(tags=…)``).

    A free slot stays blank on rows without it, so the image marks line up too.
    """
    out = Text(no_wrap=True)
    if free:
        out.append(_FREE_TAG, style=f"bold {ui.OK}")
    elif free_slot:
        out.append(" " * len(_FREE_TAG))
    if images:
        out.append(_IMAGE_TAG, style=f"bold {_image_color()}")
    return out


# Option id of a "connect a provider" row ("__connect__:" + provider id, or
# bare for the generic "Add a provider" row). Dismissed as-is; the app opens
# the /key dialog for it.
CONNECT_ID = "__connect__:"


def _unconnected_catalog_providers() -> list[tuple[str, str, int]]:
    """(provider id, name, model count) for models.dev providers with no key."""
    try:
        from ..auth import models_dev
        from ..constants.providers import catalog_connected_providers

        connected = set(catalog_connected_providers())
        return sorted(
            (
                (pid, p.name, len(p.models))
                for pid, p in models_dev.providers().items()
                if pid not in connected
            ),
            key=lambda r: r[1].lower(),
        )
    except Exception:
        return []


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
                              ("type", "to search"), ("esc", "close"))
                    + f"   [bold {ui.OK}]free[/] [{ui.FG_DIM}]no cost[/]"
                    + f"   [bold {_image_color()}]{IMAGE_MARK}[/] [{ui.FG_DIM}]sees images[/]",
                    id="modal_hint",
                )

    @staticmethod
    def _subtitle(busy: bool = False) -> str:
        from ..constants.providers import PROVIDER_LABELS, provider_label  # noqa: F401

        prov = provider_label(state.provider or "")
        line = (
            f"[{ui.FG_DIM}]current[/] [bold {ui.FG}]{state.MODEL}[/]"
            + (f" [{ui.FG_DIM}]· {prov}[/]" if prov else "")
        )
        if busy:
            line += f"   [{ui.ACCENT}]⟳[/] [{ui.FG_DIM}]refreshing model catalogs…[/]"
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
        # A first models.dev fetch can bring providers to connect even when no
        # model row changed.
        before = getattr(self, "_unconnected", None)
        self._unconnected = _unconnected_catalog_providers()
        catalog_changed = before is not None and before != self._unconnected
        if not rows or rows == getattr(self, "_all_rows", None):
            if not catalog_changed:
                return
            rows = getattr(self, "_all_rows", None) or rows
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
            harness = _harness_rows()
            seen = {mid for _, mid, _ in rows}
            rows = [r for r in harness if r[1] not in seen] + rows

        free_ids = free_model_ids()
        tags = {(src, m): (model_is_free(m, src, free_ids), model_sees_images(m, src))
                for src, m, _d in rows}
        groups: dict[str, list[tuple[str, str]]] = {}
        for src, m, desc in rows:
            label = _source_label(src)
            free, images = tags[(src, m)]
            if q == "free":  # "free" / "vision" list only the tagged models
                if not free:
                    continue
            elif q in VISION_SEARCH_WORDS:
                if not images:
                    continue
            elif q and not any(q in part.lower() for part in (m, desc, label)):
                if q not in ("harness", "agent") or src != PROVIDER_HARNESS_AGENT:
                    continue
            groups.setdefault(src, []).append((m, desc))
        shown = [tags[(src, m)] for src, items in groups.items() for m, _d in items]
        free_slot = any(f for f, _i in shown)
        tag_w = tags_width(free_slot, any(i for _f, i in shown))

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
                    label = _source_label(src)
                    free, images = tags[(src, m)]
                    options.append(Option(
                        picker_row(m, right=label, active=self._is_active(src, m),
                                   tags=model_tags(free=free, images=images, free_slot=free_slot),
                                   tags_width=tag_w),
                        id=f"recent:{oid}",
                    ))
                recent_first = f"recent:{recent[0]}"
        for i, (src, items) in enumerate(groups.items()):
            label = _source_label(src)
            note = "free · no key needed" if src == PROVIDER_HARNESS_AGENT else f"{len(items)} models"
            if is_catalog_provider(src):
                note += " · models.dev"
            options.append(section_header(label, note, first=i == 0 and not options))
            for m, desc in items:
                active = self._is_active(src, m)
                oid = model_option_id(src, m)
                if active:
                    active_id = oid
                desc_short = desc.split(" — ", 1)[-1] if " — " in desc else desc
                free, images = tags[(src, m)]
                if free:
                    desc_short = _free_less(desc_short)
                options.append(Option(
                    picker_row(m, right=desc_short[:48], active=active, query=q,
                               tags=model_tags(free=free, images=images, free_slot=free_slot),
                               tags_width=tag_w),
                    id=oid,
                ))
        connect = self._connect_rows(q, has_models=bool(options))
        if not options and not connect:
            opts.add_option(empty_row(f"No models match “{query.strip()}”"))
            return
        if not options:
            options.append(empty_row(f"No connected models match “{query.strip()}”"))
        options.extend(connect)
        opts.add_options(options)
        target = keep or (recent_first or active_id if not q else None)
        try:
            opts.highlighted = opts.get_option_index(target) if target else None
        except Exception:
            opts.highlighted = None
        if opts.highlighted is None:
            opts.action_first()
        opts.scroll_to_highlight(top=False)

    def _connect_rows(self, q: str, *, has_models: bool) -> list:
        """Rows that connect a provider: one per not-yet-connected models.dev
        provider matching the search, or a single "Add a provider" row."""
        unconnected = getattr(self, "_unconnected", None)
        if unconnected is None:
            unconnected = self._unconnected = _unconnected_catalog_providers()
        out: list = []
        if q:
            hits = [
                (pid, name, n) for pid, name, n in unconnected
                if q in name.lower() or q in pid.split(":", 1)[-1].lower()
            ][:6]
            if hits:
                out.append(section_header("Connect a provider", "add an API key · models.dev",
                                          first=not has_models))
                for pid, name, n in hits:
                    out.append(Option(
                        picker_row(f"+ {name}", right=f"{n} models", query=q,
                                   title_style=ui.ACCENT),
                        id=f"{CONNECT_ID}{pid}",
                    ))
            return out
        if unconnected:
            out.append(section_header("More providers", f"{len(unconnected)} via models.dev"))
            out.append(Option(
                picker_row("+ Add a provider", detail="type its name to search",
                           right="API keys", title_style=ui.ACCENT),
                id=CONNECT_ID,
            ))
        return out

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
        if oid.startswith(CONNECT_ID):
            self.dismiss(oid)  # the app opens /key for it — not a model pick
            return
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
