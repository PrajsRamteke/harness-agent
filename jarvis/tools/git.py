"""Git tool wrappers."""
import shlex
from .shell import run_bash


def git_status(): return run_bash("git status -sb", 10)
def git_diff(path: str = ""): return run_bash(f"git diff {shlex.quote(path)}" if path else "git diff", 15)
def git_log(n: int = 10): return run_bash(f"git log --oneline -n {int(n)}", 10)
