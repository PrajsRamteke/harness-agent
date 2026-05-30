"""Single source of truth for the Jarvis TUI's visual language.

Every widget, modal, and rendered panel pulls colors and spacing from the
constants here. Changing a token in one place changes the entire UI — keep
this file disciplined.

Themes
------
Four built-in themes (red, blue, purple, green) differ in accent and status
colors. Backgrounds remain the same GitHub Dark palette.

  - ``set_theme(name)``  — switch all tokens + CSS at runtime
  - ``_build_global_css()`` / ``_build_modal_css()``  — rebuild CSS strings

Other modules that need theme-aware colors at runtime should either:
  * ``from . import theme as ui``  → ``ui.OK``, ``ui.ACCENT_2``, etc.
  * Cache selectively with a reference to the module object, not individual constants.
"""
from __future__ import annotations

import typing as t


# ── Full palette definitions ─────────────────────────────────────────────

Palette = dict[str, str]

PALETTES: dict[str, Palette] = {
    "red": {
        # Backgrounds (same across all themes)
        "bg_0": "#0b0f15",
        "bg_1": "#11161d",
        "bg_2": "#161c24",
        "bg_3": "#1c232c",
        "bg_4": "#232b36",
        # Borders
        "border": "#2a323d",
        "border_fc": "#f97583",
        # Foreground
        "fg": "#e6edf3",
        "fg_mute": "#9aa4b1",
        "fg_dim": "#6b7684",
        "sep": "#1f2630",
        # Status
        "ok": "#56d364",
        "warn": "#e3b341",
        "err": "#f85149",
        # Accents — warm red / coral
        "accent": "#f97583",
        "accent_2": "#ff7b72",
        "accent_3": "#ffa198",
    },
    "blue": {
        "bg_0": "#0b0f15",
        "bg_1": "#11161d",
        "bg_2": "#161c24",
        "bg_3": "#1c232c",
        "bg_4": "#232b36",
        "border": "#2a323d",
        "border_fc": "#58a6ff",
        "fg": "#e6edf3",
        "fg_mute": "#9aa4b1",
        "fg_dim": "#6b7684",
        "sep": "#1f2630",
        "ok": "#3fb950",
        "warn": "#e3b341",
        "err": "#f85149",
        "accent": "#58a6ff",
        "accent_2": "#56d4dd",
        "accent_3": "#79f0ff",
    },
    "purple": {
        "bg_0": "#0b0f15",
        "bg_1": "#11161d",
        "bg_2": "#161c24",
        "bg_3": "#1c232c",
        "bg_4": "#232b36",
        "border": "#2a323d",
        "border_fc": "#bc8cff",
        "fg": "#e6edf3",
        "fg_mute": "#9aa4b1",
        "fg_dim": "#6b7684",
        "sep": "#1f2630",
        "ok": "#3fb950",
        "warn": "#d29922",
        "err": "#f85149",
        "accent": "#79c0ff",
        "accent_2": "#bc8cff",
        "accent_3": "#f0b3ff",
    },
    "green": {
        "bg_0": "#0b0f15",
        "bg_1": "#11161d",
        "bg_2": "#161c24",
        "bg_3": "#1c232c",
        "bg_4": "#232b36",
        "border": "#2a323d",
        "border_fc": "#56d364",
        "fg": "#e6edf3",
        "fg_mute": "#9aa4b1",
        "fg_dim": "#6b7684",
        "sep": "#1f2630",
        "ok": "#3fb950",
        "warn": "#e3b341",
        "err": "#f85149",
        "accent": "#56d364",
        "accent_2": "#56d4dd",
        "accent_3": "#a3f0bf",
    },
    "orange": {
        "bg_0": "#0b0f15",
        "bg_1": "#11161d",
        "bg_2": "#161c24",
        "bg_3": "#1c232c",
        "bg_4": "#232b36",
        "border": "#2a323d",
        "border_fc": "#f0883e",
        "fg": "#e6edf3",
        "fg_mute": "#9aa4b1",
        "fg_dim": "#6b7684",
        "sep": "#1f2630",
        "ok": "#56d364",
        "warn": "#d29922",
        "err": "#f85149",
        "accent": "#f0883e",
        "accent_2": "#ffa657",
        "accent_3": "#fec77d",
    },
    "yellow": {
        "bg_0": "#0b0f15",
        "bg_1": "#11161d",
        "bg_2": "#161c24",
        "bg_3": "#1c232c",
        "bg_4": "#232b36",
        "border": "#2a323d",
        "border_fc": "#d29922",
        "fg": "#e6edf3",
        "fg_mute": "#9aa4b1",
        "fg_dim": "#6b7684",
        "sep": "#1f2630",
        "ok": "#56d364",
        "warn": "#e3b341",
        "err": "#f85149",
        "accent": "#d29922",
        "accent_2": "#e3b341",
        "accent_3": "#f0d272",
    },
    "rose": {
        "bg_0": "#0b0f15",
        "bg_1": "#11161d",
        "bg_2": "#161c24",
        "bg_3": "#1c232c",
        "bg_4": "#232b36",
        "border": "#2a323d",
        "border_fc": "#f7527a",
        "fg": "#e6edf3",
        "fg_mute": "#9aa4b1",
        "fg_dim": "#6b7684",
        "sep": "#1f2630",
        "ok": "#56d364",
        "warn": "#e3b341",
        "err": "#f85149",
        "accent": "#f7527a",
        "accent_2": "#ff7b9a",
        "accent_3": "#ffb3c6",
    },
    "slate": {
        "bg_0": "#0a0c10",
        "bg_1": "#0f1116",
        "bg_2": "#14171d",
        "bg_3": "#1a1d24",
        "bg_4": "#20242b",
        "border": "#282c34",
        "border_fc": "#8b949e",
        "fg": "#e6edf3",
        "fg_mute": "#9aa4b1",
        "fg_dim": "#6b7684",
        "sep": "#1b1e25",
        "ok": "#56d364",
        "warn": "#d29922",
        "err": "#f85149",
        "accent": "#8b949e",
        "accent_2": "#b1bac4",
        "accent_3": "#d0d7de",
    },
    "ocean": {
        "bg_0": "#070b14",
        "bg_1": "#0c1220",
        "bg_2": "#111928",
        "bg_3": "#172233",
        "bg_4": "#1c2a3d",
        "border": "#1f3348",
        "border_fc": "#3b82f6",
        "fg": "#e2e8f0",
        "fg_mute": "#94a3b8",
        "fg_dim": "#64748b",
        "sep": "#1e293b",
        "ok": "#22c55e",
        "warn": "#eab308",
        "err": "#ef4444",
        "accent": "#3b82f6",
        "accent_2": "#60a5fa",
        "accent_3": "#93c5fd",
    },
    "cyberpunk": {
        "bg_0": "#09060f",
        "bg_1": "#0f0a18",
        "bg_2": "#161021",
        "bg_3": "#1d152c",
        "bg_4": "#251b36",
        "border": "#2e2240",
        "border_fc": "#d946ef",
        "fg": "#e2dff0",
        "fg_mute": "#a78bbf",
        "fg_dim": "#7c6a9e",
        "sep": "#1e1730",
        "ok": "#22d65e",
        "warn": "#facc15",
        "err": "#ff2d55",
        "accent": "#22d3ee",
        "accent_2": "#d946ef",
        "accent_3": "#f0aaff",
    },
    "monochrome": {
        "bg_0": "#000000",
        "bg_1": "#080808",
        "bg_2": "#101010",
        "bg_3": "#181818",
        "bg_4": "#202020",
        "border": "#2a2a2a",
        "border_fc": "#ffffff",
        "fg": "#e8e8e8",
        "fg_mute": "#909090",
        "fg_dim": "#606060",
        "sep": "#151515",
        "ok": "#bbbbbb",
        "warn": "#999999",
        "err": "#ffffff",
        "accent": "#e0e0e0",
        "accent_2": "#b0b0b0",
        "accent_3": "#808080",
    },
    "forest": {
        "bg_0": "#0a0e08",
        "bg_1": "#0f140c",
        "bg_2": "#151b11",
        "bg_3": "#1c2316",
        "bg_4": "#232b1c",
        "border": "#2a3422",
        "border_fc": "#4a7c3f",
        "fg": "#d4d9ce",
        "fg_mute": "#8a9a7e",
        "fg_dim": "#5c6b50",
        "sep": "#1a2114",
        "ok": "#4caf50",
        "warn": "#cd9b1d",
        "err": "#d9534f",
        "accent": "#5a8f4a",
        "accent_2": "#7cb342",
        "accent_3": "#aed581",
    },
    "dracula": {
        "bg_0": "#1e1e2e",
        "bg_1": "#252536",
        "bg_2": "#2d2d44",
        "bg_3": "#363654",
        "bg_4": "#3d3d5c",
        "border": "#45456a",
        "border_fc": "#bd93f9",
        "fg": "#f8f8f2",
        "fg_mute": "#a0a0b8",
        "fg_dim": "#6c6f85",
        "sep": "#313149",
        "ok": "#50fa7b",
        "warn": "#f1fa8c",
        "err": "#ff5555",
        "accent": "#bd93f9",
        "accent_2": "#ff79c6",
        "accent_3": "#8be9fd",
    },
    "sunset": {
        "bg_0": "#0d0808",
        "bg_1": "#140c0a",
        "bg_2": "#1c110e",
        "bg_3": "#241712",
        "bg_4": "#2c1d16",
        "border": "#3a251c",
        "border_fc": "#e07a3a",
        "fg": "#e8d8cc",
        "fg_mute": "#b08a74",
        "fg_dim": "#8a604a",
        "sep": "#1e1310",
        "ok": "#56d364",
        "warn": "#e3b341",
        "err": "#f85149",
        "accent": "#e07a3a",
        "accent_2": "#f59e4c",
        "accent_3": "#f7c08a",
    },
    "dark": {
        "bg_0": "#000000",
        "bg_1": "#0a0a0a",
        "bg_2": "#141414",
        "bg_3": "#1e1e1e",
        "bg_4": "#282828",
        "border": "#333333",
        "border_fc": "#569cd6",
        "fg": "#d4d4d4",
        "fg_mute": "#858585",
        "fg_dim": "#606060",
        "sep": "#181818",
        "ok": "#4ec9b0",
        "warn": "#ce9178",
        "err": "#f44747",
        "accent": "#569cd6",
        "accent_2": "#4ec9b0",
        "accent_3": "#ce9178",
    },
}


