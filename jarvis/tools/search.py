"""Code search via ripgrep/grep."""
import shlex, subprocess
from .shell import run_bash


def search_code(pattern: str, path: str = ".") -> str:
    has_rg = subprocess.run("which rg", shell=True, capture_output=True).returncode == 0
    cmd = (f"rg -n --max-count 50 {shlex.quote(pattern)} {shlex.quote(path)}"
           if has_rg else
           f"grep -rn --max-count=50 {shlex.quote(pattern)} {shlex.quote(path)}")
    return run_bash(cmd, 20)
