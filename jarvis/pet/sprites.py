"""Jarvis's looks: fur palettes, the tiny composer kitty and the big pixel cat.

Two renderers share one set of animations (``idle``, ``work``, ``love``,
``eat``, ``play``, ``sleep``, ``proud``, ``ouch``, ``surprised``, ``trick``,
``wave``, ``happy``):

* :func:`buddy_lines` — a 3-row text kitty (`` /\\_/\\ `` · ``( •ω• )`` ·
  ``(")_(")``) that lives at the right end of the input box;
* :func:`big_cat` — pixel art drawn with half blocks (two pixels per cell),
  used by the ``/pet`` card.

Frames are pure functions of ``(anim, t)`` so animation is just "render at
the current time"; every text line has a fixed cell width, which keeps the
layout still while Jarvis moves.
"""
from __future__ import annotations

import math

from rich.text import Text

# ── fur palettes ──────────────────────────────────────────────────────────
# F fur · D darker stripes · L belly/muzzle · O outline · E eyes · W shine
# M mouth · P pink (nose, ears, blush) · line = color of the text kitty's strokes

FURS: dict[str, dict[str, str]] = {
    "ginger": {"F": "#f4a261", "D": "#dd7b3a", "L": "#fde6cc", "O": "#4a2f22",
               "E": "#231a17", "W": "#ffffff", "M": "#231a17", "P": "#f58ba6", "line": "#f4a261"},
    "midnight": {"F": "#3f4453", "D": "#2e323d", "L": "#6d7486", "O": "#15171c",
                 "E": "#b8f36b", "W": "#ffffff", "M": "#15171c", "P": "#f58ba6", "line": "#a4acbd"},
    "snow": {"F": "#eef0f4", "D": "#d3d8e1", "L": "#ffffff", "O": "#70778a",
             "E": "#4b8bd6", "W": "#ffffff", "M": "#70778a", "P": "#f7a8c4", "line": "#e9ecf2"},
    "smoky": {"F": "#9aa1ad", "D": "#7a818f", "L": "#d9dde3", "O": "#363b45",
              "E": "#f2c14e", "W": "#ffffff", "M": "#363b45", "P": "#f58ba6", "line": "#b7bdc8"},
    "cocoa": {"F": "#a0714f", "D": "#7f5539", "L": "#e6ccb2", "O": "#3b2618",
              "E": "#231a17", "W": "#ffffff", "M": "#231a17", "P": "#f58ba6", "line": "#c9956d"},
    "theme": {},  # follows the active TUI theme's accent (filled in lazily)
}
FUR_ORDER = ("ginger", "midnight", "snow", "smoky", "cocoa", "theme")
FUR_LABELS = {
    "ginger": "ginger tabby", "midnight": "midnight black", "snow": "snow white",
    "smoky": "smoky grey", "cocoa": "cocoa brown", "theme": "theme colors",
}
PINK = "#f58ba6"
GOLD = "#ffd166"


def fur_colors(fur: str) -> dict[str, str]:
    if fur == "theme":
        from ..tui import theme as ui

        acc = ui.ACCENT
        return {"F": acc, "D": ui.blend(acc, ui.BG_0, 0.22), "L": ui.blend(acc, "#ffffff", 0.62),
                "O": ui.blend(acc, ui.BG_0, 0.72), "E": ui.BG_0, "W": "#ffffff",
                "M": ui.BG_0, "P": PINK, "line": acc}
    return FURS.get(fur) or FURS["ginger"]


def next_fur(fur: str) -> str:
    i = FUR_ORDER.index(fur) if fur in FUR_ORDER else -1
    return FUR_ORDER[(i + 1) % len(FUR_ORDER)]


# ── the tiny kitty (composer) ─────────────────────────────────────────────

BUDDY_W = 10  # cells, incl. room for a floating ♥ / z / ✦