# ── Tokens (module-level constants — reassigned by set_theme()) ──────────

BG_0 = "#0b0f15"
BG_1 = "#11161d"
BG_2 = "#161c24"
BG_3 = "#1c232c"
BG_4 = "#232b36"
BORDER = "#2a323d"
BORDER_FC = "#4d8df6"
FG = "#e6edf3"
FG_MUTE = "#9aa4b1"
FG_DIM = "#6b7684"
SEP = "#1f2630"
OK = "#56d364"
WARN = "#e3b341"
ERR = "#f85149"
ACCENT = "#79c0ff"
ACCENT_2 = "#c084fc"
ACCENT_3 = "#f0b3ff"


# ── Visible glyphs — keep ASCII-fallback-safe where used in tight strips ──
SPINNER_FRAMES = "⠋⠙⠹⠸⠼⠴⠦⠧⠏"
DOT = "·"
ARROW = "❯"
CHECK = "✓"
CROSS = "✗"
BULLET = "●"


# ── CSS builders (called once at module load and again on every theme switch) ──

def _build_global_css() -> str:
    """Return the full app CSS string using the current module-level tokens."""
    return f"""
/* ──────────────────────────────────────────────────────────────────
   Jarvis TUI — global stylesheet
   Tokens kept in sync with theme.py constants.
   ───────────────────────────────────────────────────────────────── */

Screen {{
    background: {BG_0};
    color: {FG};
    layers: base overlay;
}}

/* ── App root ───────────────────────────────────────────────────── */
#main {{
    height: 100%;
    width: 100%;
    min-width: 0;
    background: {BG_0};
}}

/* ── Transcript ─────────────────────────────────────────────────── */
#transcript_wrap {{
    height: 1fr;
    min-height: 0;
    background: {BG_0};
    padding: 0;
}}
#transcript {{
    background: {BG_0};
    color: {FG};
    padding: 0 1;
    border: none;
    height: 1fr;
    min-height: 0;
    min-width: 0;
    overflow-y: auto;
    overflow-x: auto;
    scrollbar-background: {BG_0};
    scrollbar-background-hover: {BG_0};
    scrollbar-background-active: {BG_0};
    scrollbar-color: {BORDER};
    scrollbar-color-hover: {FG_DIM};
    scrollbar-color-active: {BORDER_FC};
    scrollbar-size-vertical: 0;
    scrollbar-corner-color: {BG_0};
}}

/* ── Message queue bar (above status strip, not in transcript) ───── */
#queuebar {{
    height: auto;
    min-height: 1;
    max-height: 10;
    background: {BG_2};
    color: {FG};
    padding: 0 3;
    margin: 0;
    min-width: 0;
    border-top: thick {WARN};
    overflow-y: auto;
    overflow-x: hidden;
}}
#queuebar.hidden {{
    display: none;
    height: 0;
    min-height: 0;
    max-height: 0;
    padding: 0;
    border: none;
}}

/* ── Ask-user bar (above status strip, LLM multiple-choice) ───── */
#askbar {{
    height: auto;
    min-height: 1;
    max-height: 12;
    background: {BG_2};
    color: {FG};
    padding: 0 3;
    margin: 0;
    min-width: 0;
    border-top: thick {ACCENT};
    overflow-y: auto;
    overflow-x: hidden;
}}
#askbar.hidden {{
    display: none;
    height: 0;
    min-height: 0;
    max-height: 0;
    padding: 0;
    border: none;
}}

/* ── Status strip (single line above composer) ──────────────────── */
#statusbar {{
    height: 2;
    max-height: 2;
    background: {BG_0};
    color: {FG_MUTE};
    padding: 0 3;
    margin: 0;
    min-width: 0;
    border-top: solid {SEP};
    overflow: hidden;
}}

/* ── Composer ───────────────────────────────────────────────────── */
#composer_block {{
    height: auto;
    margin: 0;
    min-width: 0;
}}
#file_ref_panel {{
    height: auto;
    max-height: 12;
    margin: 0 2 0 2;
    background: {BG_3};
    border: round {BORDER};
    padding: 0 1;
    min-width: 0;
}}
#file_ref_panel.hidden {{
    display: none;
}}
#file_ref_hint {{
    height: 1;
    max-height: 1;
    color: {FG_MUTE};
    padding: 0 0;
    margin: 0;
    overflow: hidden;
}}
#file_ref_picker {{
    height: auto;
    max-height: 8;
    min-height: 3;
    background: {BG_3};
    border: none;
    padding: 0;
    margin: 0;
    overflow-y: auto;
    scrollbar-size-vertical: 1;
}}
#file_ref_picker > .option-list--option {{
    padding: 0 1;
}}
#file_ref_picker > .option-list--option-highlighted {{
    background: {BG_4};
    color: #ffffff;
    text-style: none;
}}
#composer {{
    height: auto;
    margin: 0 2 0 2;
    background: {BG_2};
    border: round {BORDER};
    min-width: 0;
    padding: 0 1;
}}
#composer:focus-within {{
    border: round {BORDER_FC};
}}
#prompt_prefix {{
    width: 3;
    padding: 0;
    content-align: center middle;
    color: {BORDER_FC};
    text-style: bold;
    dock: left;
    background: {BG_2};
}}
#prompt {{
    height: auto;
    min-height: 1;
    max-height: 20;
    background: {BG_2};
    border: none;
    padding: 0 1;
    min-width: 0;
    scrollbar-size-vertical: 0;
}}
#prompt:focus {{
    border: none;
}}

/* ── Footer hint bar ────────────────────────────────────────────── */
#hintbar {{
    height: 1;
    max-height: 1;
    background: {BG_0};
    color: {FG_DIM};
    padding: 0 2;
    margin: 0;
    overflow: hidden;
}}

/* WebRemoteQR styling lives on the widget (web_bar.py DEFAULT_CSS). */

/* ── Web remote bar (bottom — open / copy URL) ──────────────────── */
#webar {{
    height: auto;
    min-height: 1;
    max-height: 3;
    background: {BG_1};
    color: {FG};
    padding: 0 2;
    margin: 0;
    min-width: 0;
    border-top: solid {ACCENT};
    overflow: hidden;
}}
#webar.hidden {{
    display: none;
    height: 0;
    min-height: 0;
    max-height: 0;
    padding: 0;
    border: none;
}}
#webar #web_open {{
    width: 1fr;
    min-width: 0;
    height: auto;
    padding: 0;
    overflow: hidden;
}}

/* ── Shared widget defaults ─────────────────────────────────────── */
Input, TextArea {{
    background: {BG_2};
    color: {FG};
}}
TextArea > .text-area--cursor-line {{
    background: {BG_2};
}}
TextArea > .text-area--cursor {{
    background: {BORDER_FC};
    color: {BG_0};
}}
"""


