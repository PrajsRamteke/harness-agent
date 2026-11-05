"""Resolve user-supplied paths when the final component differs only by Unicode space characters.

macOS screenshot names often use U+202F (narrow no-break space) before AM/PM. Typed paths
use a normal space (U+0020), so pathlib reports missing files unless we match loosely.

Multi-project workspace: a project may *link* other workspace projects (see
``storage/workspace.py``). Tools then accept ``api:src/x.ts`` project-qualified paths and
may read — and, by the link's write policy, edit — files in those roots. Every workspace
lookup here is lazy (``storage`` imports sessions, which import this package).
"""
from __future__ import annotations

import functools
import os
import pathlib
import re
import threading
import time
import unicodedata

from .constants import CWD


def _unicode_space_key(name: str) -> str:
    """Map filename to a form comparable across Zs (space separator) code points."""
    return "".join(" " if unicodedata.category(ch) == "Zs" else ch for ch in name)


def _loose_name_key(name: str) -> str:
    """Looser filename key — also treats ``4.34.17PM`` and ``4.34.17 PM`` as equal."""
    s = _unicode_space_key(name).casefold()
    s = re.sub(r"(\d)\s*(am|pm)\b", r"\1 \2", s)
    return re.sub(r"\s+", " ", s).strip()


# ─── project-qualified paths (`api:src/x.ts`) ────────────────────────────────

# Same shape as workspace ids; "http://x" (a "//" right after the colon) is never a ref.
_REF_RE = re.compile(r"^([a-z0-9][a-z0-9_-]{0,31}):(?![\\/]{2})(.*)$", re.S)


def looks_like_ref(path: str) -> bool:
    """Cheap syntactic check — whether ``path`` *could* be ``<id>:rel`` (the workspace decides)."""
    return isinstance(path, str) and ":" in path and _REF_RE.match(path.strip()) is not None


def split_ref(path: str) -> tuple[str, str] | None:
    """``"api:src/x.ts"`` → ``("api", "src/x.ts")`` when ``api`` is a known workspace project, else None.

    Never raises; unknown ids (``C:foo``, URLs, plain relative names) are not refs.
    """
    if not looks_like_ref(path):
        return None
    m = _REF_RE.match(path.strip())
    assert m is not None
    try:
        from .storage import workspace

        if workspace.get(m.group(1)) is None:
            return None
    except Exception:
        return None
    return m.group(1), m.group(2)


def resolve_project_path(ref: str, cwd: pathlib.Path | str | None = None) -> pathlib.Path | None:
    """``"api:src/x.ts"`` → absolute path inside the linked (or own) project ``api``.

    ``None`` when ``ref`` isn't project-qualified. Raises ``WorkspaceError`` (message fit to
    show the model) for an unknown/unlinked project or an escape out of its folder.
    """
    if not looks_like_ref(ref):
        return None
    try:
        from .storage import workspace
    except Exception:
        return None
    return workspace.resolve_ref(ref.strip(), cwd=cwd)


def guard_refs(fn):
    """Tool decorator: a bad ``id:path`` becomes ``"ERROR: <why>"`` instead of a traceback."""

    @functools.wraps(fn)
    def wrapper(*args, **kwargs):
        try:
            return fn(*args, **kwargs)
        except Exception as exc:
            if type(exc).__name__ == "WorkspaceError":
                return f"ERROR: {exc}"
            raise

    return wrapper


def display_ref(path: pathlib.Path | str) -> str:
    """``api:src/x.ts`` for a file in a linked root; the plain relative/absolute form otherwise."""
    p = pathlib.Path(path)
    try:
        return str(p.resolve().relative_to(CWD.resolve()))
    except (ValueError, OSError):
        pass
    root = linked_root_for(p)
    if root is not None:
        try:
            return f"{root['id']}:{p.resolve().relative_to(root['path']).as_posix()}"
        except (ValueError, OSError):
            pass
    return str(path)


