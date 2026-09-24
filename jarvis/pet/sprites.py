"""How the pets look: palettes, pixel sprites, accessories, props, and scenes.

One pixel pipeline serves every view. A pet is an 18-px-tall grid (4 px of
headroom for ears/horns/hats + a 14 px body) drawn with half blocks, two
pixels per terminal cell:

* :func:`pet_pixels` — species base art, expression (eyes/mouth) stamps,
  pose (sitting / walking / paws over eyes), accessories unlocked by level
  (glasses while working, party hat, scarf, crown) and growth by level;
* :func:`pen_stage` — the scene: the pet plus props (yarn, bowl, box,
  butterfly, shelf + cup, laser dot, falling fish, keyboard, pumpkin, moon,
  clouds, snow, confetti) and floating text (♥ z ✦);
* :func:`buddy_lines` — a 3-row text pet for the input box (used when the
  sidebar, and so the pen, is hidden).

Frames are pure functions of their inputs (``t`` = seconds into the
animation), and every line has a fixed width, so layout never jumps.
"""
from __future__ import annotations

import math
from datetime import datetime

from rich.text import Text

PINK = "#f58ba6"
GOLD = "#ffd166"
LASER = "#ff3b3b"

# ── palettes ──────────────────────────────────────────────────────────────
# F fur · D darker (stripes/ears/spots) · L belly/muzzle · O outline · E eyes
# W shine · M mouth/nose · P pink · A wings/accent · Y horns · line = text pet

_BASE = {"W": "#ffffff", "P": PINK}
FURS: dict[str, dict[str, str]] = {
    # cats
    "ginger": {"F": "#f4a261", "D": "#dd7b3a", "L": "#fde6cc", "O": "#4a2f22",
               "E": "#231a17", "M": "#231a17", "line": "#f4a261"},
    "midnight": {"F": "#3f4453", "D": "#2e323d", "L": "#6d7486", "O": "#15171c",
                 "E": "#b8f36b", "M": "#15171c", "line": "#a4acbd"},
    "snow": {"F": "#eef0f4", "D": "#d3d8e1", "L": "#ffffff", "O": "#70778a",
             "E": "#4b8bd6", "M": "#70778a", "P": "#f7a8c4", "line": "#e9ecf2"},
    "smoky": {"F": "#9aa1ad", "D": "#7a818f", "L": "#d9dde3", "O": "#363b45",
              "E": "#f2c14e", "M": "#363b45", "line": "#b7bdc8"},
    "cocoa": {"F": "#a0714f", "D": "#7f5539", "L": "#e6ccb2", "O": "#3b2618",
              "E": "#231a17", "M": "#231a17", "line": "#c9956d"},
    # dogs
    "golden": {"F": "#e9b872", "D": "#a0632f", "L": "#fbe8c8", "O": "#4a2f22",
               "E": "#231a17", "M": "#231a17", "line": "#e9b872"},
    "choco": {"F": "#7b4a2e", "D": "#4f2d1a", "L": "#c89a74", "O": "#2a170c",
              "E": "#1a100a", "M": "#1a100a", "line": "#b07a52"},
    "husky": {"F": "#8c95a3", "D": "#5b6270", "L": "#f1f3f6", "O": "#2c313a",
              "E": "#6fb7ff", "M": "#1f2228", "line": "#aab2bf"},
    # bunnies
    "caramel": {"F": "#d9a066", "D": "#b97a3f", "L": "#fbe7cf", "O": "#5a3b20",
                "E": "#231a17", "M": "#8b5e3c", "line": "#d9a066"},
    # dragons
    "emerald": {"F": "#5cc98a", "D": "#3f9e67", "L": "#d9f7c8", "O": "#1f3a2a",
                "E": "#231a17", "M": "#1f3a2a", "A": "#8fe0b0", "Y": GOLD, "line": "#5cc98a"},
    "amethyst": {"F": "#a78bfa", "D": "#7c5fd6", "L": "#ede4ff", "O": "#2e1f55",
                 "E": "#1c1433", "M": "#2e1f55", "A": "#c9b8ff", "Y": GOLD, "line": "#b9a2ff"},
    "ruby": {"F": "#f07178", "D": "#c9474f", "L": "#ffe0d6", "O": "#4d1a1f",
             "E": "#2a0e11", "M": "#4d1a1f", "A": "#ffb0a8", "Y": GOLD, "line": "#f38b90"},
    "sapphire": {"F": "#5aa9f0", "D": "#3a7fc4", "L": "#dcefff", "O": "#16304d",
                 "E": "#0e1d30", "M": "#16304d", "A": "#a6d4ff", "Y": GOLD, "line": "#7dbcf5"},
    "theme": {},  # follows the active TUI theme's accent (filled in lazily)
}
SPECIES_FURS = {
    "cat": ("ginger", "midnight", "snow", "smoky", "cocoa", "theme"),
    "dog": ("golden", "choco", "husky", "snow", "theme"),
    "bunny": ("snow", "cocoa", "smoky", "caramel", "theme"),
    "dragon": ("emerald", "amethyst", "ruby", "sapphire", "theme"),
}
FUR_ORDER = SPECIES_FURS["cat"]
FUR_LABELS = {
    "ginger": "ginger tabby", "midnight": "midnight black", "snow": "snow white",
    "smoky": "smoky grey", "cocoa": "cocoa brown", "theme": "theme colors",
    "golden": "golden", "choco": "chocolate", "husky": "husky grey", "caramel": "caramel",
    "emerald": "emerald", "amethyst": "amethyst", "ruby": "ruby", "sapphire": "sapphire",
}