def _build_modal_css() -> str:
    """Return the shared modal chrome CSS using the current module-level tokens."""
    return f"""
/* ── Backdrop ──────────────────────────────────────────────────── */
.tui-modal-screen {{
    background: rgba(0, 0, 0, 0.66);
    align: center middle;
}}

/* ── Frame ─────────────────────────────────────────────────────── */
.tui-modal-screen #modal {{
    height: auto;
    background: {BG_2};
    border: round {BORDER};
    padding: 1 2;
}}

/* ── Title row ─────────────────────────────────────────────────── */
.tui-modal-screen #modal_title {{
    color: {ACCENT_2};
    text-style: bold;
    padding: 0 1 1 1;
    border-bottom: hkey {SEP};
    margin-bottom: 1;
    width: 100%;
}}

/* ── Status / sub-title strip ──────────────────────────────────── */
.tui-modal-screen #modal_status {{
    color: {FG_MUTE};
    padding: 0 1;
    margin-bottom: 1;
    width: 100%;
    height: auto;
}}

/* ── Footer hint row ───────────────────────────────────────────── */
.tui-modal-screen #modal_hint {{
    color: {FG_DIM};
    padding: 1 1 0 1;
    border-top: hkey {SEP};
    margin-top: 1;
    width: 100%;
}}

/* ── Inputs ────────────────────────────────────────────────────── */
.tui-modal-screen Input {{
    background: {BG_1};
    color: {FG};
    border: tall {BORDER};
    padding: 0 1;
    height: 3;
}}
.tui-modal-screen Input:focus {{
    border: tall {BORDER_FC};
}}

/* ── Option lists ──────────────────────────────────────────────── */
.tui-modal-screen OptionList {{
    background: {BG_2};
    color: {FG};
    border: none;
    padding: 0;
    overflow-y: auto;
    scrollbar-background: {BG_2};
    scrollbar-color: {BORDER};
    scrollbar-color-hover: {FG_DIM};
    scrollbar-color-active: {BORDER_FC};
    scrollbar-size-vertical: 1;
}}
.tui-modal-screen OptionList > .option-list--option {{
    padding: 0 1;
}}
.tui-modal-screen OptionList > .option-list--option-highlighted,
.tui-modal-screen OptionList:focus > .option-list--option-highlighted {{
    background: {BG_4};
    color: #ffffff;
    text-style: none;
}}
.tui-modal-screen OptionList > .option-list--option-disabled {{
    color: {FG_DIM};
}}

/* ── TextArea inside modals ────────────────────────────────────── */
.tui-modal-screen TextArea {{
    background: {BG_1};
    color: {FG};
    border: tall {BORDER};
    padding: 0 1;
}}
.tui-modal-screen TextArea:focus {{
    border: tall {BORDER_FC};
}}

/* ── Static labels inside modals ───────────────────────────────── */
.tui-modal-screen Static {{
    background: transparent;
}}
"""


