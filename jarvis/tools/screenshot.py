"""screenshot — let the agent SEE: the screen, an app window, a web page, or an image file.

The capture is saved under ``$TMPDIR/jarvis-screenshots/`` (newest 40 kept),
scaled so its long edge is at most 1568 px, and attached to the tool result
as a native image (``utils.tool_images``). Models without vision get the text in
the capture (macOS Vision OCR) instead, with a note saying why.

Modes (first one given wins):

* ``path``   — look at an existing image file
* ``url``    — render a page in headless Chrome (``http://localhost:3000``,
  ``localhost:5173``, or an ``.html`` file) at ``width``×``height``
* ``app``    — the front window of an app, even when other windows cover it
* ``region`` — ``{x, y, width, height}`` in screen points
* nothing    — the whole main display

Screen/app/region captures report how image pixels map to screen points so
the result can be used with ``click_at``.
"""
from __future__ import annotations

import json
import os
import pathlib
import re
import shutil
import signal
import struct
import subprocess
import sys
import tempfile
import time
from typing import Any

SHOT_DIR = pathlib.Path(tempfile.gettempdir()) / "jarvis-screenshots"
MAX_EDGE = 1568          # vision models downscale anything bigger anyway
JPEG_OVER_BYTES = 1_500_000
KEEP_FILES = 40
URL_TIMEOUT = 45.0
_SCREENCAPTURE = "/usr/sbin/screencapture"
_CHROME_APPS = (
    "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
    "/Applications/Chromium.app/Contents/MacOS/Chromium",
    "/Applications/Brave Browser.app/Contents/MacOS/Brave Browser",
    "/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge",
)
_CHROME_BINS = ("google-chrome", "google-chrome-stable", "chromium", "chromium-browser", "chrome", "msedge")
_NATIVE = {".png", ".jpg", ".jpeg", ".gif", ".webp"}
_IMAGE_EXTS = _NATIVE | {".heic", ".tif", ".tiff", ".bmp"}

_CG_FRAMEWORK = "/System/Library/Frameworks/CoreGraphics.framework/CoreGraphics"
_SCREEN_RECORDING_PANE = "x-apple.systempreferences:com.apple.preference.security?Privacy_ScreenCapture"
_permission_requested = False  # prompt + open Settings at most once per run

# Bundle ids of terminals/editors Jarvis is commonly launched from.
_TERMINAL_APPS = {
    "com.apple.Terminal": "Terminal",
    "com.googlecode.iterm2": "iTerm",
    "com.mitchellh.ghostty": "Ghostty",
    "dev.warp.Warp-Stable": "Warp",
    "com.github.wez.wezterm": "WezTerm",
    "net.kovidgoyal.kitty": "kitty",
    "org.alacritty": "Alacritty",
    "io.alacritty": "Alacritty",
    "co.zeit.hyper": "Hyper",
    "com.microsoft.VSCode": "Visual Studio Code",
    "com.todesktop.230313mzl4w4u92": "Cursor",
    "com.exafunction.windsurf": "Windsurf",
    "dev.zed.Zed": "Zed",
    "com.jetbrains.intellij": "IntelliJ IDEA",
    "com.jetbrains.pycharm": "PyCharm",
}
_TERM_PROGRAMS = {"Apple_Terminal": "Terminal", "iTerm.app": "iTerm", "WezTerm": "WezTerm",
                  "ghostty": "Ghostty", "WarpTerminal": "Warp", "vscode": "your editor (VS Code / Cursor)"}

# JXA: main screen size/scale plus the layer-0 windows, front to back.
_WINDOWS_JXA = r"""
ObjC.import('CoreGraphics');
ObjC.import('AppKit');
function run(argv) {
  var onscreen = argv[0] === '1';
  var raw = $.CGWindowListCopyWindowInfo(onscreen ? (1 | 16) : 0, 0);
  var info = ObjC.deepUnwrap(ObjC.castRefToObject(raw)) || [];
  var f = $.NSScreen.mainScreen.frame;
  var out = {screen: [f.size.width, f.size.height],
             scale: $.NSScreen.mainScreen.backingScaleFactor, windows: []};
  for (var i = 0; i < info.length; i++) {
    var w = info[i];
    if ((w.kCGWindowLayer || 0) !== 0) continue;
    var b = w.kCGWindowBounds || {};
    out.windows.push({id: w.kCGWindowNumber, owner: w.kCGWindowOwnerName || '',
                      name: w.kCGWindowName || '', x: b.X, y: b.Y, w: b.Width, h: b.Height});
  }
  return JSON.stringify(out);
}
"""


