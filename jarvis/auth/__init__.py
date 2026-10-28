# Re-exports are resolved on first use (PEP 562): importing a light submodule
# (``from ..auth import models_dev``, done while the TUI starts) must not run
# ``client`` and with it anthropic + openai — ~450 ms before the first frame.
_EXPORTS = {
    "prompt_for_key": ".api_key",
    "load_key": ".api_key",
    "prompt_for_openrouter_key": ".openrouter",
    "load_openrouter_key": ".openrouter",
    "prompt_for_opencode_key": ".opencode",
    "load_opencode_key": ".opencode",
    "prompt_for_opencode_zen_key": ".opencode_zen",
    "load_opencode_zen_key": ".opencode_zen",
    "_b64url": ".pkce",
    "_pkce_pair": ".pkce",
    "load_oauth_tokens": ".oauth_tokens",
    "save_oauth_tokens": ".oauth_tokens",
    "clear_oauth_tokens": ".oauth_tokens",
    "oauth_refresh": ".oauth_tokens",
    "get_fresh_oauth_token": ".oauth_tokens",
    "oauth_login": ".oauth_flow",
    "_choose_auth_mode": ".mode_picker",
    "_choose_provider": ".mode_picker",
    "_build_client_from_mode": ".client",
    "make_client": ".client",
}


def __getattr__(name):
    mod = _EXPORTS.get(name)
    if mod is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    from importlib import import_module

    value = getattr(import_module(mod, __name__), name)
    globals()[name] = value
    return value
