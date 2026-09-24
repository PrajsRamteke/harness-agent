"""Single source of truth for the Jarvis TUI's visual language.

Every widget, modal, and rendered block pulls colors from the palette
tokens here. Changing a token in one place changes the entire UI.

Themes
------
PALETTES holds every built-in color scheme. set_theme(name)
reassigns the module-level tokens (BG_0 … ACCENT_3) and rebuilds
the two CSS strings; textual_theme(name) builds the matching Textual
Theme whose $jv-* variables drive the transcript widgets, so a
theme switch restyles the live conversation without re-rendering it.

Other modules that need theme-aware colors at runtime should use
from . import theme as ui → ui.OK, ui.ACCENT_2, … (never
cache the individual constants — they change on every switch).
"""
from __future__ import annotations


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
    "kimchi": {
        "bg_0": "#090b0d",
        "bg_1": "#0e1014",
        "bg_2": "#14171c",
        "bg_3": "#1a1d24",
        "bg_4": "#20242c",
        "border": "#262b34",
        "border_fc": "#5dc9a5",
        "fg": "#e2e3e7",
        "fg_mute": "#949eb0",
        "fg_dim": "#647284",
        "sep": "#181b22",
        "ok": "#4a967d",
        "warn": "#ef9f27",
        "err": "#cc6666",
        "accent": "#5dc9a5",
        "accent_2": "#8abab7",
        "accent_3": "#93c5fd",
    },
}

# Newer palettes — tuned for the minimal (borderless) transcript. The first
# three are the most-requested editor schemes; "opencode" and "claude" follow
# the look of those two agent TUIs.
PALETTES.update({
    "opencode": {
        "bg_0": "#0a0a0a",
        "bg_1": "#121212",
        "bg_2": "#1a1a1a",
        "bg_3": "#222222",
        "bg_4": "#2c2c2c",
        "border": "#303030",
        "border_fc": "#fab283",
        "fg": "#eeeeee",
        "fg_mute": "#a3a3a3",
        "fg_dim": "#6e6e6e",
        "sep": "#1c1c1c",
        "ok": "#7fd88f",
        "warn": "#f5a742",
        "err": "#e06c75",
        "accent": "#fab283",
        "accent_2": "#5c9cf5",
        "accent_3": "#9d7cd8",
    },
    "claude": {
        "bg_0": "#141413",
        "bg_1": "#1b1a19",
        "bg_2": "#232220",
        "bg_3": "#2b2a27",
        "bg_4": "#363431",
        "border": "#3a3835",
        "border_fc": "#d97757",
        "fg": "#ece9e4",
        "fg_mute": "#a8a29e",
        "fg_dim": "#77716b",
        "sep": "#211f1d",
        "ok": "#4eba65",
        "warn": "#e5b43e",
        "err": "#ff6b80",
        "accent": "#d97757",
        "accent_2": "#b1b9f9",
        "accent_3": "#eba585",
    },
    "tokyonight": {
        "bg_0": "#16161e",
        "bg_1": "#1a1b26",
        "bg_2": "#1f2335",
        "bg_3": "#24283b",
        "bg_4": "#2f344d",
        "border": "#3b4261",
        "border_fc": "#7aa2f7",
        "fg": "#c0caf5",
        "fg_mute": "#a9b1d6",
        "fg_dim": "#565f89",
        "sep": "#1f2335",
        "ok": "#9ece6a",
        "warn": "#e0af68",
        "err": "#f7768e",
        "accent": "#7aa2f7",
        "accent_2": "#bb9af7",
        "accent_3": "#7dcfff",
    },
    "catppuccin": {
        "bg_0": "#181825",
        "bg_1": "#1e1e2e",
        "bg_2": "#252536",
        "bg_3": "#313244",
        "bg_4": "#3b3d52",
        "border": "#45475a",
        "border_fc": "#cba6f7",
        "fg": "#cdd6f4",
        "fg_mute": "#a6adc8",
        "fg_dim": "#6c7086",
        "sep": "#232334",
        "ok": "#a6e3a1",
        "warn": "#f9e2af",
        "err": "#f38ba8",
        "accent": "#cba6f7",
        "accent_2": "#89b4fa",
        "accent_3": "#f5c2e7",
    },
    "gruvbox": {
        "bg_0": "#1d2021",
        "bg_1": "#232526",
        "bg_2": "#282828",
        "bg_3": "#32302f",
        "bg_4": "#3c3836",
        "border": "#504945",
        "border_fc": "#fe8019",
        "fg": "#ebdbb2",
        "fg_mute": "#bdae93",
        "fg_dim": "#7c6f64",
        "sep": "#282828",
        "ok": "#b8bb26",
        "warn": "#fabd2f",
        "err": "#fb4934",
        "accent": "#fe8019",
        "accent_2": "#83a598",
        "accent_3": "#fabd2f",
    },
    "nord": {
        "bg_0": "#242933",
        "bg_1": "#2a303c",
        "bg_2": "#2e3440",
        "bg_3": "#3b4252",
        "bg_4": "#434c5e",
        "border": "#4c566a",
        "border_fc": "#88c0d0",
        "fg": "#eceff4",
        "fg_mute": "#d8dee9",
        "fg_dim": "#7b88a1",
        "sep": "#2e3440",
        "ok": "#a3be8c",
        "warn": "#ebcb8b",
        "err": "#bf616a",
        "accent": "#88c0d0",
        "accent_2": "#81a1c1",
        "accent_3": "#b48ead",
    },
})