def robust_resolve(path: str, cwd: pathlib.Path | None = None) -> pathlib.Path:
    """Like pathlib resolve, but if the path is missing, retry matching the basename in
    its parent directory when names differ only by Unicode space characters.

    ``api:src/x.ts`` resolves through the workspace when ``api`` is a known project
    (``WorkspaceError`` if it isn't linked to this one); other ``x:y`` strings stay plain paths."""
    raw_in = (path or "").strip()
    if ":" in raw_in and raw_in[:1].isalnum():
        ref = resolve_project_path(raw_in, cwd)
        if ref is not None:
            return ref
    cwd = cwd or CWD
    raw = os.path.expanduser(raw_in)
    base = (cwd / raw).resolve() if not os.path.isabs(raw) else pathlib.Path(raw).resolve()
    if base.exists():
        return base
    parent, name = base.parent, base.name
    if not name or not parent.is_dir():
        return base
    key = _unicode_space_key(name)
    loose = _loose_name_key(name)
    try:
        for ent in parent.iterdir():
            if _unicode_space_key(ent.name) == key:
                return ent.resolve()
            if _loose_name_key(ent.name) == loose:
                return ent.resolve()
    except OSError:
        pass
    return base


def is_within(path: pathlib.Path, root: pathlib.Path | None = None) -> bool:
    """Return True when ``path`` is inside ``root`` after resolving both."""
    root = (root or CWD).resolve()
    try:
        path.resolve().relative_to(root)
        return True
    except ValueError:
        return False


# ─── linked roots ────────────────────────────────────────────────────────────

def linked_roots() -> list[dict]:
    """The *other* roots this project's agent may reach: ``[{id, path, write, name, primary: False}]``."""
    try:
        from .storage import workspace

        return [r for r in workspace.roots_for() if not r.get("primary")]
    except Exception:
        return []


def linked_root_for(path: pathlib.Path | str) -> dict | None:
    """The linked (non-primary) root containing ``path`` (resolved — a symlink out of a root
    lands outside it), or ``None``."""
    try:
        from .storage import workspace

        root = workspace.root_for_path(path)
    except Exception:
        return None
    if root is None or root.get("primary"):
        return None
    return root


# Approval memory for `ask` links: "always" per project for the session, and a short
# grace per file so one multi-step edit of the same file isn't asked about over and over.
_APPROVE_GRACE = 300.0
_approved: dict[tuple[str, str], float] = {}
_always: set[str] = set()
_approve_lock = threading.Lock()


def reset_linked_approvals() -> None:
    with _approve_lock:
        _approved.clear()
        _always.clear()


def _read_only_hint(root_id: str) -> str:
    return (
        f"Project {root_id} is linked read-only. Change Write policy in the workspace Links "
        f"dialog or with /project link."
    )


def linked_write_error(path: pathlib.Path, tool: str) -> str | None:
    """Policy check for a write into a *linked* root (``None``: not linked, or allowed).

    ``deny`` → error with a hint; ``ask`` → the user is asked first (auto-approve skips the
    question); ``allow`` → fine. Never silently writes under ``ask``.
    """
    root = linked_root_for(path)
    if root is None:
        return None
    policy = root.get("write", "ask")
    if policy == "allow":
        return None
    if policy == "deny":
        return f"ERROR: {tool} refused to write '{path}'. {_read_only_hint(root['id'])}"
    key = (root["id"], str(path))
    now = time.monotonic()
    with _approve_lock:
        if root["id"] in _always or _approved.get(key, 0.0) > now:
            return None
    from .tools.shell import ask_linked_write

    answer = ask_linked_write(root["id"], str(path), tool)
    if answer in ("y", "a"):
        with _approve_lock:
            if answer == "a":
                _always.add(root["id"])
            _approved[key] = now + _APPROVE_GRACE
        return None
    return (f"ERROR: {tool} not run — the user declined editing a file in {root['id']} "
            "(linked project). Don't retry; ask what they want instead.")


def project_scope_error(
    path: pathlib.Path, tool: str, escape: str = "force=true", *, write: bool = False,
) -> str | None:
    """Default guard for codebase tools: stay inside the current project (or a linked one).

    Reads may go into any linked root; writes follow that root's ``write`` policy.
    """
    if is_within(path, CWD):
        return None
    root = linked_root_for(path)
    if root is not None:
        return linked_write_error(path, tool) if write else None
    return (
        f"ERROR: {tool} refused outside-project path '{path}'. "
        f"This codebase is rooted at '{CWD}'. Use a project-relative path, "
        f"or pass {escape} only when the user explicitly asked for that outside file."
    )


def write_scope_error(path: pathlib.Path, tool: str, allow_outside: bool = False) -> str | None:
    """Guard for write/edit tools: ``allow_outside_project`` opens truly outside paths but
    never bypasses a linked project's write policy."""
    if allow_outside:
        return linked_write_error(path, tool)
    return project_scope_error(path, tool, "allow_outside_project=true", write=True)
