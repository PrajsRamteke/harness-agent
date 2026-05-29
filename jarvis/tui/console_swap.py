"""Swap the live ``console`` object across all loaded jarvis.* modules.

Lives in its own module so both ``tui/app.py`` and the web-remote mixin can use
it without importing each other (which would form a cycle).
"""
import sys


def _swap_console_everywhere(tui_console):
    """Replace every `console` module attribute across loaded jarvis.* modules."""
    import jarvis.console as _cmod
    _cmod.console = tui_console
    for name, mod in list(sys.modules.items()):
        if not name.startswith("jarvis.") or mod is None:
            continue
        if getattr(mod, "console", None) is not None and name != "jarvis.console":
            try:
                setattr(mod, "console", tui_console)
            except Exception:
                pass
