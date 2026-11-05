"""The user's workspace: a saved, ordered list of projects (UI-free).

``~/.config/harness-agent/workspace.json`` (dir 700, file 600) holds the folders
the user works on, in their order, with a colour, a start-up mode and the
projects each one may *link* (read — and, by policy, write — from its agent).
Several Jarvis processes (one per project) read and write it, so writes take an
advisory ``flock`` and land atomically (temp file + ``os.replace``); readers
rely on the atomic replace and a short mtime cache.

Everything here is plain data in / plain data out; the web routes
(``web/workspace_api.py``), the TUI ``/project`` command and the agent tools'
path scoping (``path_resolve``) all go through this module.
"""
from __future__ import annotations

import contextlib
import json
import os
import re
import threading
import time
from pathlib import Path
from typing import Any, Callable, Iterator

from ..constants import CONFIG_DIR

WORKSPACE_FILE = CONFIG_DIR / "workspace.json"
LOCK_FILE = CONFIG_DIR / "workspace.lock"
SCHEMA_VERSION = 1

KINDS = ("project", "group")
STARTUP_MODES = ("lazy", "warm", "pinned")
LINK_WRITE_MODES = ("ask", "allow", "deny")
MAX_PROJECTS = 40
MAX_NAME = 60
CACHE_TTL = 0.5          # seconds a parsed file is reused before its mtime is looked at again

# Accent presets handed out round-robin to new projects (same hues the theme offers).
PALETTE = ("#7c5cff", "#2fb344", "#f59f00", "#e8590c", "#d6336c", "#1c7ed6", "#0ca678", "#ae3ec9")
_COLOR_RE = re.compile(r"^#[0-9a-fA-F]{6}$")
_ID_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,31}$")
_ICON_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,31}$")

# A project here would make "inside the project" meaningless (or expose the system).
_REFUSED_ROOTS = (
    "/", "/etc", "/usr", "/bin", "/sbin", "/var", "/private", "/System", "/Library", "/Applications",
    "/dev", "/proc", "/sys", "/tmp", "/Volumes", "/Users", "/home", "/opt",
)

DEFAULT_SETTINGS: dict[str, Any] = {
    "warm_limit": 3,          # headless projects kept running while idle
    "idle_stop_minutes": 30,  # an idle headless project is stopped after this long (0 = never)
    "link_all": False,        # every project may read every other
    "delegation": False,      # the agent may hand a task to another project's Jarvis (ask_project)
}


