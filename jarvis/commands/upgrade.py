"""/upgrade — update Jarvis to the latest version via git pull + pip install.

Detects the install directory by:
  1. Resolving `which jarvis` → symlink → repo root (installed via scripts/install)
  2. Walking up from `__file__` to find the repo root with .git (dev/editable install)
"""
import pathlib
import shutil
import subprocess
import sys

from ..console import console
from ..constants import VERSION
from .. import state

Path = pathlib.Path


def _find_repo_root() -> pathlib.Path | None:
    """Locate the Jarvis repository root by following the jarvis binary symlink,
    then walking up until we find pyproject.toml + .git."""
    candidates: list[pathlib.Path] = []

    # 1) Follow the `jarvis` symlink
    try:
        jarvis_bin = shutil.which("jarvis")
        if jarvis_bin:
            resolved = pathlib.Path(jarvis_bin).resolve()
            candidates.append(resolved.parent.parent)  # .venv/bin/jarvis → .venv → repo
            # Also check one level up (editable install where jarvis != .venv/bin/jarvis)
            candidates.append(resolved.parent.parent.parent)
    except Exception:
        pass

    # 2) Walk up from __file__ (the installed jarvis package)
    candidates.append(pathlib.Path(__file__).resolve().parent.parent)  # jarvis/commands → jarvis → repo
    candidates.append(pathlib.Path(__file__).resolve().parent.parent.parent)  # extra level

    # 3) Try CWD (user is running from repo root)
    candidates.append(pathlib.Path.cwd())

    seen = set()
    for candidate in candidates:
        try:
            candidate = candidate.resolve()
        except Exception:
            continue
        # Walk up looking for pyproject.toml + .git
        for parent in [candidate] + list(candidate.parents):
            if parent in seen:
                continue
            seen.add(parent)
            if (parent / "pyproject.toml").is_file() and (parent / ".git").is_dir():
                return parent

    return None


