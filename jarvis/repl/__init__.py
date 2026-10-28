# Re-exports are resolved on first use (PEP 562): importing any submodule
# (``from ..repl import api_errors``) must not pull in ``stream`` and with it
# anthropic / openai / mcp — that alone was ~1 s of TUI startup.
_EXPORTS = {
    "build_system": ".system",
    "call_claude_stream": ".stream",
    "_scrub_hallucinations": ".hallucination",
    "render_assistant": ".render",
    "welcome_banner": ".banners",
    "header_panel": ".banners",
    "estimated_cost": ".stats",
}


def __getattr__(name):
    mod = _EXPORTS.get(name)
    if mod is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    from importlib import import_module

    value = getattr(import_module(mod, __name__), name)
    globals()[name] = value
    return value