class WorkspaceError(Exception):
    """Shown to the user as is; ``code`` lets the page react."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


# ─── Reading / writing ───────────────────────────────────────────────────────

_cache_lock = threading.RLock()
_cache: dict[str, Any] = {"at": 0.0, "mtime": None, "path": None, "data": None}
_listeners: list[Callable[[], None]] = []


def _empty() -> dict[str, Any]:
    return {"version": SCHEMA_VERSION, "projects": [], "order": [], "settings": dict(DEFAULT_SETTINGS)}


def _real(path: str | Path) -> str:
    try:
        return os.path.realpath(os.path.expanduser(str(path)))
    except (OSError, ValueError):
        return str(path)


def _clean_project(raw: Any) -> dict[str, Any] | None:
    if not isinstance(raw, dict):
        return None
    pid = str(raw.get("id") or "")
    path = str(raw.get("path") or "")
    if not _ID_RE.match(pid) or not path:
        return None
    startup = raw.get("startup")
    link_write = raw.get("link_write")
    color = raw.get("color")
    icon = raw.get("icon")
    links = raw.get("links")
    last = raw.get("last_state")
    kind = raw.get("kind")
    master = raw.get("master_session")
    return {
        "id": pid,
        "kind": kind if kind in KINDS else "project",
        "master_session": master if isinstance(master, int) and not isinstance(master, bool) else None,
        "path": path,
        "name": (str(raw.get("name") or "").strip() or Path(path).name or pid)[:MAX_NAME],
        "color": color if isinstance(color, str) and _COLOR_RE.match(color) else PALETTE[0],
        "icon": icon if isinstance(icon, str) and _ICON_RE.match(icon) else "",
        "startup": startup if startup in STARTUP_MODES else "lazy",
        "links": [str(x) for x in links if isinstance(x, str)] if isinstance(links, list) else [],
        "link_write": link_write if link_write in LINK_WRITE_MODES else "ask",
        "added_at": float(raw["added_at"]) if isinstance(raw.get("added_at"), (int, float)) else 0.0,
        "last_state": dict(last) if isinstance(last, dict) else {},
    }


def _normalize(raw: Any) -> dict[str, Any]:
    """Validate what was read: unknown/duplicate ids dropped, order healed, links pruned."""
    data = _empty()
    if not isinstance(raw, dict):
        return data
    seen: set[str] = set()
    projects: list[dict[str, Any]] = []
    for item in raw.get("projects") or []:
        proj = _clean_project(item)
        if proj is None or proj["id"] in seen:
            continue
        seen.add(proj["id"])
        projects.append(proj)
    ids = [p["id"] for p in projects]
    groups = {p["id"] for p in projects if p["kind"] == "group"}
    for proj in projects:
        # A group's links are its members; a group can't be a member or a link of anything.
        proj["links"] = [x for x in dict.fromkeys(proj["links"]) if x in seen and x != proj["id"] and x not in groups]
    order = [x for x in (raw.get("order") or []) if isinstance(x, str) and x in seen]
    order = list(dict.fromkeys(order))
    order += [x for x in ids if x not in order]
    settings = dict(DEFAULT_SETTINGS)
    got = raw.get("settings")
    if isinstance(got, dict):
        for key, default in DEFAULT_SETTINGS.items():
            val = got.get(key)
            if isinstance(default, bool):
                if isinstance(val, bool):
                    settings[key] = val
            elif isinstance(val, int) and not isinstance(val, bool) and 0 <= val <= 10_000:
                settings[key] = val
    data["projects"] = projects
    data["order"] = order
    data["settings"] = settings
    return data


def _mtime() -> float | None:
    try:
        return WORKSPACE_FILE.stat().st_mtime
    except OSError:
        return None


def _read_file() -> dict[str, Any]:
    for candidate in (WORKSPACE_FILE, WORKSPACE_FILE.with_suffix(".json.bak")):
        try:
            return _normalize(json.loads(candidate.read_text()))
        except (OSError, ValueError):
            continue
    return _empty()


def load(*, fresh: bool = False) -> dict[str, Any]:
    """The workspace as a plain dict (a copy — mutate freely). Never raises."""
    with _cache_lock:
        now = time.time()
        path = str(WORKSPACE_FILE)
        stale = (
            fresh or _cache["data"] is None or _cache["path"] != path
            or now - _cache["at"] > CACHE_TTL
        )
        if stale:
            mtime = _mtime()
            if fresh or _cache["data"] is None or _cache["path"] != path or mtime != _cache["mtime"]:
                _cache["data"] = _read_file()
                _cache["mtime"] = mtime
                _cache["path"] = path
            _cache["at"] = now
        return json.loads(json.dumps(_cache["data"]))


def revision() -> float:
    """Changes whenever the file does (cheap: one ``stat``) — pollers compare it."""
    return _mtime() or 0.0


@contextlib.contextmanager
def _locked() -> Iterator[None]:
    try:
        import fcntl
    except ImportError:  # Windows: a best-effort single writer
        yield
        return
    try:
        CONFIG_DIR.mkdir(parents=True, exist_ok=True)
        fd = os.open(str(LOCK_FILE), os.O_CREAT | os.O_RDWR, 0o600)
    except OSError:
        yield
        return
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
        yield
    finally:
        try:
            fcntl.flock(fd, fcntl.LOCK_UN)
        finally:
            os.close(fd)


def _write(data: dict[str, Any]) -> None:
    WORKSPACE_FILE.parent.mkdir(parents=True, exist_ok=True)
    try:
        os.chmod(WORKSPACE_FILE.parent, 0o700)
    except OSError:
        pass
    if WORKSPACE_FILE.exists():
        try:  # the last good copy: a corrupt write falls back to it
            bak = WORKSPACE_FILE.with_suffix(".json.bak")
            bak.write_bytes(WORKSPACE_FILE.read_bytes())
            os.chmod(bak, 0o600)
        except OSError:
            pass
    tmp = WORKSPACE_FILE.with_suffix(f".{os.getpid()}.tmp")
    tmp.write_text(json.dumps({**data, "version": SCHEMA_VERSION}, ensure_ascii=False, indent=2))
    try:
        os.chmod(tmp, 0o600)
    except OSError:
        pass
    os.replace(tmp, WORKSPACE_FILE)
    with _cache_lock:
        _cache["data"] = None
    _notify()


@contextlib.contextmanager
def _edit() -> Iterator[dict[str, Any]]:
    """Read-modify-write under the lock; the caller mutates the yielded dict."""
    with _locked():
        data = load(fresh=True)
        yield data
        data = _normalize(data)
        _write(data)


def add_listener(fn: Callable[[], None]) -> None:
    """Called (in this process) after every write — other processes are seen via :func:`revision`."""
    if fn not in _listeners:
        _listeners.append(fn)


def remove_listener(fn: Callable[[], None]) -> None:
    if fn in _listeners:
        _listeners.remove(fn)


def _notify() -> None:
    for fn in list(_listeners):
        try:
            fn()
        except Exception:
            pass


# ─── Validation / slugs ──────────────────────────────────────────────────────

def refused_reason(path: str | Path) -> str:
    """Why ``path`` can't be a project ('' when it can)."""
    real = _real(path)
    home = _real(Path.home())
    if real in {_real(p) for p in _REFUSED_ROOTS} or real == home:
        return "That folder is too broad to be a project — pick a folder inside it."
    if not os.path.isdir(real):
        return "That folder doesn't exist."
    return ""