def default_fur(species: str) -> str:
    return SPECIES_FURS.get(species, FUR_ORDER)[0]


def fur_colors(fur: str) -> dict[str, str]:
    if fur == "theme":
        from ..tui import theme as ui

        acc = ui.ACCENT
        return {**_BASE, "F": acc, "D": ui.blend(acc, ui.BG_0, 0.22),
                "L": ui.blend(acc, "#ffffff", 0.62), "O": ui.blend(acc, ui.BG_0, 0.72),
                "E": ui.BG_0, "M": ui.BG_0, "A": ui.blend(acc, "#ffffff", 0.35), "Y": GOLD,
                "line": acc}
    colors = {**_BASE, **(FURS.get(fur) or FURS["ginger"])}
    colors.setdefault("A", colors["L"])
    colors.setdefault("Y", GOLD)
    return colors


def next_fur(fur: str, species: str = "cat") -> str:
    order = SPECIES_FURS.get(species, FUR_ORDER)
    i = order.index(fur) if fur in order else -1
    return order[(i + 1) % len(order)]


def blink_at(t: float, seed: float = 0.0) -> bool:
    """Deterministic, irregular blinking (~every 3–5s, 0.15s long)."""
    period = 3.7 + 1.3 * math.sin(seed + math.floor(t / 4.3))
    return (t % period) < 0.15


# ── species art (16 wide, 18 tall: rows 0–3 are headroom) ─────────────────