_FACES = {
    "idle": "•ω•", "blink": "-ω-", "work": "•_•", "happy": "^ω^", "proud": "^ω^",
    "love": "♥ω♥", "eat": "•ω•", "eat2": "•o•", "play": "^ω^", "sleep": "-ω-",
    "sleep2": "-.-", "ouch": ">_<", "surprised": "°o°", "trick": "•ω•", "wave": "^ω^",
    "sleepy": "=ω=",
}


def blink_at(t: float, seed: float = 0.0) -> bool:
    """Deterministic, irregular blinking (~every 3–5s, 0.15s long)."""
    period = 3.7 + 1.3 * math.sin(seed + math.floor(t / 4.3))
    return (t % period) < 0.15


def buddy_lines(anim: str, t: float, fur: str = "ginger", *, look: int = 0,
                mood: str = "content", eyes_open: bool = True) -> list[Text]:
    """Three fixed-width lines for the composer kitty at time ``t`` (seconds).

    ``look`` shifts the face (-1 left toward your text, +1 right);
    ``mood`` flavors the idle pose (sleepy eyes, …).
    """
    c = fur_colors(fur)
    line, eye = c["line"], "#f5f5f7" if fur != "snow" else "#4b5563"
    dim = "#8b8f98"
    step = int(t * 4)  # 4 beats / second
    face_key = anim if anim in _FACES else "idle"
    extra = ""          # floats top-right of the ears
    extra_style = dim
    paws = '(")_(")'
    tail = "~" if (int(t * 1.6) % 2 == 0) else " "
    side = ""           # right of the face (raised paw)
    shift = look

    if anim == "idle":
        if mood == "sleepy":
            face_key = "sleepy"
        if not eyes_open or blink_at(t):
            face_key = "blink"
    elif anim == "work":
        extra = "." * (1 + step % 3)
        paws = ('(\')_(")', '(")_(\')')[step % 2]
        tail = " "
        shift = (-1, 0, 1, 0)[int(t * 1.5) % 4] if look == 0 else look
        if blink_at(t, 1.3):
            face_key = "blink"
    elif anim == "love":
        extra = ("♥", " ♥", "  ♥", " ♥")[step % 4]
        extra_style = PINK
    elif anim == "eat":
        face_key = "eat2" if step % 2 else "eat"
        paws = '(")_(")'
        side_fish = ("<><", "<>", "<", "")[min(3, int(t * 1.4))]
        tail = side_fish[:3]
    elif anim == "play":
        ball = step % 4
        paws = ('(")_(")●', '(")_(") ●', '(")_(")  ●', '(")_(") ●')[ball][:9]
        tail = ""
        shift = (0, 1, 1, 0)[ball]
    elif anim == "sleep":
        face_key = "sleep2" if (int(t) % 4 == 3) else "sleep"
        extra = ("z", "zZ", "zZz", " Zz", "  z", "")[int(t * 1.5) % 6]
        tail = " "
    elif anim == "proud":
        extra = ("✦", "✧", " ✦", "✧")[step % 4]
        extra_style = GOLD
    elif anim == "ouch":
        extra = ";"
        extra_style = "#7fb4ff"
        shift = (-1, 1)[step % 2] if t < 0.5 else 0
    elif anim == "surprised":
        extra = "!"
        extra_style = GOLD
    elif anim == "wave":
        side = ("/", "\\")[step % 2]
    elif anim == "trick":
        phase = int(t * 5) % 4
        shift = (0, 1, 0, -1)[phase]
        if phase == 2:
            face_key = "_back"
        extra = ("✧", "", "✦", "")[phase]
        extra_style = GOLD

    face = "   " if face_key == "_back" else _FACES[face_key]
    inner = f" {face} "
    if shift < 0:
        inner = f"{face}  "
    elif shift > 0:
        inner = f"  {face}"

    ears = " /\\_/\\ "
    row0 = Text(no_wrap=True)
    row0.append(ears, style=line)
    row0.append(extra[: BUDDY_W - len(ears)], style=extra_style)

    row1 = Text(no_wrap=True)
    row1.append("(", style=line)
    for ch in inner:
        if ch in "ωo._":
            row1.append(ch, style=PINK if ch in "ωo" else line)
        elif ch == "♥":
            row1.append(ch, style=PINK)
        elif ch in "•-^=°><":
            row1.append(ch, style=eye)
        else:
            row1.append(ch, style=line)
    row1.append(")", style=line)
    row1.append(side, style=line)

    row2 = Text(no_wrap=True)
    row2.append(" ")
    paw_part = paws[:9]
    row2.append(paw_part.replace("●", ""), style=line)
    if "●" in paw_part:
        row2 = Text(no_wrap=True)
        row2.append(" ")
        for ch in paw_part:
            row2.append(ch, style=GOLD if ch == "●" else line)
    elif tail:
        row2.append(tail, style=dim if anim != "eat" else "#7fb4ff")

    out = []
    for row in (row0, row1, row2):
        row.truncate(BUDDY_W)
        row.pad_right(BUDDY_W - row.cell_len)
        out.append(row)
    return out