# ── helpers ───────────────────────────────────────────────────────────────


def _new_path(ext: str = ".png") -> pathlib.Path:
    SHOT_DIR.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y%m%d-%H%M%S")
    return SHOT_DIR / f"shot-{stamp}-{time.monotonic_ns() % 1_000_000:06d}{ext}"


def _prune_old() -> None:
    try:
        files = sorted(SHOT_DIR.glob("shot-*"), key=lambda p: p.stat().st_mtime, reverse=True)
    except OSError:
        return
    for old in files[KEEP_FILES:]:
        try:
            old.unlink()
        except OSError:
            pass


def image_size(path: pathlib.Path) -> tuple[int, int] | None:
    """``(width, height)`` from a PNG/JPEG/GIF/WebP header (no Pillow)."""
    try:
        with open(path, "rb") as fh:
            head = fh.read(64 * 1024)
    except OSError:
        return None
    if head[:8] == b"\x89PNG\r\n\x1a\n" and len(head) >= 24:
        return struct.unpack(">II", head[16:24])
    if head[:6] in (b"GIF87a", b"GIF89a"):
        return struct.unpack("<HH", head[6:10])
    if head[:4] == b"RIFF" and head[8:12] == b"WEBP":
        chunk = head[12:16]
        if chunk == b"VP8X":
            w = int.from_bytes(head[24:27], "little") + 1
            h = int.from_bytes(head[27:30], "little") + 1
            return w, h
        if chunk == b"VP8 ":
            w, h = struct.unpack("<HH", head[26:30])
            return w & 0x3FFF, h & 0x3FFF
        if chunk == b"VP8L":
            bits = int.from_bytes(head[21:25], "little")
            return (bits & 0x3FFF) + 1, ((bits >> 14) & 0x3FFF) + 1
        return None
    if head[:2] == b"\xff\xd8":
        i = 2
        while i + 9 < len(head):
            if head[i] != 0xFF:
                i += 1
                continue
            marker = head[i + 1]
            if marker in (0xC0, 0xC1, 0xC2, 0xC3, 0xC5, 0xC6, 0xC7, 0xC9, 0xCA, 0xCB, 0xCD, 0xCE, 0xCF):
                h, w = struct.unpack(">HH", head[i + 5:i + 9])
                return w, h
            seg = struct.unpack(">H", head[i + 2:i + 4])[0]
            i += 2 + seg
    return None


def _have_sips() -> bool:
    return sys.platform == "darwin" and shutil.which("sips") is not None


def _fit(src: pathlib.Path, *, keep_source: bool) -> pathlib.Path:
    """A copy of ``src`` the API accepts: png/jpeg, long edge ≤ MAX_EDGE, and
    JPEG when the PNG would be heavy. Returns ``src`` when it already fits."""
    size = image_size(src)
    big = size is not None and max(size) > MAX_EDGE
    native = src.suffix.lower() in _NATIVE
    heavy = src.stat().st_size > JPEG_OVER_BYTES
    if not big and native and not heavy:
        return src
    if _have_sips():
        dst = _new_path(".png")
        args = ["sips", "-s", "format", "png"]
        if big or size is None:
            args += ["-Z", str(MAX_EDGE)]
        r = subprocess.run(args + [str(src), "--out", str(dst)], capture_output=True, timeout=30)
        if r.returncode == 0 and dst.exists():
            if dst.stat().st_size > JPEG_OVER_BYTES:
                jpg = dst.with_suffix(".jpg")
                r2 = subprocess.run(["sips", "-s", "format", "jpeg", "-s", "formatOptions", "82",
                                     str(dst), "--out", str(jpg)], capture_output=True, timeout=30)
                if r2.returncode == 0 and jpg.exists():
                    dst.unlink(missing_ok=True)
                    dst = jpg
            if not keep_source:
                src.unlink(missing_ok=True)
            return dst
    convert = shutil.which("magick") or shutil.which("convert")
    if convert:
        dst = _new_path(".png")
        r = subprocess.run([convert, str(src), "-resize", f"{MAX_EDGE}x{MAX_EDGE}>", str(dst)],
                           capture_output=True, timeout=30)
        if r.returncode == 0 and dst.exists():
            if not keep_source:
                src.unlink(missing_ok=True)
            return dst
    return src


