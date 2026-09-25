"""screenshot tool: capture modes, image plumbing into tool results, provider
conversion and context pruning."""
import base64
import importlib
import pathlib
import shutil
import struct
import sys
import zlib

import pytest

from jarvis import state

# ``jarvis.tools.screenshot`` is the tool function; this is its module.
shot = importlib.import_module("jarvis.tools.screenshot")
from jarvis.utils.tool_images import (
    image_marker, split_image_markers, split_tool_result, tool_result_content,
)


def _png(path: pathlib.Path, w: int, h: int) -> pathlib.Path:
    def chunk(kind: bytes, data: bytes) -> bytes:
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data))

    raw = b"".join(b"\x00" + b"\x40\x80\xc0" * w for _ in range(h))
    path.write_bytes(b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0))
                     + chunk(b"IDAT", zlib.compress(raw)) + chunk(b"IEND", b""))
    return path


@pytest.fixture(autouse=True)
def _shot_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(shot, "SHOT_DIR", tmp_path / "shots")
    monkeypatch.setattr(state, "MODEL", "claude-opus-5-5")  # has vision
    monkeypatch.setattr(shot, "screen_recording_allowed", lambda: True)


def test_image_size_reads_png_and_gif_headers(tmp_path):
    assert shot.image_size(_png(tmp_path / "a.png", 37, 21)) == (37, 21)
    gif = tmp_path / "a.gif"
    gif.write_bytes(b"GIF89a" + struct.pack("<HH", 640, 480) + b"\x00" * 20)
    assert shot.image_size(gif) == (640, 480)


def test_marker_becomes_native_image_block(tmp_path):
    img = _png(tmp_path / "x.png", 4, 3)
    out = f"Screenshot of x — 4×3 px\nsaved: {img}\n{image_marker(img)}"
    text, paths = split_image_markers(out)
    assert paths == [str(img)] and "jarvis:image" not in text
    content = tool_result_content(out)
    assert [b["type"] for b in content] == ["text", "image"]
    assert base64.b64decode(content[1]["source"]["data"]) == img.read_bytes()
    assert content[1]["source"]["media_type"] == "image/png"
    assert tool_result_content("plain output") == "plain output"
    # A missing file degrades to a text note instead of a broken block.
    gone = tool_result_content(image_marker(tmp_path / "nope.png"))
    assert gone[0]["type"] == "text" and "could not be attached" in gone[0]["text"]


def test_path_mode_attaches_the_file(tmp_path):
    img = _png(tmp_path / "ui.png", 800, 600)
    out = shot.screenshot(path=str(img))
    assert out.startswith("Screenshot of image ui.png — 800×600 px")
    assert image_marker(img) in out  # small + native → sent as-is, not copied
    assert shot.screenshot(path=str(tmp_path / "missing.png")).startswith("ERROR")
    (tmp_path / "notes.txt").write_text("x")
    assert "not an image" in shot.screenshot(path=str(tmp_path / "notes.txt"))


def test_models_without_vision_get_ocr_text(tmp_path, monkeypatch):
    import jarvis.tools.ocr as ocr

    monkeypatch.setattr(state, "MODEL", "mimo-v2.5-free")
    monkeypatch.setattr(ocr, "read_image_text", lambda p: "Sign in\nForgot password?")
    out = shot.screenshot(path=str(_png(tmp_path / "ui.png", 10, 10)))
    assert "can't view images" in out and "Forgot password?" in out
    assert "jarvis:image" not in out


@pytest.mark.skipif(sys.platform != "darwin" or not shutil.which("sips"), reason="needs macOS sips")
def test_screen_capture_is_scaled_and_maps_to_click_coords(tmp_path, monkeypatch):
    monkeypatch.setattr(shot, "_run_jxa", lambda script, *a, **k: {
        "screen": [1728, 1117], "scale": 2,
        "windows": [{"id": 5, "owner": "Safari", "name": "Docs", "x": 10, "y": 40, "w": 1200, "h": 800}],
    })

    def fake_capture(args, out):
        _png(out, 3456, 2234)  # a Retina main display
        return None

    monkeypatch.setattr(shot, "_screencapture", fake_capture)
    out = shot.screenshot()
    first = out.splitlines()[0]
    assert first.startswith("Screenshot of the main display — 1568×")
    assert "px_x × 1.102" in out  # 1728 pt / 1568 px
    saved = pathlib.Path(out.split("saved: ", 1)[1].splitlines()[0])
    assert saved.exists() and max(shot.image_size(saved)) == 1568
    assert len(list((tmp_path / "shots").glob("shot-*"))) == 1  # the raw capture was replaced


