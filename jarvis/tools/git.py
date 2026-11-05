"""Git tool wrappers. ``project`` runs them in a linked project's repo instead of this one."""
import shlex
from ..constants import GIT_LOG_DEFAULT_COUNT, DEFAULT_BASH_TIMEOUT
from .shell import run_bash


def git_status(project=None): return run_bash("git --no-pager status -sb", DEFAULT_BASH_TIMEOUT, project)
def git_diff(path: str = "", staged=None, project=None):
    # staged=True -> only staged (git add'ed) changes; staged=False -> only unstaged.
    # Default (None) -> diff against HEAD so staged AND unstaged changes both show.
    flag = "--cached" if staged is True else ("HEAD" if staged is None else "")
    cmd = "git --no-pager diff" + (f" {flag}" if flag else "")
    if path:
        cmd += f" -- {shlex.quote(path)}"
    return run_bash(cmd, 15, project)
def git_log(n: int = GIT_LOG_DEFAULT_COUNT, project=None): return run_bash(f"git --no-pager log --oneline -n {int(n)}", DEFAULT_BASH_TIMEOUT, project)