# One-line descriptions for the /theme picker (order = picker order).
THEME_DESCRIPTIONS: dict[str, str] = {
    "opencode": "near-black, peach accent, blue + violet highlights",
    "claude": "warm charcoal, terracotta accent, lavender highlights",
    "tokyonight": "night-city navy, soft blue + purple",
    "catppuccin": "mocha pastels, mauve accent",
    "gruvbox": "retro warm, orange + aqua on dark brown",
    "nord": "arctic slate, frost blue accents",
    "kimchi": "teal accents, dark terminal vibe",
    "red": "warm coral tones, soft pink highlights",
    "blue": "cool blue tones, teal secondary, sky highlights",
    "purple": "soft violet accents, warm amber warnings",
    "green": "nature green primary, teal secondary, mint highlights",
    "orange": "fiery orange accents, golden highlights",
    "yellow": "gold and amber tones, bright highlights",
    "rose": "hot pink accents, magenta borders",
    "slate": "neutral grays, no color bias",
    "ocean": "deep navy backgrounds, ice-blue accents",
    "cyberpunk": "neon cyan + magenta on dark purple",
    "monochrome": "pure black, white/gray only",
    "forest": "deep earthy greens, amber highlights",
    "dracula": "classic dark: purple/pink accents",
    "sunset": "warm brick bg, orange coral accents",
    "dark": "pure black bg, clean blue/teal accents",
}

DEFAULT_THEME = "opencode"


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
SPINNER_FRAMES = "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏"
# Busy indicator beside the activity label: a star that breathes.
PULSE_FRAMES = ("·", "✢", "✳", "✶", "✻", "✽", "✻", "✶", "✳", "✢")
DOT = "·"
ARROW = "❯"
CHECK = "✓"
CROSS = "✗"
BULLET = "●"
GUTTER = "⏺"
ELBOW = "⎿"


def blend(a: str, b: str, t: float) -> str:
    """Mix two ``#rrggbb`` colors; ``t`` = 0 → a, 1 → b."""
    t = max(0.0, min(1.0, t))
    try:
        ra, ga, ba = int(a[1:3], 16), int(a[3:5], 16), int(a[5:7], 16)
        rb, gb, bb = int(b[1:3], 16), int(b[3:5], 16), int(b[5:7], 16)
    except (ValueError, IndexError):
        return a
    r = round(ra + (rb - ra) * t)
    g = round(ga + (gb - ga) * t)
    bl = round(ba + (bb - ba) * t)
    return f"#{r:02x}{g:02x}{bl:02x}"


# ── Textual theme (drives built-in widgets + ``$jv-*`` CSS variables) ────

def textual_theme_name(name: str | None = None) -> str:
    return f"jarvis-{name or _ACTIVE_THEME}"