_EMPTY = "." * 16
_ART = {
    "cat": (
        _EMPTY, _EMPTY, _EMPTY, _EMPTY,
        ".OO.......OO....",
        "OPFO.....OFPO...",
        "OFFFOOOOOFFFO...",
        "OFFFFDFDFFFFO...",
        "OFWEFFFFFWEFO...",
        "OFEEFFFFFEEFO...",
        "OFPFFFPFFFPFO...",
        ".OFFFMFMFFFO....",
        "..OOFFFFFOO..OO.",
        "..OFLLLLLFO.OFFO",
        ".OFLLLLLLLFO.OFO",
        ".OFLLLLLLLFOOFO.",
        ".OFFOLLLOFFOFO..",
        "..OOOOOOOOOO....",
    ),
    "dog": (
        _EMPTY, _EMPTY, _EMPTY, _EMPTY,
        _EMPTY,
        "...OOOOOOOO.....",
        "..OFFFFFFFFO....",
        ".ODDFFFFFFDDO...",
        "ODDDWEFFWEDDDO..",
        "ODDDEEFFEEDDDO..",
        ".ODOFLLLLFODO...",
        "..O.LLMMLL.O....",
        "...OLLLLLLO...O.",
        "..OFFLLLLFFO.OFO",
        ".OFFLLLLLLFFOOFO",
        ".OFFLLLLLLFFOFO.",
        ".OFOOFFFFOOFO...",
        "..OO.OOOO.OO....",
    ),
    "bunny": (
        "..OO.....OO.....",
        ".OFPO...OPFO....",
        ".OFPO...OPFO....",
        ".OFPO...OPFO....",
        "..OFO...OFO.....",
        "..OFFOOOOFFO....",
        ".OFFFFFFFFFFO...",
        "OFFFFFFFFFFFFO..",
        "OFFWEFFFFWEFFO..",
        "OFFEEFFFFEEFFO..",
        "OFPFFFPPFFFPFO..",
        ".OFFFFMMFFFFO...",
        "..OOFFFFFFOO....",
        "..OFLLLLLLFO....",
        ".OFLLLLLLLLFO.OO",
        ".OFLLLLLLLLFOOLO",
        ".OFFOLLLLOFFOOO.",
        "..OOOOOOOOOO....",
    ),
    "dragon": (
        _EMPTY, _EMPTY,
        "..Y.......Y.....",
        "..OY..Y..YO.....",
        "...OO.O...OO....",
        "..OFFOOOOOFFO...",
        ".OFFFFFFFFFFFO..",
        "OFFFFDFDFDFFFFO.",
        "OFFWEFFFFFWEFFO.",
        "OFFEEFFFFFEEFFO.",
        "OFFFFFMFMFFFFFO.",
        ".OFFFFMMMFFFFO..",
        "AOOOFFFFFFFOOOA.",
        "AAAOLDLDLDLOAAA.",
        ".AAOLLLLLLLOAA.O",
        "..OFLDLDLDLFO.OF",
        "..OFFOFFFOFFOOFO",
        "...OOOOOOOOOOOO.",
    ),
    "egg": (
        _EMPTY, _EMPTY, _EMPTY, _EMPTY, _EMPTY, _EMPTY,
        "......OOOO......",
        ".....OLLLLO.....",
        "....OLLDLLLO....",
        "....OLLLLLLO....",
        "...OLDLLLLDLO...",
        "...OLLLLLLLLO...",
        "...OLLLLDLLLO...",
        "...OLLDLLLLLO...",
        "...OLLLLLLDLO...",
        "....OLLLLLLO....",
        ".....OOOOOO.....",
        _EMPTY,
    ),
}
SPRITE_W, SPRITE_H = 16, 18
MINI_W, MINI_H = SPRITE_W, SPRITE_H  # (legacy names)

# Where things sit on each species (in the 18-row grid): eye row, 2-px eye
# cols (l, r), 3-px eye boxes (l, r), hat point (row, col), neck (row, c0, c1),
# open-mouth cells, first walking-leg row.
_ANCHOR = {
    "cat": dict(eye=8, e2=(2, 9), e3=(1, 9), hat=(6, 6), neck=(12, 2, 10),
                mouth=((11, 5, "M"), (11, 6, "P"), (11, 7, "M")), legs=16),
    "dog": dict(eye=8, e2=(4, 8), e3=(3, 8), hat=(5, 6), neck=(12, 3, 10),
                mouth=((12, 6, "P"), (12, 7, "P"), (13, 6, "P")), legs=16),
    "bunny": dict(eye=8, e2=(3, 9), e3=(2, 9), hat=(5, 6), neck=(12, 2, 11),
                  mouth=((11, 6, "M"), (11, 7, "M"), (12, 6, "W"), (12, 7, "W")), legs=16),
    "dragon": dict(eye=8, e2=(3, 10), e3=(2, 10), hat=(5, 7), neck=(12, 3, 11),
                   mouth=((11, 6, "M"), (11, 7, "P"), (11, 8, "M"), (12, 7, "Y")), legs=16),
}
_WALK = (  # leg rows 16–17 alternate while walking (same for every species)
    (".OFOOFFFOOFO....", ".OO..OOO...OO..."),
    ("..OFOOFOOFOO....", "..OO..OO..OO...."),
)