# ── the big pixel cat (/pet card) ─────────────────────────────────────────

_HEAD = (
    "...OO.........OO......",
    "..OFFO.......OFFO.....",
    "..OFPFO.....OFPFO.....",
    ".OFPPFFOOOOOFFPPFO....",
    ".OFFFFFFDFDFFFFFFO....",
    "OFFFFFFFFDFFFFFFFFO...",
    "OFFWEFFFFFFFFFWEFFO...",
    "OFFEEFFFFFFFFFEEFFO...",
    "OFFEEFFFFFFFFFEEFFO...",
    "OFPPFFFFFPFFFFFPPFO...",
    "OFFFFFFFFMFFFFFFFFO...",
    ".OFFFFFFMFMFFFFFFO....",
)
_BODY = (
    (
        "..OOFFFFFFFFFFFOO..OO.",
        "...OFFLLLLLLLFFO..OLLO",
        "..OFFLLLLLLLLLFFO.OFFO",
        "..OFFLLLLLLLLLFFO.OFFO",
        "..OFFLLLLLLLLLFFO.OFFO",
        "..OFFFLLLLLLLFFFOOFFO.",
        "..OFOOFFFFFFFOOFFFFO..",
        "...OOOOOOOOOOOOOOOO...",
    ),
    (
        "..OOFFFFFFFFFFFOO...OO",
        "...OFFLLLLLLLFFO...OLO",
        "..OFFLLLLLLLLLFFO.OFFO",
        "..OFFLLLLLLLLLFFO.OFFO",
        "..OFFLLLLLLLLLFFO.OFFO",
        "..OFFFLLLLLLLFFFOOFFO.",
        "..OFOOFFFFFFFOOFFFFO..",
        "...OOOOOOOOOOOOOOOO...",
    ),
)
BIG_W = 22
BIG_ROWS = 11  # text rows: 2 px headroom + 20 px cat
DECO_W = 7     # text column to the right of the cat (hearts, z, fish, yarn)

# Eye stamps over rows 6–8. Two-pixel shapes sit at cols 3–4 / 14–15 (same
# orientation both sides, so the shine points one way); three-pixel shapes sit
# at cols 2–4 / 14–16 and are mirrored for the right eye ("." = fur).
_EYES2 = {
    "open": ("WE", "EE", "EE"),
    "blink": ("..", "EE", ".."),
    "focus": ("..", "EE", "EE"),
}
_EYES3 = {
    "happy": ("...", ".E.", "E.E"),
    "content": ("...", "E.E", ".E."),
    "love": ("P.P", "PPP", ".P."),
    "ouch": ("E..", ".E.", "E.."),
    "wide": (".E.", "EWE", ".E."),
}
_EYE_FOR_ANIM = {
    "idle": "open", "work": "focus", "happy": "happy", "proud": "happy", "love": "love",
    "eat": "content", "play": "happy", "sleep": "content", "ouch": "ouch",
    "surprised": "wide", "trick": "happy", "wave": "happy",
}


def _stamp(grid: list[list[str]], row: int, col: int, rows: tuple[str, ...], mirror: bool) -> None:
    for dy, pattern in enumerate(rows):
        cells = pattern[::-1] if mirror else pattern
        for dx, ch in enumerate(cells):
            grid[row + dy][col + dx] = "F" if ch == "." else ch