# ── Pre-built CSS strings (rebuilt by set_theme()) ───────────────────────

GLOBAL_CSS: str = ""
MODAL_CSS: str = ""


# ── Theme switcher ────────────────────────────────────────────────────────

_ACTIVE_THEME: str = "red"


def set_theme(name: str) -> None:
    """Switch all theme tokens + CSS strings to the named palette.

    Callers should also rebuild the Textual stylesheet after this:
        from textual.css.stylesheet import Stylesheet
        self.stylesheet.add_source(ui.GLOBAL_CSS, …)
        self.stylesheet.reparse()
        self.stylesheet.update(self)
    """
    global _ACTIVE_THEME
    global BG_0, BG_1, BG_2, BG_3, BG_4
    global BORDER, BORDER_FC
    global FG, FG_MUTE, FG_DIM, SEP
    global OK, WARN, ERR
    global ACCENT, ACCENT_2, ACCENT_3
    global GLOBAL_CSS, MODAL_CSS

    p = PALETTES.get(name)
    if p is None:
        p = PALETTES["red"]

    _ACTIVE_THEME = name

    BG_0 = p["bg_0"]
    BG_1 = p["bg_1"]
    BG_2 = p["bg_2"]
    BG_3 = p["bg_3"]
    BG_4 = p["bg_4"]
    BORDER = p["border"]
    BORDER_FC = p["border_fc"]
    FG = p["fg"]
    FG_MUTE = p["fg_mute"]
    FG_DIM = p["fg_dim"]
    SEP = p["sep"]
    OK = p["ok"]
    WARN = p["warn"]
    ERR = p["err"]
    ACCENT = p["accent"]
    ACCENT_2 = p["accent_2"]
    ACCENT_3 = p["accent_3"]

    GLOBAL_CSS = _build_global_css()
    MODAL_CSS = _build_modal_css()


def active_theme() -> str:
    """Return the name of the currently active theme."""
    return _ACTIVE_THEME


# ── Init at module load time ─────────────────────────────────────────────
set_theme("red")