def test_app_window_lookup_and_permission_errors(monkeypatch):
    monkeypatch.setattr(shot.sys, "platform", "darwin")
    windows = [
        {"id": 1, "owner": "Google Chrome", "name": "Docs", "x": 0, "y": 25, "w": 1400, "h": 900},
        {"id": 2, "owner": "Safari", "name": "", "x": 0, "y": 25, "w": 30, "h": 30},  # too small
        {"id": 3, "owner": "Safari", "name": "Apple", "x": 100, "y": 60, "w": 1000, "h": 700},
    ]
    assert [w["id"] for w in shot._match_windows(windows, "safari")] == [3]
    assert [w["id"] for w in shot._match_windows(windows, "chrome")] == [1]

    monkeypatch.setattr(shot, "_run_jxa", lambda *a, **k: {"screen": [1440, 900], "windows": windows})
    err = shot.screenshot(app="Figma")
    assert err.startswith("ERROR: no window found for app 'Figma'") and "Safari" in err

    calls = []

    class R:
        returncode, stdout, stderr = 1, "", "could not create image from window"

    monkeypatch.setattr(shot.subprocess, "run", lambda cmd, **k: calls.append(cmd) or R())
    err = shot.screenshot(app="Safari")
    assert "Screen Recording" in err
    assert calls[-1][:5] == ["/usr/sbin/screencapture", "-x", "-o", "-l", "3"]


def test_url_mode_normalizes_and_clamps(tmp_path, monkeypatch):
    seen = {}

    def fake_chrome(url, out, w, h, settle_ms):
        seen.update(url=url, w=w, h=h, settle=settle_ms)
        _png(out, w, h)
        return None

    monkeypatch.setattr(shot, "_chrome_capture", fake_chrome)
    out = shot.screenshot(url="localhost:5173", width=99999, height=10)
    assert seen == {"url": "http://localhost:5173", "w": 3840, "h": 240, "settle": 3000}
    assert "jarvis:image" in out
    page = tmp_path / "index.html"
    page.write_text("<h1>hi</h1>")
    assert shot.normalize_url(str(page)).startswith("file://")


@pytest.mark.skipif(not shot.find_chrome(), reason="needs Chrome/Chromium")
def test_real_headless_chrome_render(tmp_path):
    page = tmp_path / "p.html"
    page.write_text("<body style='background:#123'><h1 style='color:white'>Hello</h1></body>")
    out = shot.screenshot(url=str(page), width=640, height=400)
    assert "— 640×400 px" in out.splitlines()[0], out


# ── providers ─────────────────────────────────────────────────────────────


def _conversation(tmp_path):
    img = _png(tmp_path / "s.png", 2, 2)
    return [
        {"role": "user", "content": "how does the page look?"},
        {"role": "assistant", "content": [
            {"type": "tool_use", "id": "t1", "name": "screenshot", "input": {"url": "localhost:3000"}},
        ]},
        {"role": "user", "content": [
            {"type": "tool_result", "tool_use_id": "t1",
             "content": tool_result_content(f"Screenshot — 2×2 px\n{image_marker(img)}")},
        ]},
    ]


def test_openai_style_providers_get_image_after_tool_message(tmp_path):
    from jarvis.auth.opencode_client import _anthropic_messages_to_openai

    out = _anthropic_messages_to_openai(_conversation(tmp_path))
    roles = [m["role"] for m in out]
    assert roles == ["user", "assistant", "tool", "user"]  # tool msg right after tool_calls
    assert out[2]["content"] == "Screenshot — 2×2 px"
    parts = out[3]["content"]
    assert parts[1]["type"] == "image_url"
    assert parts[1]["image_url"]["url"].startswith("data:image/png;base64,")


def test_codex_responses_input_carries_the_image(tmp_path):
    from jarvis.auth.codex_client import _anthropic_messages_to_responses_input

    items = _anthropic_messages_to_responses_input(_conversation(tmp_path))
    out_item = next(i for i in items if i.get("type") == "function_call_output")
    assert out_item["output"] == "Screenshot — 2×2 px"
    img_msg = items[-1]
    assert img_msg["role"] == "user"
    assert img_msg["content"][1]["type"] == "input_image"


