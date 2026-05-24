"""Read accessibility UI tree and click elements on Windows."""
import sys
import time

from ._ps import run_ps


def _desktop():
    if sys.platform != "win32":
        return None
    try:
        from pywinauto import Desktop  # type: ignore
        return Desktop(backend="uia")
    except ImportError:
        return None
    except Exception:
        return None


def _match_window(desktop, app: str):
    if app:
        for win in desktop.windows():
            title = win.window_text() or ""
            if app.lower() in title.lower():
                return win
        return None
    try:
        return desktop.get_active()
    except Exception:
        return None


def _walk(control, depth: int, max_depth: int, lines: list, max_lines: int):
    if depth > max_depth or len(lines) >= max_lines:
        return
    try:
        info = control.element_info
        name = (info.name or "").strip()
        role = (info.control_type or "").strip()
        val = ""
        try:
            val = (control.window_text() or "").strip()
        except Exception:
            pass
        rect = info.rectangle
        cx = cy = 0
        if rect.width() and rect.height():
            cx = rect.left + rect.width() // 2
            cy = rect.top + rect.height() // 2
        indent = "  " * depth
        label = name or val or "(unnamed)"
        lines.append(f"{indent}{role} '{label}' @ ({cx},{cy})")
        for child in control.children():
            _walk(child, depth + 1, max_depth, lines, max_lines)
            if len(lines) >= max_lines:
                return
    except Exception:
        return


def read_ui(app: str = "", max_depth: int = 7, max_lines: int = 400, max_chars: int = 14000) -> str:
    if sys.platform != "win32":
        return "ERROR: read_ui requires Windows (windows branch)."
    desktop = _desktop()
    if desktop is None:
        return (
            "ERROR: UI Automation unavailable. Run: pip install pywinauto\n"
            "Or use run_powershell for app-specific automation."
        )
    try:
        win = _match_window(desktop, app.strip())
        if win is None:
            return f"ERROR: no window found for '{app or 'frontmost'}'"
        lines: list[str] = []
        _walk(win, 0, max(1, int(max_depth)), lines, max(10, int(max_lines)))
        out = "\n".join(lines) or "(empty UI tree)"
        if len(out) > max_chars:
            out = out[:max_chars] + f"\n… [truncated, {len(out)} chars total]"
        return out
    except Exception as e:
        return f"ERROR: {e}"


def click_element(app: str, query: str, role: str = "", nth: int = 1) -> str:
    desktop = _desktop()
    if desktop is None:
        return "ERROR: pywinauto required for click_element"
    query = (query or "").strip().lower()
    if not query:
        return "ERROR: query required"
    try:
        win = _match_window(desktop, app.strip())
        if win is None:
            return f"ERROR: no window for '{app}'"
        matches = []

        def scan(control):
            try:
                info = control.element_info
                name = (info.name or "").lower()
                ctrl_role = (info.control_type or "").lower()
                text = ""
                try:
                    text = (control.window_text() or "").lower()
                except Exception:
                    pass
                if role and role.lower() not in ctrl_role:
                    pass
                elif query in name or query in text:
                    matches.append(control)
                for child in control.children():
                    scan(child)
            except Exception:
                return

        scan(win)
        if not matches:
            return f"ERROR: no element matching '{query}'"
        idx = max(1, int(nth)) - 1
        if idx >= len(matches):
            return f"ERROR: only {len(matches)} match(es), nth={nth} invalid"
        target = matches[idx]
        target.click_input()
        return f"clicked '{query}' (#{idx + 1})"
    except Exception as e:
        return f"ERROR: {e}"


def wait(seconds: float = 0.8) -> str:
    time.sleep(max(0.0, min(float(seconds), 10.0)))
    return f"slept {seconds}s"


def check_permissions() -> str:
    if sys.platform != "win32":
        return "ERROR: Windows desktop tools require Windows (windows branch)."
    desktop = _desktop()
    if desktop is None:
        try:
            import pywinauto  # type: ignore  # noqa: F401
            installed = True
        except ImportError:
            installed = False
        if not installed:
            return (
                "UI automation package missing.\n"
                "Run: pip install pywinauto\n"
                "Then retry read_ui / click_element."
            )
        return (
            "UI Automation failed to start.\n"
            "Run the terminal as a normal user session (not mixed elevated/non-elevated)."
        )
    try:
        title = desktop.get_active().window_text()
        return f"UI Automation OK. Active window: {title or '(unknown)'}"
    except Exception as e:
        return (
            "UI Automation may be blocked.\n"
            "Run the terminal as the same user session (not elevated vs non-elevated mix).\n"
            f"Error: {e}"
        )