_EYES2 = {"open": ("WE", "EE"), "blink": ("..", "EE"), "focus": ("EE", "EE")}
_EYES3 = {
    "happy": (".E.", "E.E"), "content": ("E.E", ".E."), "love": ("P.P", ".P."),
    "ouch": ("EE.", "..E"), "wide": ("EEE", "EWE"), "worried": ("E..", ".EE"),
}
_EYE_FOR_ANIM = {
    "idle": "open", "work": "focus", "happy": "happy", "proud": "happy", "love": "love",
    "eat": "content", "play": "happy", "sleep": "content", "ouch": "ouch",
    "surprised": "wide", "trick": "happy", "wave": "happy", "cheer": "happy",
    "party": "happy", "worried": "worried", "hide": "blink", "hatch": "wide",
}

# Accessories, anchored to the hat point / eyes / neck.
_HAT = {
    "party": ("..G..", ".RSR.", ".SRS.", "RSRSR"),
    "crown": ("G.G.G", "GGGGG", "GJGJG"),
}
_ACC_COLORS = {"G": GOLD, "R": "#ff6b8b", "S": "#ffe066", "J": "#ff4d6d",
               "K": "#2b2f36", "C": "#e63946", "c": "#ffd6dc"}


def _stamp(grid: list[list[str]], row: int, col: int, rows, mirror: bool = False) -> None:
    for dy, pattern in enumerate(rows):
        cells = pattern[::-1] if mirror else pattern
        for dx, ch in enumerate(cells):
            y, x = row + dy, col + dx
            if 0 <= y < len(grid) and 0 <= x < len(grid[y]):
                grid[y][x] = "F" if ch == "." else ch


def _overlay(grid: list[list[str]], row: int, col: int, rows) -> None:
    """Like _stamp, but '.' leaves the pixel alone (for accessories)."""
    for dy, pattern in enumerate(rows):
        for dx, ch in enumerate(pattern):
            y, x = row + dy, col + dx
            if ch != "." and 0 <= y < len(grid) and 0 <= x < len(grid[y]):
                grid[y][x] = ch


def _grow(rows: list[str], stage: int, center: int) -> list[str]:
    """Bigger pets: repeat a belly row and the center column per stage."""
    rows = list(rows)
    for _ in range(max(0, stage)):
        rows.insert(14, rows[14])
        rows = [r[: center + 1] + r[center] + r[center + 1:] for r in rows]
    return rows