def slugify(name: str) -> str:
    slug = re.sub(r"[^a-z0-9_-]+", "-", (name or "").lower()).strip("-_")
    slug = re.sub(r"-{2,}", "-", slug)[:28].strip("-_")
    return slug if _ID_RE.match(slug or "") else "project"


def _unique_id(base: str, taken: set[str]) -> str:
    if base not in taken:
        return base
    n = 2
    while f"{base}-{n}" in taken:
        n += 1
    return f"{base}-{n}"


# ─── Queries ─────────────────────────────────────────────────────────────────

def projects(data: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    """Projects in the user's order."""
    data = data or load()
    by_id = {p["id"]: p for p in data["projects"]}
    return [by_id[i] for i in data["order"] if i in by_id]


def get(project_id: str, data: dict[str, Any] | None = None) -> dict[str, Any] | None:
    for proj in (data or load())["projects"]:
        if proj["id"] == project_id:
            return proj
    return None


def find_by_path(path: str | Path, *, within: bool = False, data: dict[str, Any] | None = None) -> dict[str, Any] | None:
    """The project whose folder is ``path`` (``within``: or contains it — deepest wins)."""
    real = _real(path)
    best: dict[str, Any] | None = None
    best_len = -1
    for proj in (data or load())["projects"]:
        root = _real(proj["path"])
        if real == root:
            return proj
        if within and real.startswith(root.rstrip(os.sep) + os.sep) and len(root) > best_len:
            best, best_len = proj, len(root)
    return best


def current(cwd: str | Path | None = None) -> dict[str, Any] | None:
    """The workspace project this process is working in (``None``: an ad-hoc folder)."""
    if cwd is None:
        try:
            from ..constants import paths

            cwd = paths.CWD
        except Exception:
            cwd = os.getcwd()
    return find_by_path(cwd, within=True)


def links_of(project: dict[str, Any], data: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    """The projects ``project``'s agent may reach (explicit links, or everyone with ``link_all``)."""
    data = data or load()
    if data["settings"].get("link_all") and project.get("kind") != "group":
        return [p for p in projects(data) if p["id"] != project["id"] and p.get("kind") != "group"]
    return [p for p in (get(i, data) for i in project["links"]) if p is not None]


def roots_for(project_id: str | None = None, *, cwd: str | Path | None = None) -> list[dict[str, Any]]:
    """Every root a tool may touch: ``[{id, path, primary, write}]``, own root first.

    ``write`` is ``allow`` for the primary root and the linked project's
    ``link_write`` policy (``ask`` / ``allow`` / ``deny``) for the rest. Linked
    projects whose folder is gone, or is no longer a valid project root, are left out.
    """
    data = load()
    proj = get(project_id, data) if project_id else current(cwd)
    if proj is None:
        return []
    roots = [{"id": proj["id"], "path": _real(proj["path"]), "primary": True, "write": "allow",
              "name": proj["name"]}]
    for other in links_of(proj, data):
        real = _real(other["path"])
        if refused_reason(real):
            continue
        # A group's own policy covers all its members; otherwise the linked project's decides.
        policy = proj["link_write"] if proj.get("kind") == "group" else other["link_write"]
        roots.append({"id": other["id"], "path": real, "primary": False, "write": policy,
                      "name": other["name"]})
    return roots


def resolve_ref(ref: str, *, cwd: str | Path | None = None) -> Path | None:
    """``"api:src/x.ts"`` → absolute path inside the linked/own project ``api`` (``None``: not a ref).

    Raises :class:`WorkspaceError` for a project-qualified path that can't be honoured
    (unknown project, not linked here, or an escape out of its folder).
    """
    m = re.match(r"^([a-z0-9][a-z0-9_-]{0,31}):(?![\\/]{2})(.*)$", ref or "", re.S)
    if not m:
        return None
    pid, rel = m.group(1), m.group(2)
    roots = {r["id"]: r for r in roots_for(cwd=cwd)}
    if pid not in roots:
        known = get(pid)
        if known is None:
            return None  # "http://x" / "C:foo": not ours — let the caller treat it as a plain path
        raise WorkspaceError(
            "not_linked",
            f"Project '{pid}' isn't linked to this project. Link it in the workspace "
            f"(/project link {pid}) before reading or editing its files.",
        )
    root = Path(roots[pid]["path"])
    target = (root / rel.lstrip("/\\")).resolve()
    try:
        target.relative_to(root)
    except ValueError:
        raise WorkspaceError("outside", f"'{rel}' escapes project '{pid}'.") from None
    return target


def root_for_path(path: str | Path, *, cwd: str | Path | None = None) -> dict[str, Any] | None:
    """Which root (own or linked) contains ``path`` — the deepest match."""
    real = _real(path)
    best: dict[str, Any] | None = None
    for root in roots_for(cwd=cwd):
        base = root["path"].rstrip(os.sep)
        if real == base or real.startswith(base + os.sep):
            if best is None or len(base) > len(best["path"]):
                best = root
    return best


# ─── Mutations ───────────────────────────────────────────────────────────────

def add(path: str | Path, *, name: str | None = None) -> dict[str, Any]:
    """Add a folder. Returns the project; ``WorkspaceError`` for ``refused`` / ``duplicate`` / ``limit``."""
    real = _real(path)
    reason = refused_reason(real)
    if reason:
        raise WorkspaceError("refused" if os.path.isdir(real) else "not_found", reason)
    with _edit() as data:
        if len(data["projects"]) >= MAX_PROJECTS:
            raise WorkspaceError("limit", f"A workspace holds at most {MAX_PROJECTS} projects.")
        for proj in data["projects"]:
            if _real(proj["path"]) == real:
                raise WorkspaceError("duplicate", f"'{proj['name']}' is already in your workspace.")
        taken = {p["id"] for p in data["projects"]}
        pid = _unique_id(slugify(name or Path(real).name), taken)
        proj = _clean_project({
            "id": pid, "path": real, "name": (name or Path(real).name).strip(),
            "color": PALETTE[len(data["projects"]) % len(PALETTE)],
            "added_at": time.time(),
        })
        assert proj is not None
        data["projects"].append(proj)
        data["order"].append(pid)
    return get(pid) or proj


def add_many(paths: list[str]) -> dict[str, Any]:
    """Add several folders: ``{added: [project], errors: [{path, code, error}]}`` (partial success is fine)."""
    added: list[dict[str, Any]] = []
    errors: list[dict[str, str]] = []
    for raw in paths:
        try:
            added.append(add(raw))
        except WorkspaceError as exc:
            errors.append({"path": str(raw), "code": exc.code, "error": str(exc)})
    return {"added": added, "errors": errors}


def remove(project_id: str) -> dict[str, Any]:
    with _edit() as data:
        proj = get(project_id, data)
        if proj is None:
            raise WorkspaceError("not_found", "That project isn't in your workspace.")
        data["projects"] = [p for p in data["projects"] if p["id"] != project_id]
        data["order"] = [i for i in data["order"] if i != project_id]
        for other in data["projects"]:
            other["links"] = [x for x in other["links"] if x != project_id]
    for grp in projects():
        if is_group(grp) and project_id not in grp["links"] and grp["id"] != project_id:
            _write_hub_notes(grp)
    return proj


_UPDATABLE = ("name", "color", "icon", "startup", "link_write", "links")


def update(project_id: str, **fields: Any) -> dict[str, Any]:
    """Change ``name``, ``color``, ``icon``, ``startup``, ``link_write``, ``links`` (and ``new_id``)."""
    with _edit() as data:
        proj = get(project_id, data)
        if proj is None:
            raise WorkspaceError("not_found", "That project isn't in your workspace.")
        known = {p["id"] for p in data["projects"]}
        for key, val in fields.items():
            if val is None or key not in _UPDATABLE + ("new_id",):
                continue
            if key == "name":
                name = str(val).strip()
                if not name:
                    raise WorkspaceError("bad_value", "A project needs a name.")
                proj["name"] = name[:MAX_NAME]
            elif key == "color":
                if not (isinstance(val, str) and _COLOR_RE.match(val)):
                    raise WorkspaceError("bad_value", "Colour must look like #7c5cff.")
                proj["color"] = val
            elif key == "icon":
                if val and not (isinstance(val, str) and _ICON_RE.match(val)):
                    raise WorkspaceError("bad_value", "Unknown icon name.")
                proj["icon"] = str(val or "")
            elif key == "startup":
                if val not in STARTUP_MODES:
                    raise WorkspaceError("bad_value", f"Start-up must be one of {', '.join(STARTUP_MODES)}.")
                proj["startup"] = val
            elif key == "link_write":
                if val not in LINK_WRITE_MODES:
                    raise WorkspaceError("bad_value", f"Write policy must be one of {', '.join(LINK_WRITE_MODES)}.")
                proj["link_write"] = val
            elif key == "links":
                if not isinstance(val, list):
                    raise WorkspaceError("bad_value", "Links must be a list of project ids.")
                groups = {p["id"] for p in data["projects"] if p.get("kind") == "group"}
                bad = [x for x in val if x not in known or x == project_id]
                if bad:
                    raise WorkspaceError("bad_value", f"Unknown project(s) to link: {', '.join(map(str, bad))}.")
                if any(x in groups for x in val):
                    raise WorkspaceError("bad_value", "A group can't be linked — link its member projects instead.")
                proj["links"] = [str(x) for x in dict.fromkeys(val)]
            elif key == "new_id":
                new = str(val)
                if not _ID_RE.match(new):
                    raise WorkspaceError("bad_value", "Id: lowercase letters, digits, - and _ (max 32).")
                if new != project_id:
                    if new in known:
                        raise WorkspaceError("duplicate", f"'{new}' is already used by another project.")
                    proj["id"] = new
                    data["order"] = [new if i == project_id else i for i in data["order"]]
                    for other in data["projects"]:
                        other["links"] = [new if x == project_id else x for x in other["links"]]
                    project_id = new
    result = get(project_id) or {}
    if is_group(result):
        _write_hub_notes(result)
    return result


def reorder(order: list[str]) -> list[str]:
    with _edit() as data:
        known = {p["id"] for p in data["projects"]}
        wanted = [i for i in dict.fromkeys(order) if i in known]
        data["order"] = wanted + [i for i in data["order"] if i not in wanted]
        result = list(data["order"])
    return result


def update_settings(**values: Any) -> dict[str, Any]:
    with _edit() as data:
        for key, val in values.items():
            if key not in DEFAULT_SETTINGS or val is None:
                continue
            want = DEFAULT_SETTINGS[key]
            if isinstance(want, bool):
                if not isinstance(val, bool):
                    raise WorkspaceError("bad_value", f"{key} must be true or false.")
            elif not (isinstance(val, int) and not isinstance(val, bool) and 0 <= val <= 10_000):
                raise WorkspaceError("bad_value", f"{key} must be a whole number.")
            data["settings"][key] = val
        settings = dict(data["settings"])
    return settings


# ─── Master groups ───────────────────────────────────────────────────────────
# A group is a virtual "main project": no repo of its own, just a hub folder where one
# shared *master* chat (and master terminal) lives, with every member project linked.

def is_group(proj: dict[str, Any] | None) -> bool:
    return bool(proj) and proj.get("kind") == "group"


def members(group: dict[str, Any], data: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    data = data or load()
    return [p for p in (get(i, data) for i in group.get("links", [])) if p is not None]


def groups_of(project_id: str, data: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    """Groups ``project_id`` is a member of, in the user's order."""
    return [p for p in projects(data) if is_group(p) and project_id in p["links"]]


def hub_notes(group: dict[str, Any], data: dict[str, Any] | None = None) -> str:
    lines = [f"# {group['name']} — master group", "",
             "Hub folder of a Jarvis master group. The master chat and terminal live here.", "",
             "| id | project | path |", "|---|---|---|"]
    for m in members(group, data):
        lines.append(f"| {m['id']} | {m['name']} | {m['path']} |")
    return "\n".join(lines) + "\n"


def _write_hub_notes(group: dict[str, Any]) -> None:
    try:
        Path(group["path"]).mkdir(parents=True, exist_ok=True)
        (Path(group["path"]) / "PROJECTS.md").write_text(hub_notes(group), encoding="utf-8")
    except OSError:
        pass


def create_group(name: str, member_ids: list[str]) -> dict[str, Any]:
    """Create a group over existing projects. ``WorkspaceError`` for a bad name, member or limit."""
    name = str(name or "").strip()
    if not name:
        raise WorkspaceError("bad_value", "A group needs a name.")
    ids = list(dict.fromkeys(str(i) for i in member_ids or []))
    if not ids:
        raise WorkspaceError("bad_value", "Pick at least one project for the group.")
    with _edit() as data:
        if len(data["projects"]) >= MAX_PROJECTS:
            raise WorkspaceError("limit", f"A workspace holds at most {MAX_PROJECTS} projects.")
        bad = [i for i in ids if get(i, data) is None or is_group(get(i, data))]
        if bad:
            raise WorkspaceError("bad_value", f"Not a project in your workspace: {', '.join(bad)}.")
        taken = {p["id"] for p in data["projects"]}
        gid = _unique_id(slugify(name), taken)
        hub = CONFIG_DIR / "groups" / gid   # the hub folder of this group
        try:
            hub.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            raise WorkspaceError("io", f"Couldn't create the group folder: {exc}") from exc
        proj = _clean_project({
            "id": gid, "kind": "group", "path": _real(hub), "name": name,
            "color": PALETTE[len(data["projects"]) % len(PALETTE)], "links": ids,
            "link_write": "ask", "added_at": time.time(),
        })
        assert proj is not None
        data["projects"].append(proj)
        data["order"].append(gid)
    group = get(gid) or proj
    _write_hub_notes(group)
    return group


def set_master_session(group_id: str, session_id: int | None) -> None:
    """Remember which saved chat is the group's master chat — never raises."""
    try:
        with _edit() as data:
            proj = get(group_id, data)
            if proj is None or not is_group(proj) or proj.get("master_session") == session_id:
                return
            proj["master_session"] = session_id
    except (OSError, WorkspaceError):
        pass


def set_last_state(project_id: str, state: dict[str, Any]) -> None:
    """Remember what a project looked like when it stopped (branch, last chat) — never raises."""
    clean = {k: state[k] for k in ("branch", "session_id", "session_title", "model", "last_active")
             if k in state and isinstance(state[k], (str, int, float, type(None)))}
    try:
        with _edit() as data:
            proj = get(project_id, data)
            if proj is None:
                return
            proj["last_state"] = {**proj["last_state"], **clean}
    except (OSError, WorkspaceError):
        pass


def import_order(folder_paths: list[str]) -> list[str]:
    """Adopt a device's drag order (folder paths, from the old localStorage list) for projects we know."""
    ids: list[str] = []
    data = load()
    for raw in folder_paths:
        proj = find_by_path(raw, data=data)
        if proj is not None:
            ids.append(proj["id"])
    return reorder(ids) if ids else [p["id"] for p in projects(data)]


__all__ = [
    "WORKSPACE_FILE", "WorkspaceError", "add", "add_listener", "add_many", "current", "find_by_path", "get",
    "import_order", "links_of", "load", "projects", "refused_reason", "remove", "remove_listener", "reorder",
    "create_group", "groups_of", "hub_notes", "is_group", "members", "set_master_session",
    "resolve_ref", "revision", "root_for_path", "roots_for", "set_last_state", "slugify", "update",
    "update_settings",
]