def textual_theme(name: str | None = None):
    """Build a :class:`textual.theme.Theme` for a palette.

    Built-in widgets (Markdown, OptionList, TextArea, toasts, scrollbars)
    read ``$primary`` / ``$surface`` / … so they follow the palette, and our
    own widgets use the ``$jv-*`` variables, which Textual re-resolves on
    every ``App.theme`` change — no stylesheet rebuild needed.
    """
    from textual.theme import Theme

    key = name or _ACTIVE_THEME
    p = PALETTES.get(key) or PALETTES[DEFAULT_THEME]
    variables = {f"jv-{k.replace('_', '-')}": v for k, v in p.items()}
    variables.update({
        "jv-user-bg": blend(p["bg_0"], p["bg_3"], 0.8),
        "jv-code-bg": blend(p["bg_0"], p["bg_2"], 0.9),
        "jv-select": blend(p["bg_0"], p["accent"], 0.28),
        "jv-modal-select": blend(p["bg_1"], p["accent"], 0.22),
        "block-cursor-background": p["accent"],
        "block-cursor-foreground": p["bg_0"],
        "block-cursor-text-style": "bold",
        "block-cursor-blurred-background": blend(p["bg_0"], p["accent"], 0.3),
        "block-cursor-blurred-foreground": p["fg"],
        "block-hover-background": blend(p["bg_0"], p["bg_4"], 0.6),
        "input-selection-background": blend(p["bg_0"], p["accent"], 0.35),
        "input-cursor-background": p["accent"],
        "input-cursor-foreground": p["bg_0"],
        "footer-background": p["bg_0"],
        "scrollbar": p["bg_3"],
        "scrollbar-hover": p["border"],
        "scrollbar-active": p["accent"],
        "scrollbar-background": p["bg_0"],
        "scrollbar-background-hover": p["bg_0"],
        "scrollbar-background-active": p["bg_0"],
        "scrollbar-corner-color": p["bg_0"],
        "markdown-h1-color": p["accent"],
        "markdown-h1-background": "transparent",
        "markdown-h1-text-style": "bold",
        "markdown-h2-color": p["accent"],
        "markdown-h2-background": "transparent",
        "markdown-h2-text-style": "bold",
        "markdown-h3-color": p["fg"],
        "markdown-h3-background": "transparent",
        "markdown-h3-text-style": "bold",
        "markdown-h4-color": p["fg"],
        "markdown-h4-background": "transparent",
        "markdown-h4-text-style": "bold italic",
        "markdown-h5-color": p["fg_mute"],
        "markdown-h5-background": "transparent",
        "markdown-h5-text-style": "bold",
        "markdown-h6-color": p["fg_mute"],
        "markdown-h6-background": "transparent",
        "markdown-h6-text-style": "italic",
        "link-color": p["accent_2"],
        "link-color-hover": p["accent"],
        "link-background-hover": "transparent",
    })
    return Theme(
        name=textual_theme_name(key),
        primary=p["accent"],
        secondary=p["accent_2"],
        accent=p["accent_3"],
        warning=p["warn"],
        error=p["err"],
        success=p["ok"],
        foreground=p["fg"],
        background=p["bg_0"],
        surface=p["bg_1"],
        panel=p["bg_2"],
        boost=blend(p["bg_0"], p["fg"], 0.04),
        dark=True,
        variables=variables,
    )


# ── CSS builders (called once at module load and again on every theme switch) ──