def pet_pixels(species: str, anim: str, t: float, *, walking: bool = False, facing: int = -1,
               look: int = 0, outfit=frozenset(), stage: int = 0,
               egg_cracks: int = 0) -> list[str]:
    """Pixel rows (palette keys) for one frame: ``SPRITE_H + stage`` rows of
    ``SPRITE_W + stage`` pixels."""
    if species == "egg":
        grid = [list(r) for r in _ART["egg"]]
        for y, x in ((9, 7), (10, 8), (9, 9), (12, 6), (13, 7), (11, 10))[: 2 * max(0, egg_cracks)]:
            grid[y][x] = "O"
        rows = ["".join(r) for r in grid]
        if anim in ("wobble", "love", "surprised", "hatch"):
            shift = (0, 1, 0, -1)[int(t * 5) % 4]
        else:
            shift = 1 if (int(t * 1.2) % 6 == 0 and int(t * 8) % 2) else 0
        if shift > 0:
            rows = [r[-1:] + r[:-1] for r in rows]
        elif shift < 0:
            rows = [r[1:] + r[:1] for r in rows]
        return rows
    art = _ART.get(species, _ART["cat"])
    anchor = _ANCHOR.get(species, _ANCHOR["cat"])
    grid = [list(r) for r in art]
    if walking or anim == "play":
        legs = _WALK[int(t * (6 if walking else 4)) % 2]
        grid[anchor["legs"]] = list(legs[0])
        grid[anchor["legs"] + 1] = list(legs[1])

    eyes = _EYE_FOR_ANIM.get(anim, "open")
    if anim in ("idle", "work") and blink_at(t, 2.1 if anim == "work" else 0.4):
        eyes = "blink"
    er, (l2, r2), (l3, r3) = anchor["eye"], anchor["e2"], anchor["e3"]
    if eyes in _EYES2:
        _stamp(grid, er, l2, ("..", ".."))
        _stamp(grid, er, r2, ("..", ".."))
        dx = max(-1, min(1, look))
        _stamp(grid, er, l2 + dx, _EYES2[eyes])
        _stamp(grid, er, r2 + dx, _EYES2[eyes])
    else:
        _stamp(grid, er, l3, _EYES3[eyes])
        _stamp(grid, er, r3, _EYES3[eyes], mirror=True)
    if (anim == "eat" and int(t * 4) % 2) or anim in ("surprised", "wave", "cheer", "party"):
        for y, x, key in anchor["mouth"]:
            grid[y][x] = key
    if anim == "hide":  # paws up over the eyes
        _overlay(grid, er - 1, l3 - 1, ("OFFO", "OFFO", ".OO."))
        _overlay(grid, er - 1, r3, ("OFFO", "OFFO", ".OO."))

    hat_row, hat_c = anchor["hat"]
    wear = set(outfit)
    if anim == "party":
        wear.add("party")
        wear.discard("crown")
    if "glasses" in wear and anim != "hide":
        _overlay(grid, er - 1, l2 - 1, ("K" * (r2 - l2 + 4),))
        for x in (l2 - 1, l2 + 2, r2 - 1, r2 + 2):
            _overlay(grid, er, x, ("K",))
    if "scarf" in wear:
        nr, n0, n1 = anchor["neck"]
        _overlay(grid, nr, n0, ("C" * (n1 - n0 + 1),))
        _overlay(grid, nr + 1, n1 - 2, ("cC",))
    for hat in ("crown", "party"):
        if hat in wear:
            art_rows = _HAT[hat]
            _overlay(grid, hat_row - len(art_rows) + 1, hat_c - 2, art_rows)
            break

    rows = _grow(["".join(r) for r in grid], stage, hat_c)
    if facing > 0:
        rows = [r[::-1] for r in rows]
    return rows


# ── the scene ─────────────────────────────────────────────────────────────

_PROPS = {
    "ball": ("GG", "GG"),
    "bowl": (".fff.", "OKKKO", ".OOO."),
    "box": ("kk......kk", "OkkkkkkkkO", "OkttkkttkO", "OkkkkkkkkO", "OkkkkkkkkO", "OOOOOOOOOO"),
    "butterfly": (("V.V", ".O."), (".V.", ".O.")),
    "shelf": ("hhhhhhhhh", ".h.....h."),
    "cup": ("uuu.", "uuuh", "uuu."),
    "shards": ("u...u.", ".u.u..", "u....u"),
    "fish": (".bbb.b", "bEbbbb", ".bbb.b"),
    "fish2": (".BBB.B", "BEBBBB", ".BBB.B"),
    "keyboard": ("OOOOOOOOOOOO", "OKwKwKwKwKwO", "OOOOOOOOOOOO"),
    "pumpkin": ("..g...", ".QqQq.", "QqQqQq", "QqQqQq", ".QqQq."),
    "moon": (".mm.", "mm..", "mm..", ".mm."),
    "cloud": (".www...", "wwwwww.", ".wwwww."),
}
_PROP_COLORS = {
    "G": GOLD, "K": "#c9d1d9", "k": "#c8955c", "t": "#e8c48a", "V": "#c084fc",
    "h": "#8b5a2b", "u": "#f4f1ea", "b": "#7fb4ff", "B": "#ffa94d", "E": "#1a1d23",
    "w": "#3b4150", "Q": "#ff8c1a", "q": "#d96a00", "g": "#5cc98a", "m": "#f5e6a8",
    "O": "#6b5a3a",
}
_CONFETTI = ("#ff6b8b", GOLD, "#7fb4ff", "#5cc98a", "#c084fc")