def test_old_screenshots_are_pruned_from_requests(tmp_path):
    from jarvis.repl.trim import _content_chars, prune_tool_images

    msgs = []
    for i in range(5):
        msgs += _conversation(tmp_path)[1:]
        msgs[-2]["content"][0]["id"] = f"t{i}"
        msgs[-1]["content"][0]["tool_use_id"] = f"t{i}"

    def images(ms):
        return sum(len(split_tool_result(m["content"][0]["content"])[1])
                   for m in ms if m["role"] == "user")

    kept = prune_tool_images(msgs, vision=True)
    assert images(kept) == 3 and images(msgs) == 5  # input untouched
    assert "earlier screenshot removed" in split_tool_result(kept[1]["content"][0]["content"])[0]
    assert images(prune_tool_images(msgs, vision=False)) == 0
    # Budgeting counts an image as a fixed estimate, never its base64 length.
    assert _content_chars(msgs[1]["content"]) < 9_000


def test_tool_loop_puts_the_image_into_the_conversation(tmp_path, monkeypatch):
    """A screenshot tool_use goes through render_assistant and lands in
    state.messages as a tool_result holding text + a native image block."""
    from types import SimpleNamespace

    from jarvis.repl import render

    img = _png(tmp_path / "page.png", 64, 48)
    monkeypatch.setattr(state, "messages", [
        {"role": "user", "content": "check the page"},
        {"role": "assistant", "content": [{"type": "tool_use", "id": "s1", "name": "screenshot",
                                           "input": {"path": str(img)}}]},
    ])
    monkeypatch.setattr(state, "_assistant_stream_ui_active", False, raising=False)
    resp = SimpleNamespace(stop_reason="tool_use", content=[
        SimpleNamespace(type="tool_use", id="s1", name="screenshot", input={"path": str(img)}),
    ])
    assert render.render_assistant(resp) is True
    result = state.messages[-1]["content"][0]
    assert result["type"] == "tool_result" and result["tool_use_id"] == "s1"
    text, images = split_tool_result(result["content"])
    assert text.startswith("Screenshot of image page.png — 64×48 px")
    assert "jarvis:image" not in text
    assert len(images) == 1 and images[0]["source"]["media_type"] == "image/png"


def test_missing_permission_asks_macos_once_and_names_the_terminal(monkeypatch):
    asked = []
    monkeypatch.setattr(shot.sys, "platform", "darwin")
    monkeypatch.setattr(shot, "screen_recording_allowed", lambda: False)
    monkeypatch.setattr(shot, "request_screen_recording", lambda: asked.append(1))
    monkeypatch.setenv("__CFBundleIdentifier", "com.googlecode.iterm2")
    monkeypatch.setattr(shot, "_run_jxa", lambda *a, **k: pytest.fail("must not capture"))

    first = shot.screenshot(app="Finder")
    assert first.startswith("ERROR: macOS blocked the capture — Screen Recording permission is off for iTerm")
    assert "opened System Settings" in first and "quit and reopen iTerm" in first
    second = shot.screenshot()
    assert "Tell the user to open System Settings" in second
    assert asked == [1]  # the prompt / Settings pane opens once, not on every call
    # url/path captures don't need the permission.
    monkeypatch.setattr(shot, "_chrome_capture", lambda url, out, w, h, ms: _png(out, w, h) and None)
    assert "jarvis:image" in shot.screenshot(url="localhost:3000")


def test_terminal_name_fallbacks(monkeypatch):
    monkeypatch.setenv("__CFBundleIdentifier", "com.apple.Terminal")
    assert shot.terminal_app_name() == "Terminal"
    monkeypatch.delenv("__CFBundleIdentifier")
    monkeypatch.setenv("TERM_PROGRAM", "ghostty")
    assert shot.terminal_app_name() == "Ghostty"
    monkeypatch.setenv("TERM_PROGRAM", "something-else")
    assert shot.terminal_app_name() == "your terminal app"


def test_check_permissions_reports_screen_recording(monkeypatch):
    from jarvis.tools.mac import ui as mac_ui

    class R:
        returncode, stdout, stderr = 0, "Finder\n", ""

    monkeypatch.setattr(mac_ui.subprocess, "run", lambda *a, **k: R())
    monkeypatch.setenv("__CFBundleIdentifier", "com.apple.Terminal")
    monkeypatch.setattr(shot, "screen_recording_allowed", lambda: False)
    out = mac_ui.check_permissions()
    assert "Accessibility OK" in out and "SCREEN RECORDING OFF for Terminal" in out
    monkeypatch.setattr(shot, "screen_recording_allowed", lambda: True)
    assert "Screen Recording OK (Terminal)" in mac_ui.check_permissions()
