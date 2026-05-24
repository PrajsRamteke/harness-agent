"""Pinned context + aliases persistence, and markdown export."""
import json, os, pathlib, time

from ..constants import CONFIG_DIR, PIN_FILE, ALIAS_FILE
from .. import state


def save_pin():
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    PIN_FILE.write_text(state.pinned_context)


def save_aliases():
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    ALIAS_FILE.write_text(json.dumps(state.aliases, indent=2))


def _global_settings_snapshot() -> dict:
    """Read global settings.json directly — no legacy migration side effects."""
    import json
    from .settings import SETTINGS_FILE, DEFAULTS, _deep_merge

    on_disk: dict = {}
    if SETTINGS_FILE.exists():
        try:
            parsed = json.loads(SETTINGS_FILE.read_text(encoding="utf-8"))
            if isinstance(parsed, dict):
                on_disk = parsed
        except (OSError, json.JSONDecodeError):
            pass
    return _deep_merge(DEFAULTS, on_disk)


def _legacy_saved_model() -> str:
    """Read pre-unified last_model.json without migrating it."""
    import json
    from ..constants import LAST_MODEL_FILE

    if not LAST_MODEL_FILE.exists():
        return ""
    try:
        data = json.loads(LAST_MODEL_FILE.read_text(encoding="utf-8"))
        m = data.get("model") if isinstance(data, dict) else ""
        return m.strip() if isinstance(m, str) else ""
    except (OSError, json.JSONDecodeError):
        return ""


def load_saved_model() -> str:
    """Last explicitly saved model (global settings, then legacy snapshot)."""
    m = (_global_settings_snapshot().get("model") or "").strip()
    if m:
        return m
    return _legacy_saved_model()


def load_saved_provider() -> str:
    """Last explicitly saved provider from global settings."""
    p = (_global_settings_snapshot().get("provider") or "").strip()
    return p if isinstance(p, str) else ""


def load_saved_preferences() -> tuple[str, str]:
    """Return (model, provider) restored from global settings."""
    model = load_saved_model()
    if not model:
        return "", ""
    provider = load_saved_provider()
    if not provider:
        from ..constants.providers import infer_provider_for_model
        provider = infer_provider_for_model(model)
    return model, provider


def should_use_first_run_harness_defaults() -> bool:
    """True on fresh install — no saved model and no explicit env overrides."""
    if os.getenv("CLAUDE_MODEL") or os.getenv("HARNESS_PROVIDER"):
        return False
    return not load_saved_model()


def save_last_model(model: str | None = None) -> None:
    """Persist the active model (+ provider) into global settings."""
    m = (model if model is not None else state.MODEL).strip()
    if not m:
        return
    try:
        from .settings import get_settings
        settings = get_settings()
        settings.set_global("model", m)
        if state.provider:
            settings.set_global("provider", state.provider)
    except Exception:
        pass


def export_markdown(path: str) -> str:
    """Dump conversation as a human-readable markdown file."""
    lines = [f"# Claude session — {time.strftime('%Y-%m-%d %H:%M')}", ""]
    for m in state.messages:
        role = m["role"]
        c = m["content"]
        lines.append(f"## {role}")
        if isinstance(c, str):
            lines.append(c)
        else:
            for b in c:
                bd = b.model_dump() if hasattr(b, "model_dump") else b
                t = bd.get("type")
                if t == "text":
                    lines.append(bd.get("text", ""))
                elif t == "thinking":
                    lines.append(f"> *thinking:* {bd.get('thinking','')}")
                elif t == "tool_use":
                    lines.append(f"**⚙ tool:** `{bd.get('name')}` — `{json.dumps(bd.get('input',{}))[:400]}`")
                elif t == "tool_result":
                    body = bd.get("content", "")
                    if isinstance(body, list):
                        body = "".join(x.get("text", "") for x in body if isinstance(x, dict))
                    # Show context pack results in full; cap other tool results at 2000.
                    body_str = str(body)
                    if body_str.startswith("=== Connected Context Pack ==="):
                        lines.append(f"```\n{body_str}\n```")
                    else:
                        lines.append(f"```\n{body_str[:2000]}\n```")
        lines.append("")
    pathlib.Path(path).write_text("\n".join(lines))
    return path