def _pixel_cell(a: str | None, b: str | None) -> tuple[str, str]:
    if a and b:
        return ("█", a) if a == b else ("▀", f"{a} on {b}")
    if a:
        return ("▀", a)
    if b:
        return ("▄", b)
    return (" ", "")


def pen_stage(width: int, cat_x: int, anim: str, t: float, fur: str = "ginger", *,
              species: str = "cat", walking: bool = False, facing: int = -1, look: int = 0,
              lift: int = 0, outfit=frozenset(), stage: int = 0, egg_cracks: int = 0,
              rows: int = 10, ball_x: int | None = None, bowl_x: int | None = None,
              snack: str = "", props: list | None = None, front: list | None = None,
              pixels: list | None = None, deco: list | None = None,
              confetti: float = 0.0) -> list[Text]:
    """A ``rows``-tall scene, ``width`` cells wide.

    ``props`` / ``front`` are ``(name, x, y_px)`` drawn behind / in front of
    the pet (``y_px`` from the top; negative = resting on the floor).
    ``pixels`` are single dots ``(x, y_px, color)`` (the laser pointer).
    ``deco`` are text glyphs ``(col, row, text, color)`` placed only on
    empty cells.
    """
    fur_c = fur_colors(fur)
    prop_c = dict(_PROP_COLORS)
    prop_c["f"] = {"milk": "#f4f1ea", "cookie": "#e9c46a"}.get(snack, "#7fb4ff")
    h = rows * 2
    canvas: list[list[str | None]] = [[None] * width for _ in range(h)]

    def blit(art, x0: int, y0: int, palette) -> None:
        if y0 < 0:
            y0 = h + y0 - len(art) + 1
        for dy, row in enumerate(art):
            y = y0 + dy
            if not 0 <= y < h:
                continue
            for dx, key in enumerate(row):
                x = x0 + dx
                if key != "." and 0 <= x < width:
                    color = palette.get(key)
                    if color:
                        canvas[y][x] = color

    def art_of(name: str):
        art = _PROPS[name]
        return art[int(t * 8) % 2] if name == "butterfly" else art

    for name, x, y in props or []:
        blit(art_of(name), x, y, prop_c)
    sprite = pet_pixels(species, anim, t, walking=walking, facing=facing, look=look,
                        outfit=outfit, stage=stage, egg_cracks=egg_cracks)
    blit(sprite, cat_x, h - len(sprite) - max(0, min(2, lift)), {**fur_c, **_ACC_COLORS})
    if bowl_x is not None:
        blit(art_of("bowl"), bowl_x, -1, {**prop_c, "O": fur_c["O"]})
    if ball_x is not None:
        blit(_PROPS["ball"], ball_x, h - 2 - (0, 1, 2, 1)[int(t * 8) % 4], prop_c)
    for name, x, y in front or []:
        blit(art_of(name), x, y, prop_c)
    for x, y, color in pixels or []:
        if 0 <= x < width and 0 <= y < h:
            canvas[y][x] = color
    if confetti > 0:
        for k in range(14):
            x = (k * 7 + int(k * t * 3)) % width
            y = int((t * (6 + k % 4) + k * 3) % h * min(1.0, confetti))
            if canvas[y][x] is None:
                canvas[y][x] = _CONFETTI[k % len(_CONFETTI)]

    cells = []
    for row in range(rows):
        top, bot = canvas[row * 2], canvas[row * 2 + 1]
        cells.append([_pixel_cell(top[x], bot[x]) for x in range(width)])
    for col, row, text, color in deco or []:
        for i, ch in enumerate(text):
            x = col + i
            if 0 <= row < rows and 0 <= x < width and cells[row][x][0] == " ":
                cells[row][x] = (ch, color)
    out = []
    for row in cells:
        line = Text(no_wrap=True)
        for ch, style in row:
            line.append(ch, style=style or None)
        out.append(line)
    return out