def _run_jxa(script: str, *args: str, timeout: float = 10.0) -> dict | None:
    try:
        r = subprocess.run(["osascript", "-l", "JavaScript", "-", *args], input=script,
                           capture_output=True, text=True, timeout=timeout)
        if r.returncode != 0:
            return None
        return json.loads(r.stdout.strip() or "{}")
    except Exception:
        return None


def screen_recording_allowed() -> bool | None:
    """macOS's own answer (``CGPreflightScreenCaptureAccess``) for the app
    that launched Jarvis; None when it can't be asked (not macOS, old OS)."""
    if sys.platform != "darwin":
        return None
    try:
        import ctypes

        fn = ctypes.cdll.LoadLibrary(_CG_FRAMEWORK).CGPreflightScreenCaptureAccess
        fn.restype = ctypes.c_bool
        return bool(fn())
    except Exception:
        return None


def terminal_app_name() -> str:
    """Name of the app macOS attributes our captures to (the terminal)."""
    bundle = os.environ.get("__CFBundleIdentifier", "").strip()
    if bundle in _TERMINAL_APPS:
        return _TERMINAL_APPS[bundle]
    if bundle and sys.platform == "darwin":
        try:
            r = subprocess.run(["mdfind", f"kMDItemCFBundleIdentifier == '{bundle}'"],
                               capture_output=True, text=True, timeout=3)
            first = next((ln for ln in r.stdout.splitlines() if ln.endswith(".app")), "")
            if first:
                return pathlib.Path(first).stem
        except Exception:
            pass
    return _TERM_PROGRAMS.get(os.environ.get("TERM_PROGRAM", ""), "your terminal app")


def request_screen_recording() -> None:
    """Ask macOS for the permission (adds the terminal to the list and shows
    the system prompt the first time) and open the Screen Recording pane."""
    try:
        import ctypes

        fn = ctypes.cdll.LoadLibrary(_CG_FRAMEWORK).CGRequestScreenCaptureAccess
        fn.restype = ctypes.c_bool
        fn()
    except Exception:
        pass
    try:
        subprocess.run(["open", _SCREEN_RECORDING_PANE], capture_output=True, timeout=5)
    except Exception:
        pass


def permission_error() -> str:
    """Explain the missing permission — and, once per run, open the fix."""
    global _permission_requested
    app = terminal_app_name()
    if not _permission_requested:
        _permission_requested = True
        request_screen_recording()
        where = ("I've asked macOS for it and opened System Settings → Privacy & Security → "
                 f"Screen Recording. Tell the user to switch on “{app}” there")
    else:
        where = ("Tell the user to open System Settings → Privacy & Security → Screen Recording "
                 f"and switch on “{app}”")
    return (
        f"ERROR: macOS blocked the capture — Screen Recording permission is off for {app} "
        f"(the app running Jarvis). {where}, then quit and reopen {app}: macOS only applies "
        "the permission after a restart. Until then screenshot(url=…) and "
        "screenshot(path=…) still work, and read_ui shows an app's structure."
    )


def _screencapture(args: list[str], out: pathlib.Path) -> str | None:
    """Run ``screencapture``; return an error message or None on success."""
    try:
        r = subprocess.run([_SCREENCAPTURE, "-x", *args, str(out)], capture_output=True,
                           text=True, timeout=20)
    except subprocess.TimeoutExpired:
        return "ERROR: screencapture timed out"
    msg = ((r.stderr or "") + (r.stdout or "")).strip()
    if r.returncode != 0 or not out.exists() or out.stat().st_size == 0:
        if "could not create image" in msg or not msg:
            return permission_error()
        return f"ERROR: screencapture failed: {msg}"
    return None


def _linux_capture(out: pathlib.Path) -> str | None:
    for cmd in (["grim", str(out)], ["gnome-screenshot", "-f", str(out)],
                ["scrot", "-o", str(out)], ["import", "-window", "root", str(out)]):
        if shutil.which(cmd[0]):
            r = subprocess.run(cmd, capture_output=True, timeout=20)
            if r.returncode == 0 and out.exists() and out.stat().st_size:
                return None
    return ("ERROR: no screen capture tool found (install grim, scrot, gnome-screenshot "
            "or ImageMagick). screenshot(url=…) and screenshot(path=…) still work.")


