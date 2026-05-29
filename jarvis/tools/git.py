"""Git tool wrappers."""
import shlex
from ..constants import GIT_LOG_DEFAULT_COUNT, DEFAULT_BASH_TIMEOUT
from .shell import run_bash


def git_status(): return run_bash("git --no-pager status -sb", DEFAULT_BASH_TIMEOUT)
def git_diff(path: str = "", staged=None):
    # staged=True -> only staged (git add'ed) changes; staged=False -> only unstaged.
    # Default (None) -> diff against HEAD so staged AND unstaged changes both show.
    flag = "--cached" if staged is True else ("HEAD" if staged is None else "")
    cmd = "git --no-pager diff" + (f" {flag}" if flag else "")
    if path:
        cmd += f" -- {shlex.quote(path)}"
    return run_bash(cmd, 15)
def git_log(n: int = GIT_LOG_DEFAULT_COUNT): return run_bash(f"git --no-pager log --oneline -n {int(n)}", DEFAULT_BASH_TIMEOUT)
