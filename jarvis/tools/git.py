"""Git tool wrappers."""
import shlex
from ..constants import GIT_LOG_DEFAULT_COUNT, DEFAULT_BASH_TIMEOUT
from .shell import run_bash


def git_status(): return run_bash("git status -sb", DEFAULT_BASH_TIMEOUT)
def git_diff(path: str = ""): return run_bash(f"git diff {shlex.quote(path)}" if path else "git diff", 15)
def git_log(n: int = GIT_LOG_DEFAULT_COUNT): return run_bash(f"git log --oneline -n {int(n)}", DEFAULT_BASH_TIMEOUT)