def sky(now: datetime, width: int, t: float) -> tuple[list, list]:
    """Time-of-day and seasonal scenery: (props, text deco) for a pen."""
    props: list = []
    deco: list = []
    night = now.hour >= 19 or now.hour < 6
    if night:
        props.append(("moon", width - 6, 1))
        for k, (x, y) in enumerate(((3, 0), (11, 1), (19, 0), (24, 2), (7, 2))):
            if x < width - 7 and int(t * 1.3 + k) % 4:
                deco.append((x, y, "·" if (k + int(t)) % 3 else "✦", "#6b7385"))
    else:
        drift = int(t * 0.6) % (width + 8) - 8
        props.append(("cloud", drift, 1))
    if now.month == 12:
        for k in range(7):
            deco.append(((k * 5 + 2) % width, int(t * 1.2 + k * 1.7) % 6,
                         "*" if k % 2 else "·", "#cfe3ff"))
    if now.month == 10:
        props.append(("pumpkin", 0, -1))
    return props, deco


# ── the 3-row text pet (input box) ────────────────────────────────────────

BUDDY_W = 10  # cells, incl. room for a floating ♥ / z / ✦

_TEXT = {  # (ears, face left, face right, paws)
    "cat": (" /\\_/\\ ", "(", ")", '(")_(")'),
    "dog": ("  ,---, ", "U(", ")U", ' (")_(")'),
    "bunny": (" (\\_/) ", "(", ")", '(")(")'),
    "dragon": (" ^\\_/^ ", "(", ")", '(")_(")>'),
}
_FACES = {
    "idle": "•ω•", "blink": "-ω-", "work": "•_•", "happy": "^ω^", "proud": "^ω^",
    "love": "♥ω♥", "eat": "•ω•", "eat2": "•o•", "play": "^ω^", "sleep": "-ω-",
    "sleep2": "-.-", "ouch": ">_<", "surprised": "°o°", "trick": "•ω•", "wave": "^ω^",
    "sleepy": "=ω=", "cheer": "^o^", "party": "^o^", "hide": "#ω#", "worried": "•~•",
    "hatch": "°o°",
}


def buddy_lines(anim: str, t: float, fur: str = "ginger", *, look: int = 0,
                mood: str = "content", eyes_open: bool = True, species: str = "cat") -> list[Text]:
    """Three fixed-width lines for the input-box pet at time ``t`` (seconds)."""
    c = fur_colors(fur)
    line, eye = c["line"], "#f5f5f7" if fur != "snow" else "#4b5563"
    dim = "#8b8f98"
    if species == "egg":
        return _egg_lines(anim, t, line, dim)
    ears, lface, rface, paws = _TEXT.get(species, _TEXT["cat"])
    step = int(t * 4)
    face_key = anim if anim in _FACES else "idle"
    extra, extra_style = "", dim
    tail = "~" if (int(t * 1.6) % 2 == 0) else " "
    side = ""
    shift = look

    if anim == "idle":
        if mood == "sleepy":
            face_key = "sleepy"
        if not eyes_open or blink_at(t):
            face_key = "blink"
    elif anim == "work":
        extra = "." * (1 + step % 3)
        paws = paws.replace('(")', "(')", 1) if step % 2 else paws
        tail = " "
        shift = (-1, 0, 1, 0)[int(t * 1.5) % 4] if look == 0 else look
        if blink_at(t, 1.3):
            face_key = "blink"
    elif anim == "love":
        extra, extra_style = ("♥", " ♥", "  ♥", " ♥")[step % 4], PINK
    elif anim == "eat":
        face_key = "eat2" if step % 2 else "eat"
        tail = ("<><", "<>", "<", "")[min(3, int(t * 1.4))]
    elif anim == "play":
        shift = (0, 1, 1, 0)[step % 4]
        tail = ("●", " ●", "  ●", " ●")[step % 4]
    elif anim == "sleep":
        face_key = "sleep2" if (int(t) % 4 == 3) else "sleep"
        extra = ("z", "zZ", "zZz", " Zz", "  z", "")[int(t * 1.5) % 6]
        tail = " "
    elif anim in ("proud", "cheer", "party"):
        extra, extra_style = ("✦", "✧", " ✦", "✧")[step % 4], GOLD
    elif anim in ("ouch", "worried"):
        extra, extra_style = ";", "#7fb4ff"
        shift = (-1, 1)[step % 2] if t < 0.5 else 0
    elif anim in ("surprised", "hatch"):
        extra, extra_style = "!", GOLD
    elif anim == "wave":
        side = ("/", "\\")[step % 2]
    elif anim == "trick":
        phase = int(t * 5) % 4
        shift = (0, 1, 0, -1)[phase]
        if phase == 2:
            face_key = "_back"
        extra, extra_style = ("✧", "", "✦", "")[phase], GOLD

    face = "   " if face_key == "_back" else _FACES[face_key]
    inner = f" {face} " if shift == 0 else (f"{face}  " if shift < 0 else f"  {face}")

    row0 = Text(no_wrap=True)
    row0.append(ears, style=line)
    row0.append(extra[: max(0, BUDDY_W - len(ears))], style=extra_style)

    row1 = Text(no_wrap=True)
    row1.append(lface, style=line)
    for ch in inner:
        if ch in "ωo♥":
            row1.append(ch, style=PINK)
        elif ch in "•-^=°><#~":
            row1.append(ch, style=eye)
        else:
            row1.append(ch, style=line)
    row1.append(rface, style=line)
    row1.append(side, style=line)

    row2 = Text(no_wrap=True)
    row2.append(" ")
    row2.append(paws, style=line)
    if tail:
        row2.append(tail, style=GOLD if "●" in tail else ("#7fb4ff" if anim == "eat" else dim))

    out = []
    for row in (row0, row1, row2):
        row.truncate(BUDDY_W)
        row.pad_right(BUDDY_W - row.cell_len)
        out.append(row)
    return out