def find_chrome() -> str | None:
    env = os.environ.get("HARNESS_CHROME", "").strip()
    if env and os.path.exists(env):
        return env
    for p in _CHROME_APPS:
        if os.path.exists(p):
            return p
    for name in _CHROME_BINS:
        found = shutil.which(name)
        if found:
            return found
    return None


def normalize_url(url: str) -> str:
    """``localhost:3000`` → ``http://localhost:3000``; a file path → ``file://``."""
    from ..constants import CWD
    from ..path_resolve import robust_resolve

    u = (url or "").strip()
    if re.match(r"^[a-zA-Z][a-zA-Z0-9+.-]*://", u) or u.startswith(("about:", "data:")):
        return u
    p = robust_resolve(u, CWD)
    if p.exists():
        return p.resolve().as_uri()
    return "http://" + u


def _chrome_capture(url: str, out: pathlib.Path, width: int, height: int, settle_ms: int) -> str | None:
    chrome = find_chrome()
    if not chrome:
        return ("ERROR: screenshot(url=…) needs Google Chrome, Chromium, Brave or Edge "
                "(or set HARNESS_CHROME to a Chrome binary).")
    profile = tempfile.mkdtemp(prefix="jarvis-chrome-")
    cmd = [
        chrome, "--headless", "--use-mock-keychain", f"--user-data-dir={profile}",
        "--no-first-run", "--no-default-browser-check", "--disable-gpu",
        "--hide-scrollbars", "--force-device-scale-factor=1",
        f"--window-size={width},{height}", f"--virtual-time-budget={settle_ms}",
        f"--screenshot={out}", url,
    ]
    proc = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                            stdin=subprocess.DEVNULL, start_new_session=True)
    deadline = time.monotonic() + URL_TIMEOUT
    written_at = None
    try:
        while time.monotonic() < deadline:
            if proc.poll() is not None:
                break
            if out.exists() and out.stat().st_size > 0:
                written_at = written_at or time.monotonic()
                # Chrome sometimes lingers after writing the file.
                if time.monotonic() - written_at > 2.0:
                    break
            time.sleep(0.1)
    finally:
        if proc.poll() is None:
            try:
                os.killpg(proc.pid, signal.SIGKILL)
            except (OSError, ProcessLookupError):
                proc.kill()
            try:
                proc.wait(timeout=5)
            except Exception:
                pass
        shutil.rmtree(profile, ignore_errors=True)
    if not out.exists() or out.stat().st_size == 0:
        return f"ERROR: headless Chrome produced no screenshot for {url} (timed out after {URL_TIMEOUT:.0f}s?)"
    return None


def _coords_note(origin: tuple[float, float], extent_pt: float, img_w: int) -> str:
    k = extent_pt / img_w if img_w else 1.0
    x0, y0 = origin
    if abs(k - 1.0) < 0.005 and not x0 and not y0:
        return "Image pixels = screen points (use them directly with click_at)."
    offs = f"{x0:g} + " if x0 else ""
    offy = f"{y0:g} + " if y0 else ""
    return (f"To click something seen here: click_at(x={offs}px_x × {k:.3f}, "
            f"y={offy}px_y × {k:.3f}).")


# ── tool ──────────────────────────────────────────────────────────────────


