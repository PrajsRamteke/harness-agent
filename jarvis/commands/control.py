"""Handlers for /think /auto /verbose /multi /tokens /cost /stats /model and auth subcmds."""
import os, time

from ..console import console, Panel, Table
from ..constants import AVAILABLE_MODELS, KEY_FILE, AUTH_MODE_FILE
from ..utils.io import _secure_write
from ..utils.time_fmt import fmt_duration
from ..auth.oauth_tokens import load_oauth_tokens, clear_oauth_tokens
from ..auth.oauth_flow import oauth_login
from ..auth.client import _build_client_from_mode
from ..repl.banners import header_panel
from ..repl.stats import estimated_cost
from .. import state


def handle_control(c: str, arg: str):
    """Return (handled, new_inp_or_None)."""
    if c == "/multi":
        console.print("[dim]enter multiline message, end with a single ';;' line:[/]")
        buf = []
        while True:
            try: line = input()
            except EOFError: break
            if line.strip() == ";;": break
            buf.append(line)
        inp = "\n".join(buf)
        if not inp.strip():
            console.print("[dim]empty[/]"); return True, None
        return True, inp
    if c == "/think":
        state.think_mode = not state.think_mode; header_panel(); return True, None
    if c == "/auto":
        state.auto_approve = not state.auto_approve; header_panel(); return True, None
    if c in ("/verbose", "/debug"):
        state.show_internal = not state.show_internal
        mode = "shown" if state.show_internal else "hidden"
        console.print(f"[green]internal tool trace {mode}[/]")
        header_panel()
        return True, None
    if c == "/tokens":
        console.print(f"in:{state.total_in}  out:{state.total_out}  total:{state.total_in+state.total_out}")
        return True, None
    if c == "/cost":
        console.print(f"[green]≈ ${estimated_cost():.4f}[/] "
                      f"[dim]({state.total_in} in + {state.total_out} out @ {state.MODEL})[/]")
        return True, None
    if c == "/stats":
        import pathlib
        t = Table(show_header=False, box=None, padding=(0, 2))
        t.add_row("⏱  elapsed", fmt_duration(time.time() - state.session_start))
        t.add_row("💬 messages", str(len(state.messages)))
        t.add_row("🔧 tool calls", str(state.tool_calls_count))
        t.add_row("🛠  internals", "shown" if state.show_internal else "hidden")
        t.add_row("⇅ tokens in/out", f"{state.total_in} / {state.total_out}")
        t.add_row("💰 est. cost", f"${estimated_cost():.4f}")
        t.add_row("🤖 model", state.MODEL)
        t.add_row("📂 cwd", str(pathlib.Path.cwd()))
        console.print(Panel(t, title="📊 session stats", border_style="cyan"))
        return True, None
    if c == "/model":
        _handle_model(arg)
        return True, None
    if c == "/key" and arg == "reset":
        KEY_FILE.unlink(missing_ok=True)
        console.print("[green]key deleted — restart the agent[/]")
        return True, None
    if c == "/login":
        clear_oauth_tokens()
        oauth_login()
        _secure_write(AUTH_MODE_FILE, "oauth")
        state.auth_mode = "oauth"
        try:
            state.client = _build_client_from_mode("oauth")
            state.client.models.list(limit=1)
            console.print("[green]✓ OAuth client active[/]")
        except Exception as e:
            console.print(f"[red]login validation failed: {e}[/]")
        return True, None
    if c == "/logout":
        clear_oauth_tokens()
        if KEY_FILE.exists() or os.getenv("ANTHROPIC_API_KEY"):
            _secure_write(AUTH_MODE_FILE, "api_key")
            state.auth_mode = "api_key"
            try:
                state.client = _build_client_from_mode("api_key")
                console.print("[green]logged out — falling back to API key[/]")
            except Exception as e:
                console.print(f"[red]{e}[/]")
        else:
            console.print("[yellow]logged out — no API key configured, restart to set one[/]")
        return True, None
    if c == "/auth":
        _handle_auth()
        return True, None
    return False, None


def _handle_model(arg: str):
    if arg:
        chosen = None
        if arg.isdigit():
            i = int(arg) - 1
            if 0 <= i < len(AVAILABLE_MODELS):
                chosen = AVAILABLE_MODELS[i][0]
        else:
            for m, _ in AVAILABLE_MODELS:
                if arg == m or arg in m:
                    chosen = m; break
            if not chosen:
                chosen = arg  # accept raw model id
        if chosen:
            state.MODEL = chosen
            console.print(f"[green]✓ model switched to[/] [cyan]{state.MODEL}[/]")
            header_panel()
        else:
            console.print(f"[red]unknown model: {arg}[/]")
        return
    t = Table(show_header=True, box=None, pad_edge=False)
    t.add_column("#", style="dim"); t.add_column("model", style="cyan"); t.add_column("description"); t.add_column("")
    for i, (m, desc) in enumerate(AVAILABLE_MODELS, 1):
        marker = "[green]● current[/]" if m == state.MODEL else ""
        t.add_row(str(i), m, desc, marker)
    console.print(Panel(t, title="🤖 available models", border_style="cyan"))
    sel = console.input("[cyan]select model (# or name, enter to cancel): [/]").strip()
    if sel:
        chosen = None
        if sel.isdigit():
            i = int(sel) - 1
            if 0 <= i < len(AVAILABLE_MODELS):
                chosen = AVAILABLE_MODELS[i][0]
        else:
            for m, _ in AVAILABLE_MODELS:
                if sel == m or sel in m:
                    chosen = m; break
        if chosen:
            state.MODEL = chosen
            console.print(f"[green]✓ model switched to[/] [cyan]{state.MODEL}[/]")
            header_panel()
        else:
            console.print(f"[red]invalid selection: {sel}[/]")


def _handle_auth():
    lines = [f"mode: [bold]{state.auth_mode}[/]"]
    if state.auth_mode == "oauth":
        t = load_oauth_tokens()
        if t:
            rem = int(t.get("expires_at", 0) - time.time())
            lines.append(f"access token: …{t['access_token'][-6:]}")
            lines.append(f"expires in: {rem}s" if rem > 0 else "[red]expired[/]")
            lines.append(f"scopes: {' '.join(t.get('scopes', []))}")
        else:
            lines.append("[red]no tokens stored[/]")
    else:
        has_env = bool(os.getenv("ANTHROPIC_API_KEY"))
        lines.append("source: " + ("env ANTHROPIC_API_KEY" if has_env else f"{KEY_FILE}"))
    console.print(Panel("\n".join(lines), title="🔐 auth", border_style="cyan"))