def _build_global_css() -> str:
    """Return the app CSS string using the current module-level tokens.

    Only layout chrome lives here; transcript blocks carry their own
    ``DEFAULT_CSS`` built on the ``$jv-*`` theme variables.
    """
    return f"""
Screen {{
    background: {BG_0};
    color: {FG};
    layers: base overlay;
}}

#main {{
    height: 1fr;
    width: 100%;
    min-width: 0;
    background: {BG_0};
}}
#body {{
    width: 1fr;
    height: 100%;
    min-width: 0;
    layers: base overlay;  /* StickyPrompt floats over the transcript */
}}

/* ── Bottom dock: queue · ask · activity · popups · composer · footer ──
   Gutters match the transcript: 3 cols left; right = its 3 + scrollbar. */
#dock {{
    height: auto;
    padding: 0 4 0 3;
    background: {BG_0};
}}

#queuebar {{
    height: auto;
    max-height: 8;
    color: {FG_MUTE};
    background: {BG_0};
    padding: 0 1;
    margin: 0 0 0 0;
    overflow-y: auto;
}}
#queuebar.hidden, #askbar.hidden, #popup.hidden {{
    display: none;
}}

#askbar {{
    height: auto;
    max-height: 16;
    background: {BG_0};
    color: {FG};
    border: round {ACCENT};
    padding: 0 1;
    margin: 1 0 0 0;
    overflow-y: auto;
}}

/* One blank row above (transcript padding) and below; the whole row
   collapses when there's nothing to show (ActivityLine.wanted). */
#activity {{
    height: 1;
    padding: 0;
    margin: 0 0 1 0;
    background: {BG_0};
    color: {FG_MUTE};
    overflow: hidden;
}}
#activity.-idle {{
    display: none;
}}

#popup {{
    height: auto;
    max-height: 14;
    background: {BG_1};
    padding: 0 0;
    margin: 0;
}}
#popup_hint {{
    height: 1;
    padding: 0 2;
    color: {FG_DIM};
    background: {BG_1};
}}
#popup_list {{
    height: auto;
    max-height: 10;
    text-wrap: nowrap;
    text-overflow: ellipsis;
    background: {BG_1};
    border: none;
    padding: 0;
    scrollbar-size-vertical: 1;
}}
#popup_list > .option-list--option {{
    padding: 0 1;
}}
#popup_list > .option-list--option-highlighted,
#popup_list:focus > .option-list--option-highlighted {{
    background: {blend(BG_1, ACCENT, 0.2)};
    color: {FG};
    text-style: none;
}}

#composer {{
    height: auto;
    background: {BG_2};
    border-left: outer {ACCENT};
    padding: 0 2;
    margin: 0;
}}
#composer.-busy {{
    border-left: outer {FG_DIM};
}}
#composer.-shell {{
    border-left: outer {WARN};
}}
/* Vertical breathing room lives on the children (not composer padding) so
   the pet (#pet, 3 rows) can use the composer's full height. */
#prompt_prefix {{
    width: 2;
    height: 1;
    margin: 1 0;
    color: {ACCENT};
    text-style: bold;
    background: {BG_2};
}}
#composer.-shell #prompt_prefix {{
    color: {WARN};
}}
#prompt {{
    height: auto;
    min-height: 1;
    max-height: 14;
    margin: 1 0;
    width: 1fr;
    background: {BG_2};
    border: none;
    padding: 0;
    scrollbar-size-vertical: 1;
}}
#prompt:focus {{
    border: none;
}}
#prompt > .text-area--placeholder {{
    color: {FG_DIM};
}}

/* Text lines up with the composer's content (bar + 2 cols padding);
   one blank row separates it from the composer. */
#footer {{
    height: 1;
    padding: 0 2 0 3;
    margin: 1 0 0 0;
    background: {BG_0};
    color: {FG_DIM};
}}
#footer_left {{
    width: 1fr;
    height: 1;
    overflow: hidden;
}}
#footer_right {{
    width: auto;
    height: 1;
    overflow: hidden;
}}

/* ── Web remote strip ──────────────────────────────────────────── */
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
}}
#webar #web_open {{
    width: 1fr;
    min-width: 0;
    height: auto;
    padding: 0;
    overflow: hidden;
    color: {FG_MUTE};
}}
#web_qr_overlay.hidden {{ display: none; }}

/* ── Shared widget defaults ─────────────────────────────────────── */
Input, TextArea {{
    background: {BG_2};
    color: {FG};
}}
TextArea > .text-area--cursor-line {{
    background: transparent;
}}
TextArea > .text-area--cursor {{
    background: {ACCENT};
    color: {BG_0};
}}
*:focus TextArea > .text-area--selection,
TextArea > .text-area--selection {{
    background: {blend(BG_0, ACCENT, 0.3)};
    color: {FG};
}}

Toast {{
    background: {BG_2};
    color: {FG};
    border-left: outer {ACCENT};
    padding: 0 1;
}}
Toast.-information {{
    border-left: outer {ACCENT};
}}
Toast.-warning {{
    border-left: outer {WARN};
}}
Toast.-error {{
    border-left: outer {ERR};
}}
Toast .toast--title {{
    text-style: bold;
}}
ToastRack {{
    align: right top;
    padding: 1 2 0 0;
}}

Scrollbar {{
    scrollbar-background: {BG_0};
    scrollbar-color: {BG_3};
    scrollbar-color-hover: {BORDER};
    scrollbar-color-active: {ACCENT};
}}
"""