def screenshot(app: str = "", url: str = "", path: str = "", region: Any = None,
               width: int = 0, height: int = 0, wait: float = 0) -> str:
    """Capture something and return it as an image the model can see."""
    from ..utils.tool_images import image_marker

    try:
        wait = max(0.0, min(float(wait or 0), 15.0))
    except (TypeError, ValueError):
        wait = 0.0
    note = ""
    coords = ""
    keep_source = False

    if path:
        from ..constants import CWD
        from ..path_resolve import robust_resolve

        src = robust_resolve(str(path), CWD)
        if not src.is_file():
            return f"ERROR: {path} not found"
        if src.suffix.lower() not in _IMAGE_EXTS:
            return f"ERROR: {path} is not an image ({', '.join(sorted(_IMAGE_EXTS))})"
        what = f"image {src.name}"
        raw = src
        keep_source = True
    elif url:
        target = normalize_url(str(url))
        try:
            w = max(320, min(int(width or 1280), 3840))
            h = max(240, min(int(height or 800), 4000))
        except (TypeError, ValueError):
            w, h = 1280, 800
        raw = _new_path()
        err = _chrome_capture(target, raw, w, h, int(max(wait, 3.0) * 1000))
        if err:
            return err
        what = f"{target} at {w}×{h}"
    else:
        if wait:
            time.sleep(wait)
        raw = _new_path()
        if sys.platform != "darwin":
            if app or region:
                return "ERROR: app/region capture is macOS-only here — use screenshot() or screenshot(url=…)."
            err = _linux_capture(raw)
            if err:
                return err
            what = "the screen"
        else:
            allowed = screen_recording_allowed()
            if allowed is False:
                return permission_error()
            info = _run_jxa(_WINDOWS_JXA, "1") or {}
            screen = info.get("screen") or [0, 0]
            if app:
                wins = _match_windows(info.get("windows") or [], str(app))
                if not wins:
                    more = _run_jxa(_WINDOWS_JXA, "0") or {}
                    wins = _match_windows(more.get("windows") or [], str(app))
                if not wins:
                    owners = sorted({w.get("owner") for w in info.get("windows") or [] if w.get("owner")})
                    listed = ", ".join(owners[:20]) or "none visible"
                    return f"ERROR: no window found for app '{app}'. Apps with windows: {listed}"
                win = wins[0]
                err = _screencapture(["-o", "-l", str(win["id"])], raw)
                if err:
                    return err
                title = f" “{win['name']}”" if win.get("name") else ""
                what = f"{win['owner']} window{title}"
                coords = ("win", (win.get("x") or 0, win.get("y") or 0), win.get("w") or 0)
            elif region:
                try:
                    r = region if isinstance(region, dict) else json.loads(str(region))
                    x, y = int(r.get("x", 0)), int(r.get("y", 0))
                    rw, rh = int(r["width"]), int(r["height"])
                except Exception:
                    return 'ERROR: region must look like {"x": 0, "y": 0, "width": 800, "height": 600} (screen points)'
                if rw <= 0 or rh <= 0:
                    return "ERROR: region width and height must be positive"
                err = _screencapture(["-R", f"{x},{y},{rw},{rh}"], raw)
                if err:
                    return err
                what = f"screen region {rw}×{rh} at ({x}, {y})"
                coords = ("region", (x, y), rw)
            else:
                err = _screencapture(["-m"], raw)
                if err:
                    return err
                what = "the main display"
                coords = ("screen", (0, 0), screen[0])
                named = [w for w in info.get("windows") or [] if w.get("name")]
                if allowed is None and len(info.get("windows") or []) >= 3 and not named:
                    note = ("If the image shows only the wallpaper/menu bar, Screen Recording "
                            "permission is missing for the terminal (System Settings → Privacy & "
                            "Security → Screen Recording).")

    final = _fit(raw, keep_source=keep_source)
    _prune_old()
    size = image_size(final)
    dims = f"{size[0]}×{size[1]} px" if size else "unknown size"
    lines = [f"Screenshot of {what} — {dims}", f"saved: {final}"]
    if coords and size:
        _kind, origin, extent = coords
        if extent:
            lines.append(_coords_note(origin, float(extent), size[0]))
    if note:
        lines.append(note)

    from .. import state
    from ..constants.providers import model_supports_images

    if not model_supports_images(state.MODEL):
        lines.append(
            f"NOTE: the current model ({state.MODEL}) can't view images, so here is the text "
            "found in the capture (OCR) instead. Tell the user to switch to a vision model "
            "(/model) if they need you to see the layout."
        )
        try:
            from .ocr import read_image_text

            ocr = read_image_text(str(final))
        except Exception as e:  # pragma: no cover - platform specific
            ocr = f"(OCR unavailable: {type(e).__name__})"
        lines.append("--- OCR text ---")
        lines.append((ocr or "(no text found)")[:5000])
        return "\n".join(lines)

    lines.append(image_marker(final))
    return "\n".join(lines)


def _match_windows(windows: list[dict], app: str) -> list[dict]:
    want = app.strip().lower().removesuffix(".app")
    usable = [w for w in windows if (w.get("w") or 0) >= 60 and (w.get("h") or 0) >= 40]
    exact = [w for w in usable if (w.get("owner") or "").lower() == want]
    if exact:
        return exact
    return [w for w in usable if want in (w.get("owner") or "").lower()
            or want in (w.get("name") or "").lower()]