def cat_pixels(anim: str, t: float, *, look: int = 0) -> list[str]:
    """The pixel grid (``2 * BIG_ROWS`` rows of palette keys) at time ``t``."""
    tail = int(t * (4 if anim == "play" else 1.6)) % 2 if anim not in ("sleep", "work") else 0
    grid = [list(r) for r in _HEAD + _BODY[tail]]
    eyes = _EYE_FOR_ANIM.get(anim, "open")
    if anim in ("idle", "work") and blink_at(t, 1.3 if anim == "work" else 0.0):
        eyes = "blink"
    if eyes in _EYES2:
        # Clear the default eyes, then stamp (shifted when glancing).
        _stamp(grid, 6, 3, ("..", "..", ".."), False)
        _stamp(grid, 6, 14, ("..", "..", ".."), False)
        dx = max(-1, min(1, look))
        _stamp(grid, 6, 3 + dx, _EYES2[eyes], False)
        _stamp(grid, 6, 14 + dx, _EYES2[eyes], False)
    else:
        _stamp(grid, 6, 2, _EYES3[eyes], False)
        _stamp(grid, 6, 14, _EYES3[eyes], True)
    # Mouth: open (chomping / meowing) vs the little "ω".
    open_mouth = (anim == "eat" and int(t * 4) % 2 == 1) or anim in ("surprised", "wave") \
        or (anim == "play" and int(t * 4) % 4 == 0)
    if open_mouth:
        grid[10][9] = "M"
        grid[11][8], grid[11][9], grid[11][10] = "M", "P", "M"
    # Canvas = 2 px headroom + the 20 px cat. ``lift`` bobs it upward:
    # breathing (1 px, slow when asleep) or a hop during tricks (2 px).
    if anim == "trick":
        lift = 2 if int(t * 5) % 4 in (1, 2) else 0
    else:
        speed = 0.8 if anim == "sleep" else (3.0 if anim == "play" else 1.2)
        lift = int(t * speed * 2) % 2
    blank = "." * BIG_W
    rows = ["".join(r) for r in grid]
    return [blank] * (2 - lift) + rows + [blank] * lift


def render_pixels(rows: list[str], colors: dict[str, str]) -> list[Text]:
    """Half-block rendering: each text cell shows two stacked pixels."""
    out: list[Text] = []
    height = len(rows) + (len(rows) % 2)
    rows = rows + ["." * BIG_W] * (height - len(rows))
    for y in range(0, height, 2):
        top, bot = rows[y], rows[y + 1]
        line = Text(no_wrap=True)
        for x in range(BIG_W):
            a = colors.get(top[x]) if x < len(top) else None
            b = colors.get(bot[x]) if x < len(bot) else None
            if a and b:
                line.append("█" if a == b else "▀", style=a if a == b else f"{a} on {b}")
            elif a:
                line.append("▀", style=a)
            elif b:
                line.append("▄", style=b)
            else:
                line.append(" ")
        out.append(line)
    return out


