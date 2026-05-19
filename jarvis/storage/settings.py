"""Unified user preferences — single ``settings.json`` per user.

All toggleable preferences (theme, model, skills/mcp global scope, think mode,
…) read from and write to one file::

    ~/.config/harness-agent/settings.json

The schema is intentionally human-editable. Users can hand-edit the file and
run ``/settings reload`` (or restart) to pick up changes::

    {
      "model": "sonnet-4-6",
      "theme": "red",
      "skills": { "global": false },
      "mcp":    { "global": false },
      "think":  { "mode": true, "effort": "medium" }
    }

On first load, values are migrated from the legacy per-feature files
(``last_model.json``, ``last_theme.json``, ``skills_config.json``,
``mcp_config.json``, ``think_config.json``). The legacy files are not deleted
— they remain as a one-way snapshot. After migration, only ``settings.json``
is written.
"""

from __future__ import annotations

import copy
import json
import os
import pathlib
import tempfile
from typing import Any

from ..constants import (
    CONFIG_DIR,
    LAST_MODEL_FILE,
    LAST_THEME_FILE,
    SKILLS_CONFIG_FILE,
    THINK_CONFIG_FILE,
    MCP_PREFS_FILE,
    PROJECT_HARNESS_SETTINGS,
    THINK_EFFORTS,
)


SETTINGS_FILE = CONFIG_DIR / "settings.json"


# ── schema + defaults ────────────────────────────────────────────────────

# Defaults are also the implicit schema. Any dotted path used by get/set must
# trace through this tree, otherwise we'd let typos silently expand the file.
DEFAULTS: dict[str, Any] = {
    "model":  "",            # empty string == fall back to constants.MODEL
    "theme":  "red",
    "agent":  {"active": "", "global": True},   # global agents visible by default
    "skills": {"global": False},
    "mcp":    {"global": False},
    "think":  {"mode": True, "effort": "medium"},
}


def _deep_merge(base: dict, overlay: dict) -> dict:
    """Recursive merge — overlay values win, but unknown keys are kept."""
    out = copy.deepcopy(base)
    for k, v in overlay.items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _deep_merge(out[k], v)
        else:
            out[k] = copy.deepcopy(v)
    return out


def _get_by_path(data: dict, path: str) -> Any:
    cur: Any = data
    for part in path.split("."):
        if not isinstance(cur, dict):
            return None
        if part not in cur:
            return None
        cur = cur[part]
    return cur


def _set_by_path(data: dict, path: str, value: Any) -> None:
    parts = path.split(".")
    cur = data
    for part in parts[:-1]:
        nxt = cur.get(part)
        if not isinstance(nxt, dict):
            nxt = {}
            cur[part] = nxt
        cur = nxt
    cur[parts[-1]] = value


def _delete_by_path(data: dict, path: str) -> bool:
    parts = path.split(".")
    cur = data
    for part in parts[:-1]:
        if not isinstance(cur, dict) or part not in cur:
            return False
        cur = cur[part]
    if not isinstance(cur, dict) or parts[-1] not in cur:
        return False
    del cur[parts[-1]]
    return True


def _atomic_write(path: pathlib.Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        "w", encoding="utf-8", dir=path.parent, delete=False
    ) as tmp:
        tmp.write(text)
        tmp.flush()
        os.fsync(tmp.fileno())
        tmp_path = tmp.name
    os.replace(tmp_path, path)


# ── Settings class ───────────────────────────────────────────────────────

_VALID_THEMES = ("red", "blue", "purple", "green", "orange", "yellow", "rose", "slate", "ocean", "cyberpunk", "monochrome", "forest", "dracula", "sunset", "dark")
_VALID_THINK_EFFORTS = THINK_EFFORTS


def _coerce(path: str, value: Any) -> Any:
    """Light validation/coercion for known paths."""
    if path == "theme":
        if value not in _VALID_THEMES:
            raise ValueError(f"theme must be one of {_VALID_THEMES}")
        return value
    if path == "think.effort":
        if value not in _VALID_THINK_EFFORTS:
            raise ValueError(f"think.effort must be one of {_VALID_THINK_EFFORTS}")
        return value
    if path in ("skills.global", "mcp.global", "agent.global", "think.mode"):
        if isinstance(value, str):
            v = value.strip().lower()
            if v in ("true", "1", "yes", "on"):
                return True
            if v in ("false", "0", "no", "off"):
                return False
            raise ValueError(f"{path} must be a boolean")
        if not isinstance(value, bool):
            raise ValueError(f"{path} must be a boolean")
        return value
    if path == "model":
        if not isinstance(value, str):
            raise ValueError("model must be a string")
        return value.strip()
    if path == "agent.active":
        if value is None:
            return ""
        if not isinstance(value, str):
            raise ValueError("agent.active must be a string")
        return value.strip().lower()
    return value


