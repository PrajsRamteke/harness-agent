"""Square ASCII QR codes for the TUI web remote overlay."""
from __future__ import annotations

import io

# Half-block QR keeps modules visually SQUARE in monospace terminals — each
# terminal cell is ~2:1 (tall:wide), so packing 2 vertical modules per char
# (and 1 horizontal) cancels the aspect ratio. Quadrant blocks pack 2×2 and
# end up rendering modules as 2:1 (stretched vertically) which breaks scans.
#
# ``_QUIET_BORDER = 0`` — we lean on the themed Panel border + the widget's
# dark background to act as the visual quiet zone. Phone cameras handle this
# fine when the QR is rendered on a dark UI surround.
_DEFAULT_SCALE = 1
_QUIET_BORDER = 0

# 4-bit index → quadrant block. Bits are (TL, TR, BL, BR) where a set bit
# means "light" (printed character) — we invert so dark modules become the
# terminal background and light modules become foreground glyphs.
_QUAD_CHARS = (
    " ", "▗", "▖", "▄",
    "▝", "▐", "▞", "▟",
    "▘", "▚", "▌", "▙",
    "▀", "▜", "▛", "█",
)


def _quadrant_ascii(data: str) -> str:
    import qrcode

    qr = qrcode.QRCode(
        border=_QUIET_BORDER,
        box_size=1,
        error_correction=qrcode.constants.ERROR_CORRECT_M,
    )
    qr.add_data(data)
    qr.make(fit=True)
    matrix = [list(row) for row in qr.get_matrix()]
    if not matrix:
        return ""
    h = len(matrix)
    w = len(matrix[0])
    # Pad to even dims by appending off-modules so every 2×2 block is full.
    if w % 2 == 1:
        for row in matrix:
            row.append(False)
        w += 1
    if h % 2 == 1:
        matrix.append([False] * w)
        h += 1
    lines: list[str] = []
    for r in range(0, h, 2):
        row_a = matrix[r]
        row_b = matrix[r + 1]
        chars: list[str] = []
        for c in range(0, w, 2):
            tl = 0 if row_a[c] else 1
            tr = 0 if row_a[c + 1] else 1
            bl = 0 if row_b[c] else 1
            br = 0 if row_b[c + 1] else 1
            chars.append(_QUAD_CHARS[(tl << 3) | (tr << 2) | (bl << 1) | br])
        lines.append("".join(chars))
    return "\n".join(lines)


def _halfblock_ascii(data: str) -> str:
    """Half-block fallback — useful if a terminal mis-renders quadrant glyphs."""
    import qrcode

    qr = qrcode.QRCode(
        border=_QUIET_BORDER,
        box_size=1,
        error_correction=qrcode.constants.ERROR_CORRECT_M,
    )
    qr.add_data(data)
    qr.make(fit=True)
    buf = io.StringIO()
    qr.print_ascii(out=buf, invert=True, tty=False)
    return buf.getvalue().rstrip("\n")


def _scale(text: str, factor: int) -> str:
    if factor <= 1:
        return text
    lines: list[str] = []
    for line in text.splitlines():
        wide = "".join(ch * factor for ch in line)
        lines.extend([wide] * factor)
    return "\n".join(lines)


def qr_ascii(
    data: str,
    *,
    scale: int = _DEFAULT_SCALE,
    style: str = "halfblock",
) -> str:
    """Return a plain-text QR suitable for Textual ``Static`` (no Rich styling).

    ``style="halfblock"`` (default) → square modules, reliable scanning.
    ``style="quadrant"`` → 2×2 blocks; chars are half the count but render
    visually stretched in standard terminals, so use only when you've verified
    the target terminal has square cells.
    """
    text = (data or "").strip()
    if not text:
        return ""
    try:
        factor = max(1, min(int(scale), 3))
        renderer = _quadrant_ascii if style == "quadrant" else _halfblock_ascii
        return _scale(renderer(text), factor)
    except Exception:
        return ""


def qr_dimensions(art: str) -> tuple[int, int]:
    """Return (cols, rows) of a rendered QR — used to size the Textual widget."""
    if not art:
        return (0, 0)
    lines = art.split("\n")
    cols = max((len(line) for line in lines), default=0)
    return cols, len(lines)