def _build_modal_css() -> str:
    """Return the shared modal chrome CSS (``TuiModalScreen.DEFAULT_CSS``).

    Dialogs are flat panels on a dimmed backdrop: no heavy frame, a bold
    title, and an accent-filled selection row. Colors are ``$jv-*`` theme
    variables, so the string is the same for every palette and Textual
    re-resolves it on each ``App.theme`` switch — open dialogs included.
    """
    return """
.tui-modal-screen {
    background: $jv-bg-0 60%;
    align: center middle;
}

.tui-modal-screen #modal {
    height: auto;
    background: $jv-bg-1;
    border: none;
    border-left: outer $jv-accent;
    padding: 1 3;
}

.tui-modal-screen #modal_title {
    color: $jv-fg;
    text-style: bold;
    padding: 0 1;
    margin-bottom: 1;
    width: 100%;
}

.tui-modal-screen #modal_status {
    color: $jv-fg-mute;
    padding: 0 1;
    margin-bottom: 1;
    width: 100%;
    height: auto;
}

.tui-modal-screen #modal_hint {
    color: $jv-fg-dim;
    padding: 0 1;
    margin-top: 1;
    width: 100%;
}

.tui-modal-screen Input {
    background: $jv-bg-2;
    color: $jv-fg;
    border: none;
    padding: 0 1;
    height: 1;
    margin: 0 0 1 0;
}
/* TuiModalScreen makes inputs compact; outrank Textual's `padding: 0`. */
.tui-modal-screen Input.-textual-compact {
    padding: 0 1;
}
.tui-modal-screen Input:focus {
    border: none;
    background: $jv-bg-3;
}
.tui-modal-screen Input > .input--placeholder {
    color: $jv-fg-dim;
}

.tui-modal-screen OptionList {
    background: $jv-bg-1;
    color: $jv-fg;
    border: none;
    padding: 0;
    text-wrap: nowrap;
    text-overflow: ellipsis;
    overflow-y: auto;
    scrollbar-background: $jv-bg-1;
    scrollbar-color: $jv-bg-4;
    scrollbar-color-hover: $jv-border;
    scrollbar-color-active: $jv-accent;
    scrollbar-size-vertical: 1;
}
.tui-modal-screen OptionList:focus {
    border: none;
    background-tint: transparent;
}
.tui-modal-screen OptionList > .option-list--option {
    padding: 0 1;
}
.tui-modal-screen OptionList > .option-list--option-highlighted,
.tui-modal-screen OptionList:focus > .option-list--option-highlighted {
    background: $jv-modal-select;
    color: $jv-fg;
    text-style: bold;
}
.tui-modal-screen OptionList > .option-list--option-hover {
    background: $jv-bg-3;
}
.tui-modal-screen OptionList > .option-list--option-disabled {
    color: $jv-fg-dim;
    text-style: none;
}
.tui-modal-screen #modal_status, .tui-modal-screen #model_subtitle,
.tui-modal-screen #modal_subtitle {
    color: $jv-fg-dim;
    padding: 0 1;
    margin-bottom: 1;
}
.tui-modal-screen OptionList > .option-list--separator {
    color: $jv-bg-4;
}

.tui-modal-screen TextArea {
    background: $jv-bg-2;
    color: $jv-fg;
    border: tall $jv-bg-2;
    padding: 0 1;
}
.tui-modal-screen TextArea:focus {
    border: tall $jv-bg-3;
}

.tui-modal-screen Static {
    background: transparent;
}
.tui-modal-screen Button {
    background: $jv-bg-3;
    color: $jv-fg;
    border: none;
    min-width: 8;
    height: 1;
    margin: 0 1;
}
.tui-modal-screen Button:focus, .tui-modal-screen Button:hover {
    background: $jv-accent;
    color: $jv-bg-0;
    text-style: bold;
}
"""


# ── Pre-built CSS strings (rebuilt by set_theme()) ───────────────────────

GLOBAL_CSS: str = ""
MODAL_CSS: str = ""


# ── Theme switcher ────────────────────────────────────────────────────────

_ACTIVE_THEME: str = DEFAULT_THEME


def set_theme(name: str) -> None:
    """Switch all theme tokens + CSS strings to the named palette.

    The app applies the change live via ``JarvisTUI._apply_theme_runtime``
    (stylesheet rebuild + ``App.theme`` swap).
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
        name = DEFAULT_THEME
        p = PALETTES[name]

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


def theme_names() -> list[str]:
    """Palette names in picker order (described ones first)."""
    ordered = [n for n in THEME_DESCRIPTIONS if n in PALETTES]
    return ordered + [n for n in PALETTES if n not in THEME_DESCRIPTIONS]


# ── Init at module load time ─────────────────────────────────────────────
set_theme(DEFAULT_THEME)
