"""Code search via ripgrep/grep."""
import os, shlex, subprocess
from ..constants import SEARCH_MATCH_CAP
from ..path_resolve import guard_refs, linked_root_for, project_scope_error, robust_resolve
from .shell import run_bash


_SKIP_GLOBS = [
    "node_modules", ".venv", "venv", "__pycache__", ".git", "dist", "build",
    ".next", ".mypy_cache", ".pytest_cache", ".ruff_cache",
]


def _command(pattern: str, cmd_path: str, *, linked: bool = False) -> str:
    has_rg = subprocess.run("which rg", shell=True, capture_output=True).returncode == 0
    if has_rg:
        # ripgrep respects .gitignore by default; add explicit globs for safety.
        skips = " ".join(f"-g '!{d}'" for d in _SKIP_GLOBS)
        if linked:
            skips += " -g '!.env*' -H"      # linked projects: never search secrets files
        return f"rg -n --max-count {SEARCH_MATCH_CAP} {skips} {shlex.quote(pattern)} {shlex.quote(cmd_path)}"
    excludes = " ".join(f"--exclude-dir={d}" for d in _SKIP_GLOBS)
    if linked:
        excludes += " --exclude='.env*' -H"
    return f"grep -rn --max-count={SEARCH_MATCH_CAP} {excludes} {shlex.quote(pattern)} {shlex.quote(cmd_path)}"


def _search_linked(pattern: str, base, root: dict) -> tuple[int, str]:
    """Search ``base`` (inside a linked root) from that root; lines come back as ``id:path:line:text``."""
    import pathlib

    from .shell import _run_process

    cwd = root["path"]
    try:
        cmd_path = str(pathlib.Path(base).relative_to(cwd)) or "."
    except ValueError:
        cmd_path = "."
    env = os.environ.copy()
    env.setdefault("GIT_PAGER", "cat")
    code, out, err = _run_process(_command(pattern, cmd_path, linked=True), 20, env, cwd)
    lines = [f"{root['id']}:{ln}" for ln in (out or "").splitlines() if ln]
    return code, "\n".join(lines) + (f"\n[stderr]\n{err}" if err else "")


@guard_refs
def search_code(pattern: str, path: str = ".", allow_outside_project: bool = False, project=None) -> str:
    from .dirs import _bases

    bases, err = _bases(path, project)
    if err:
        return err
    for base in bases:
        if not allow_outside_project:
            scope_err = project_scope_error(base, "search_code", "allow_outside_project=true")
            if scope_err:
                return scope_err

    if len(bases) == 1 and linked_root_for(bases[0]) is None:
        root = bases[0]
        try:
            cmd_path = str(root.relative_to(robust_resolve("."))) or "."
        except ValueError:
            cmd_path = str(root)
        return run_bash(_command(pattern, cmd_path), 20)

    # Several roots (project=…) or a path inside a linked one: search each from its own
    # root and name the hits the way the model must pass them back — `api:src/x.ts:12:…`.
    from .shell import _format_result

    chunks: list[str] = []
    worst = 1
    for base in bases:
        linked = linked_root_for(base)
        if linked is None:
            try:
                cmd_path = str(base.relative_to(robust_resolve("."))) or "."
            except ValueError:
                cmd_path = str(base)
            out = run_bash(_command(pattern, cmd_path), 20)
            body = out.split("\n", 2)[2] if out.startswith("$ ") and out.count("\n") >= 2 else out
            chunks.append(body)
            if out.startswith("$ ") and "exit=0" in out.split("\n", 2)[1]:
                worst = 0
            continue
        code, body = _search_linked(pattern, base, linked)
        if code == 0:
            worst = 0
        if body.strip():
            chunks.append(body)
    names = ", ".join(dict.fromkeys(
        (linked_root_for(b) or {"id": "this project"})["id"] for b in bases))
    return _format_result(f"search {pattern!r} in {names}", worst, "\n".join(c for c in chunks if c.strip()))