def _deco(anim: str, t: float, snack: str = "") -> list[tuple[str, str]]:
    """Per-row text to the right of the big cat: (text, color)."""
    rows = [("", "")] * BIG_ROWS
    rows = list(rows)
    step = int(t * 3)
    if anim == "love":
        for k in range(3):
            y = 5 - ((step + k * 2) % 6)
            if 0 <= y < BIG_ROWS:
                rows[y] = ((" " * ((k * 2 + step) % 4)) + "♥", PINK)
    elif anim == "sleep":
        zs = ("z", " Z", "  z", "   Z")
        for k in range(3):
            y = 4 - ((step + k) % 5)
            if 0 <= y < BIG_ROWS:
                rows[y] = (zs[(step + k) % 4], "#9aa3b5")
    elif anim == "eat":
        bites = min(3, int(t * 1.4))
        food = {"fish": ("<><", "<>", "<", ""), "milk": ("(_)", "(_)", "(_", ""),
                "cookie": ("(◍)", "(◍", "(", "")}.get(snack or "fish", ("<><", "<>", "<", ""))
        rows[5] = (food[bites], "#7fb4ff" if snack in ("fish", "") else "#e9c46a")
    elif anim == "play":
        pos = (8, 6, 5, 6)[int(t * 4) % 4]
        rows[pos] = (("  ", " ", "", " ")[int(t * 4) % 4] + "●", GOLD)
    elif anim in ("proud", "trick"):
        for k, y in enumerate((1, 3, 0)):
            if (step + k) % 3 != 2:
                rows[y] = (" " * k + ("✦" if (step + k) % 2 else "✧"), GOLD)
    elif anim == "surprised":
        rows[1] = (" !", GOLD)
    elif anim == "ouch":
        rows[2] = (" ;", "#7fb4ff")
    elif anim == "wave":
        rows[4] = ((" )", "  ))")[step % 2], "#9aa3b5")
    elif anim == "work":
        rows[2] = (" " + "." * (1 + step % 3), "#9aa3b5")
    return rows


def big_cat(anim: str, t: float, fur: str = "ginger", *, look: int = 0,
            snack: str = "") -> list[Text]:
    """The /pet card's cat: ``BIG_ROWS`` lines of ``BIG_W + DECO_W`` cells."""
    colors = fur_colors(fur)
    lines = render_pixels(cat_pixels(anim, t, look=look), colors)
    deco = _deco(anim, t, snack)
    out = []
    for line, (text, color) in zip(lines, deco):
        line.append(text[:DECO_W], style=color)
        line.pad_right(BIG_W + DECO_W - line.cell_len)
        out.append(line)
    return out


# ── the mini cat that roams the sidebar pen ───────────────────────────────

MINI_W, MINI_H = 16, 14  # pixels (8 cols of travel per 8 px… 1 col = 1 px)
STAGE_ROWS = 8           # text rows: 2 px headroom for hops + the 14 px cat

_MINI_HEAD = (
    ".OO.......OO....",
    "OPFO.....OFPO...",
    "OFFFOOOOOFFFO...",
    "OFFFFDFDFFFFO...",
    "OFWEFFFFFWEFO...",
    "OFEEFFFFFEEFO...",
    "OFPFFFPFFFPFO...",
    ".OFFFMFMFFFO....",
    "..OOFFFFFOO..OO.",
)
_MINI_SIT = (
    "..OFLLLLLFO.OFFO",
    ".OFLLLLLLLFO.OFO",
    ".OFLLLLLLLFOOFO.",
    ".OFFOLLLOFFOFO..",
    "..OOOOOOOOOO....",
)
_MINI_WALK = (
    (
        "..OFLLLLLFO.OFFO",
        ".OFLLLLLLLFO.OFO",
        ".OFLLLLLLLFOOFO.",
        ".OFOOFFFOOFO....",
        ".OO..OOO...OO...",
    ),
    (
        "..OFLLLLLFO..OFO",
        ".OFLLLLLLLFO.OFO",
        ".OFLLLLLLLFOOFO.",
        "..OFOOFOOFOO....",
        "..OO..OO..OO....",
    ),
)
# Eye stamps over head rows 4–5; 2-px shapes at cols 2 / 9, 3-px at 1 / 9.
_MINI_EYES2 = {"open": ("WE", "EE"), "blink": ("..", "EE"), "focus": ("EE", "EE")}
_MINI_EYES3 = {
    "happy": (".E.", "E.E"), "content": ("E.E", ".E."), "love": ("P.P", ".P."),
    "ouch": ("EE.", "..E"), "wide": ("EEE", "EWE"),
}