def _run(cmd: list[str], cwd: pathlib.Path, timeout: int = 120) -> tuple[int, str, str]:
    """Run a command, return (returncode, stdout, stderr)."""
    try:
        r = subprocess.run(
            cmd,
            cwd=str(cwd),
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        return r.returncode, r.stdout.strip(), r.stderr.strip()
    except subprocess.TimeoutExpired:
        return -1, "", f"Timed out after {timeout}s"
    except FileNotFoundError as e:
        return -1, "", f"Command not found: {e}"
    except Exception as e:
        return -1, "", str(e)


def _format_output(title: str, stdout: str, stderr: str, rc: int) -> str:
    """Format command output for display."""
    lines = [f"[bold]{title}[/] [dim](exit {rc})[/]"]
    if stdout:
        # Show last N lines to avoid overwhelming
        out_lines = stdout.split("\n")
        if len(out_lines) > 12:
            lines.append(f"[dim]… ({len(out_lines) - 12} lines hidden)[/]")
            out_lines = out_lines[-12:]
        for l in out_lines:
            lines.append(f"  {l}")
    if stderr:
        lines.append(f"[dim]stderr:[/]")
        for l in stderr.split("\n")[-6:]:
            lines.append(f"  [dim]{l}[/]")
    return "\n".join(lines)


def cmd_upgrade(arg: str) -> bool:
    """/upgrade — update Jarvis via git pull + pip install.

    Returns True if handled.
    """
    # Fast-path: if user typed /upgrade (no args or "check")
    arg = arg.strip().lower()

    console.print("[cyan]◎ Locating Jarvis installation…[/]")

    repo_root = _find_repo_root()
    if repo_root is None:
        console.print(
            "[red]Could not find Jarvis repository root.[/]\n\n"
            "To manually upgrade:\n\n"
            "  [dim]# If installed via the installer:[/]\n"
            f"  [cyan]{Path('~/.local/share/harness-agent').expanduser()}[/] not found.\n\n"
            "  [dim]# Try running the install script again:[/]\n"
            "  [cyan]curl -fsSL https://raw.githubusercontent.com/PrajsRamteke/harness-agent/main/scripts/install | bash[/]\n\n"
            "  [dim]# If in a dev clone:[/]\n"
            "  cd /path/to/harness && git pull && pip install -e .\n"
        )
        return True

    console.print(f"[dim]Repo root: {repo_root}[/]")

    # Check if it's a git repo
    if not (repo_root / ".git").is_dir():
        console.print(f"[red]{repo_root} is not a git repository. Cannot auto-upgrade.[/]")
        return True

    if arg == "check":
        # Dry-run: just show current version + remote status
        rc, out, err = _run(["git", "rev-parse", "--short", "HEAD"], repo_root)
        local_commit = out or "unknown"

        rc, out, err = _run(["git", "remote", "get-url", "origin"], repo_root)
        remote_url = out or "unknown"

        # Fetch without pulling
        console.print("[dim]Fetching remote info…[/]")
        rc, out, err = _run(["git", "fetch", "origin"], repo_root)

        rc, out, err = _run(
            ["git", "rev-list", "--count", "HEAD..origin/main"], repo_root
        )
        behind = int(out) if out and out.isdigit() else 0

        rc, out, err = _run(["git", "rev-parse", "--short", "origin/main"], repo_root)
        remote_commit = out or "unknown"

        from rich.panel import Panel
        from rich.table import Table

        table = Table(show_header=False, box=None, padding=(0, 2))
        table.add_row("≡ Version", f"[cyan]v{VERSION}[/]")
        table.add_row("▣ Location", f"[dim]{repo_root}[/]")
        table.add_row("⌗ Remote", remote_url)
        table.add_row("⚙ Local commit", local_commit)
        table.add_row("✦ Remote commit", remote_commit)
        table.add_row("↓  Behind remote", f"{'[yellow]' if behind > 0 else '[green]'}{behind} commit{'s' if behind != 1 else ''}[/]")

        console.print(Panel(table, title="◎ Jarvis Upgrade Check", border_style="cyan"))

        if behind > 0:
            console.print(f"\n[yellow]{behind} update{'s' if behind != 1 else ''} available.[/] Run [cyan]/upgrade[/] to apply.")
        else:
            console.print("\n[green]✓ Up to date.[/]")
        return True

    # ── Actual upgrade ─────────────────────────────────────────────
    console.print("[cyan]↓ Fetching latest changes…[/]")

    # Step 1: git fetch
    rc, out, err = _run(["git", "fetch", "origin"], repo_root)
    console.print(_format_output("git fetch", out, err, rc))
    if rc != 0:
        console.print("[red]✗ git fetch failed. Aborting.[/]")
        return True

    # Step 2: git pull --ff-only
    branch_name = "main"  # default
    rc, out, _ = _run(["git", "rev-parse", "--abbrev-ref", "HEAD"], repo_root)
    if rc == 0 and out:
        branch_name = out.strip()

    console.print(f"[cyan]⬇ Pulling {branch_name}…[/]")
    rc, out, err = _run(["git", "pull", "--ff-only", "origin", branch_name], repo_root)
    console.print(_format_output("git pull", out, err, rc))
    if rc != 0:
        console.print("[red]✗ git pull failed. Check for local changes or merge conflicts.[/]")
        return True

    # Step 3: pip install
    console.print("[cyan]≡ Installing dependencies…[/]")

    # Detect the python from the venv or system
    venv_python = repo_root / ".venv" / "bin" / "python"
    python_cmd = str(venv_python) if venv_python.is_file() else sys.executable

    rc, out, err = _run([python_cmd, "-m", "pip", "install", "."], repo_root, timeout=180)
    console.print(_format_output("pip install .", out, err, rc))

    if rc == 0:
        # Read the new version
        try:
            new_version_file = repo_root / "jarvis" / "constants" / "models.py"
            if new_version_file.is_file():
                for line in new_version_file.read_text().split("\n"):
                    if line.startswith("VERSION"):
                        new_ver = line.split("=")[-1].strip().strip('"').strip("'")
                        break
                else:
                    new_ver = "?"
            else:
                new_ver = "?"
        except Exception:
            new_ver = "?"

        console.print(f"\n[green bold]✓ Upgrade complete![/] [dim]v{VERSION} → v{new_ver}[/]")
        console.print("[dim]Your next session will use the updated code.[/]")
        console.print("[dim]Note: You may need to restart the current session for changes to take full effect ([/][cyan]/new[/][dim]).[/]")
    else:
        console.print("[red]✗ pip install failed. See output above.[/]")

    return True
