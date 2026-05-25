"""QR ASCII helper tests."""
from jarvis.web.qr_ascii import qr_ascii, qr_dimensions


def test_qr_ascii_returns_matrix():
    url = "http://127.0.0.1:8765/?token=test"
    out = qr_ascii(url, scale=1)
    assert out
    lines = out.splitlines()
    assert len(lines) >= 10
    width = len(lines[0])
    assert all(len(line) == width for line in lines)
    # Half-block QR uses block-drawing glyphs.
    assert any(ch in out for ch in "█▀▄")


def test_qr_default_is_smallest_scale():
    """Default scale must be 1 — the widget pins its own size, so anything
    larger gets stretched by Textual into unreadable horizontal bars."""
    url = "http://127.0.0.1:8765/?token=test"
    assert qr_ascii(url) == qr_ascii(url, scale=1)


def test_qr_scales_up():
    url = "http://127.0.0.1:8765/?token=test"
    s1 = qr_ascii(url, scale=1)
    s2 = qr_ascii(url, scale=2)
    lines1 = s1.splitlines()
    lines2 = s2.splitlines()
    assert len(lines2) == len(lines1) * 2
    assert len(lines2[0]) == len(lines1[0]) * 2


def test_qr_dimensions_matches_output():
    url = "http://127.0.0.1:8765/?token=test"
    art = qr_ascii(url, scale=1)
    cols, rows = qr_dimensions(art)
    lines = art.splitlines()
    assert rows == len(lines)
    assert cols == len(lines[0])