class Settings:
    """In-memory settings backed by ``~/.config/harness-agent/settings.json``."""

    def __init__(self, path: pathlib.Path = SETTINGS_FILE) -> None:
        self.path = path
        self._data: dict[str, Any] = {}
        self._loaded = False

    # ── load / save ──────────────────────────────────────────────────────

    def load(self) -> None:
        """Read settings.json, then merge in any legacy per-feature files.

        Resolution order (highest precedence last so it wins the merge):
            1. legacy per-feature files (lowest)
            2. global ~/.config/harness-agent/settings.json
            3. project .harness/settings.json (highest)
        """
        on_disk: dict[str, Any] = {}
        if self.path.exists():
            try:
                parsed = json.loads(self.path.read_text(encoding="utf-8"))
                if isinstance(parsed, dict):
                    on_disk = parsed
            except (OSError, json.JSONDecodeError):
                on_disk = {}

        legacy = _migrate_legacy()
        project = _read_project_settings()

        merged = _deep_merge(legacy, on_disk)
        merged = _deep_merge(merged, project)
        self._data = merged
        self._loaded = True

        # If we filled in anything from legacy, persist the merged global file
        # once so subsequent reads stop touching the legacy files. We never
        # write the project file from here — it's user-managed.
        write_back = _deep_merge(legacy, on_disk)
        if legacy and not on_disk:
            self.save()
        elif legacy and write_back != on_disk:
            self.save()

    def save(self) -> None:
        if not self._loaded:
            # Avoid clobbering with an empty doc.
            self._data = _deep_merge(DEFAULTS, self._data)
            self._loaded = True
        # Ensure defaults are represented so users can see what's available.
        out = _deep_merge(DEFAULTS, self._data)
        _atomic_write(
            self.path,
            json.dumps(out, indent=2, ensure_ascii=False) + "\n",
        )

    # ── access ───────────────────────────────────────────────────────────

    def get(self, path: str, default: Any = None) -> Any:
        if not self._loaded:
            self.load()
        merged = _deep_merge(DEFAULTS, self._data)
        val = _get_by_path(merged, path)
        return val if val is not None else default

    def set(self, path: str, value: Any) -> Any:
        """Set, validate, persist. Returns the coerced value."""
        if not self._loaded:
            self.load()
        coerced = _coerce(path, value)
        _set_by_path(self._data, path, coerced)
        self.save()
        return coerced

    def reset(self, path: str) -> Any:
        """Remove an override so the default takes effect."""
        if not self._loaded:
            self.load()
        _delete_by_path(self._data, path)
        self.save()
        return _get_by_path(_deep_merge(DEFAULTS, self._data), path)

    def all(self) -> dict[str, Any]:
        """Full merged view (defaults + overrides)."""
        if not self._loaded:
            self.load()
        return _deep_merge(DEFAULTS, self._data)

    def overrides(self) -> dict[str, Any]:
        """Only the user-set values (what's actually in the file on disk)."""
        if not self._loaded:
            self.load()
        return copy.deepcopy(self._data)

    def reload(self) -> dict[str, Any]:
        """Re-read from disk and return the new merged view."""
        self._loaded = False
        self._data = {}
        self.load()
        return self.all()


# ── legacy migration ─────────────────────────────────────────────────────

def _read_json(path: pathlib.Path) -> dict:
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


def _read_project_settings() -> dict[str, Any]:
    """Read ``<cwd>/.harness/settings.json`` (if present).

    Honors the project root (git toplevel) for consistent behavior with
    skill / agent discovery. Walks up from CWD to find the .harness dir.
    Returns ``{}`` when not found or malformed.
    """
    try:
        cwd = pathlib.Path.cwd().resolve()
        candidates: list[pathlib.Path] = []
        for parent in [cwd] + list(cwd.parents):
            candidates.append(parent / PROJECT_HARNESS_SETTINGS)
            if (parent / ".git").exists():
                break
        for path in candidates:
            if path.is_file():
                try:
                    parsed = json.loads(path.read_text(encoding="utf-8"))
                    if isinstance(parsed, dict):
                        return parsed
                except (OSError, json.JSONDecodeError):
                    pass
    except Exception:
        pass
    return {}


def _migrate_legacy() -> dict[str, Any]:
    """Pull values from the per-feature legacy files into the unified schema."""
    out: dict[str, Any] = {}

    m = _read_json(LAST_MODEL_FILE).get("model")
    if isinstance(m, str) and m.strip():
        out["model"] = m.strip()

    t = _read_json(LAST_THEME_FILE).get("theme")
    if isinstance(t, str) and t in _VALID_THEMES:
        out["theme"] = t

    s = _read_json(SKILLS_CONFIG_FILE).get("global_skills")
    if isinstance(s, bool):
        out.setdefault("skills", {})["global"] = s

    g = _read_json(MCP_PREFS_FILE).get("global_mcp")
    if isinstance(g, bool):
        out.setdefault("mcp", {})["global"] = g

    think_doc = _read_json(THINK_CONFIG_FILE)
    if isinstance(think_doc.get("think_mode"), bool):
        out.setdefault("think", {})["mode"] = think_doc["think_mode"]
    if think_doc.get("think_effort") in _VALID_THINK_EFFORTS:
        out.setdefault("think", {})["effort"] = think_doc["think_effort"]

    return out


# ── module-level singleton ───────────────────────────────────────────────

_singleton: Settings | None = None


def get_settings() -> Settings:
    """Return the process-wide ``Settings`` instance (lazy-loaded)."""
    global _singleton
    if _singleton is None:
        _singleton = Settings()
        _singleton.load()
    return _singleton


def reload_settings() -> Settings:
    """Force a fresh read from disk."""
    get_settings().reload()
    return get_settings()