def _egg_lines(anim: str, t: float, line: str, dim: str) -> list[Text]:
    wob = (0, 1, 0, -1)[int(t * 4) % 4] if anim in ("wobble", "love", "surprised", "hatch") else 0
    pad = " " * (2 + wob)
    rows = [f"{pad} .--.", f"{pad}(    )", f"{pad} `--'"]
    out = []
    for s in rows:
        txt = Text(s, style=line, no_wrap=True)
        txt.truncate(BUDDY_W)
        txt.pad_right(BUDDY_W - txt.cell_len)
        out.append(txt)
    return out


# ── portrait (the /pet card and the REPL card) ────────────────────────────

PORTRAIT_W, PORTRAIT_ROWS = 26, 10


def portrait(species: str, anim: str, t: float, fur: str, *, outfit=frozenset(), stage: int = 0,
             egg_cracks: int = 0, width: int = PORTRAIT_W, rows: int = PORTRAIT_ROWS,
             look: int = 0, snack: str = "") -> list[Text]:
    """The pet sitting centered in a small stage (``rows`` × ``width``)."""
    x = max(0, (width - SPRITE_W - stage) // 2)
    side = min(x + SPRITE_W + stage, width - 3)
    step = int(t * 3)
    deco: list = []
    if anim == "love":
        deco = [(side + (k + step) % 3, 6 - (step + k * 2) % 6, "♥", PINK) for k in range(3)]
    elif anim == "sleep":
        deco = [(side + (step + k) % 3, 5 - (step + k) % 5, "zZz"[k], "#9aa3b5") for k in range(3)]
    elif anim in ("proud", "trick", "cheer", "party"):
        deco = [(side + k, y, "✦" if (step + k) % 2 else "✧", GOLD)
                for k, y in enumerate((1, 3, 2)) if (step + k) % 3 != 2]
    elif anim == "surprised":
        deco = [(side, 2, "!", GOLD)]
    return pen_stage(width, x, anim, t, fur, species=species, look=look, outfit=outfit,
                     stage=stage, egg_cracks=egg_cracks, rows=rows, snack=snack,
                     bowl_x=x + 5 if anim == "eat" else None, deco=deco,
                     confetti=1.0 if anim in ("cheer", "party") else 0.0)