def mini_cat(anim: str, t: float, *, walking: bool = False, facing: int = -1,
             look: int = 0) -> list[str]:
    """16×14 pixel rows. ``facing`` -1 = tail on the right (walking left)."""
    body = _MINI_WALK[int(t * 6) % 2] if walking else _MINI_SIT
    if anim == "play" and not walking:
        body = _MINI_WALK[int(t * 4) % 2]
    grid = [list(r) for r in _MINI_HEAD + body]
    eyes = _EYE_FOR_ANIM.get(anim, "open")
    if anim in ("idle", "work") and blink_at(t, 2.1 if anim == "work" else 0.4):
        eyes = "blink"
    if eyes not in _MINI_EYES2 and eyes not in _MINI_EYES3:
        eyes = "open"
    if eyes in _MINI_EYES2:
        _stamp(grid, 4, 2, ("..", ".."), False)
        _stamp(grid, 4, 9, ("..", ".."), False)
        dx = max(-1, min(1, look))
        _stamp(grid, 4, 2 + dx, _MINI_EYES2[eyes], False)
        _stamp(grid, 4, 9 + dx, _MINI_EYES2[eyes], False)
    else:
        _stamp(grid, 4, 1, _MINI_EYES3[eyes], False)
        _stamp(grid, 4, 9, _MINI_EYES3[eyes], True)
    chomp = (anim == "eat" and int(t * 4) % 2) or anim in ("surprised", "wave")
    if chomp:
        grid[7][5], grid[7][6], grid[7][7] = "M", "P", "M"
    rows = ["".join(r) for r in grid]
    if facing > 0:
        rows = [r[::-1] for r in rows]
    return rows


def _pixel_cell(a: str | None, b: str | None) -> tuple[str, str]:
    if a and b:
        return ("█", a) if a == b else ("▀", f"{a} on {b}")
    if a:
        return ("▀", a)
    if b:
        return ("▄", b)
    return (" ", "")


def pen_stage(width: int, cat_x: int, anim: str, t: float, fur: str = "ginger", *,
              walking: bool = False, facing: int = -1, look: int = 0, lift: int = 0,
              ball_x: int | None = None, bowl_x: int | None = None, snack: str = "",
              deco: list[tuple[int, int, str, str]] | None = None) -> list[Text]:
    """The pen's floor-level scene: ``STAGE_ROWS`` lines, ``width`` cells each.

    ``cat_x`` is the cat's left column; ``lift`` (0–2 px) makes it hop.
    ``deco`` are text glyphs ``(col, row, text, color)`` drawn only where no
    pixel is (hearts, z's, sparkles float around the cat, never over it).
    """
    colors = fur_colors(fur)
    colors = dict(colors, G=GOLD, B="#7fb4ff", K="#c9d1d9", C="#e9c46a", Y="#f4f1ea")
    h = STAGE_ROWS * 2
    canvas = [[None] * width for _ in range(h)]

    def blit(rows: list[str], x0: int, y0: int) -> None:
        for dy, row in enumerate(rows):
            y = y0 + dy
            if not 0 <= y < h:
                continue
            for dx, key in enumerate(row):
                x = x0 + dx
                if key != "." and 0 <= x < width:
                    canvas[y][x] = colors.get(key)

    blit(mini_cat(anim, t, walking=walking, facing=facing, look=look),
         cat_x, h - MINI_H - max(0, min(2, lift)))
    if bowl_x is not None:  # in front of the paws
        food = {"milk": "Y", "cookie": "C"}.get(snack, "B")
        blit([f".{food}{food}{food}.", "OKKKO", ".OOO."], bowl_x, h - 3)
    if ball_x is not None:
        bounce = (0, 1, 2, 1)[int(t * 8) % 4]
        blit(["GG", "GG"], ball_x, h - 2 - bounce)

    cells = []
    for row in range(STAGE_ROWS):
        top, bot = canvas[row * 2], canvas[row * 2 + 1]
        cells.append([_pixel_cell(top[x], bot[x]) for x in range(width)])
    for col, row, text, color in deco or []:
        for i, ch in enumerate(text):
            x = col + i
            if 0 <= row < STAGE_ROWS and 0 <= x < width and cells[row][x][0] == " ":
                cells[row][x] = (ch, color)
    out = []
    for row in cells:
        line = Text(no_wrap=True)
        for ch, style in row:
            line.append(ch, style=style or None)
        out.append(line)
    return out
