"""Handlers for /think /auto /verbose /multi /tokens /cost /stats /model and auth subcmds."""
import os, time

from ..console import console, Panel, Table
from ..constants import (
    AVAILABLE_MODELS, KEY_FILE, OPENROUTER_KEY_FILE, OPENCODE_KEY_FILE,
    AUTH_MODE_FILE, PROVIDER_FILE, PROVIDER_LABELS,
    OPENROUTER_DEFAULT_MODEL, OPENCODE_DEFAULT_MODEL, models_for,
)
from ..constants.models import MODEL as _DEFAULT_ANTHROPIC_MODEL
from ..utils.io import _secure_write
from ..utils.time_fmt import fmt_duration
from ..auth.oauth_tokens import load_oauth_tokens, clear_oauth_tokens
from ..auth.oauth_flow import oauth_login
from ..auth.openrouter import prompt_for_openrouter_key, load_openrouter_key
from ..auth.opencode import prompt_for_opencode_key, load_opencode_key
from ..auth.client import _build_client_from_mode, _build_opencode_client
from ..repl.banners import header_panel
from ..repl.stats import estimated_cost
from ..storage.prefs import save_last_model
from .. import state


def handle_control(c: str, arg: str):
    """Return (handled, new_inp_or_None)."""
    # ── mode switching ─────────────────────────────────────────────────────────
    if c == "/coding":
        _toggle_coding_mode()
        return True, None
    if c == "/mode":
        _handle_mode(arg)
        return True, None
    # ─────────────────────────────────────────────────────────────────────────
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
    if c == "/provider":
        _handle_provider(arg)
        return True, None
    return False, None


def _all_models():
    """Combined list: [(provider, model_id, description), ...]."""
    rows = [("anthropic", m, d) for m, d in models_for("anthropic")]
    rows += [("openrouter", m, d) for m, d in models_for("openrouter")]
    rows += [("opencode", m, d) for m, d in models_for("opencode")]
    return rows


_OPENCODE_MODEL_IDS = {m for m, _ in models_for("opencode")}


def _provider_for_model(model: str) -> str:
    """Determine provider from model id."""
    if model in _OPENCODE_MODEL_IDS:
        return "opencode"
    if "/" in model:
        return "openrouter"
    return "anthropic"


def _apply_model_selection(chosen: str):
    target_provider = _provider_for_model(chosen)
    if target_provider != state.provider:
        _handle_provider(target_provider)
        if state.provider != target_provider:
            return  # switch failed (e.g. user cancelled key prompt)
    state.MODEL = chosen
    save_last_model()
    console.print(f"[green]✓ model switched to[/] [cyan]{state.MODEL}[/] "
                  f"[dim]({PROVIDER_LABELS[state.provider]})[/]")
    header_panel()


def _handle_model(arg: str):
    rows = _all_models()
    if arg:
        chosen = None
        if arg.isdigit():
            i = int(arg) - 1
            if 0 <= i < len(rows):
                chosen = rows[i][1]
        else:
            for _, m, _d in rows:
                if arg == m or arg in m:
                    chosen = m; break
            if not chosen:
                # Freeform: accept any string. '/' → OpenRouter slug, else Anthropic id.
                chosen = arg
        if chosen:
            _apply_model_selection(chosen)
        else:
            console.print(f"[red]unknown model: {arg}[/]")
        return

    t = Table(show_header=True, box=None, pad_edge=False)
    t.add_column("#", style="dim")
    t.add_column("model", style="cyan")
    t.add_column("description")
    t.add_column("provider", style="magenta")
    t.add_column("")
    for i, (prov, m, desc) in enumerate(rows, 1):
        marker = "[green]● current[/]" if m == state.MODEL else ""
        t.add_row(str(i), m, desc, PROVIDER_LABELS[prov], marker)
    console.print(Panel(t, title="🤖 available models — all providers", border_style="cyan"))
    try:
        sel = console.input("[cyan]select model (# or name, enter to cancel): [/]").strip()
    except (RuntimeError, EOFError):
        console.print("[dim]run [cyan]/model <# or name>[/] to switch[/]")
        return
    if sel:
        chosen = None
        if sel.isdigit():
            i = int(sel) - 1
            if 0 <= i < len(rows):
                chosen = rows[i][1]
        else:
            for _, m, _d in rows:
                if sel == m or sel in m:
                    chosen = m; break
            if not chosen:
                chosen = sel  # accept any free-form id (Claude or OR slug)
        if chosen:
            _apply_model_selection(chosen)
        else:
            console.print(f"[red]invalid selection: {sel}[/]")


def _handle_auth():
    lines = [f"provider: [bold cyan]{PROVIDER_LABELS.get(state.provider, state.provider)}[/]"]
    if state.provider == "openrouter":
        has_env = bool(os.getenv("OPENROUTER_API_KEY"))
        lines.append("auth: [bold]API key[/]")
        lines.append("source: " + ("env OPENROUTER_API_KEY" if has_env else f"{OPENROUTER_KEY_FILE}"))
        if not has_env and OPENROUTER_KEY_FILE.exists():
            k = OPENROUTER_KEY_FILE.read_text().strip()
            lines.append(f"key: sk-or-…{k[-6:]}")
        lines.append(f"model: [cyan]{state.MODEL}[/]")
    elif state.provider == "opencode":
        has_env = bool(os.getenv("OPENCODE_API_KEY"))
        lines.append("auth: [bold]API key[/]")
        lines.append("source: " + ("env OPENCODE_API_KEY" if has_env else f"{OPENCODE_KEY_FILE}"))
        if not has_env and OPENCODE_KEY_FILE.exists():
            k = OPENCODE_KEY_FILE.read_text().strip()
            lines.append(f"key: …{k[-6:]}")
        lines.append(f"model: [cyan]{state.MODEL}[/]")
    else:
        lines.append(f"auth: [bold]{state.auth_mode}[/]")
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
        lines.append(f"model: [cyan]{state.MODEL}[/]")
    console.print(Panel("\n".join(lines), title="🔐 auth", border_style="cyan"))


def _handle_provider(arg: str):
    """/provider [anthropic|openrouter|opencode] — switch provider mid-session."""
    target = arg.strip().lower() if arg else ""
    if not target:
        console.print(Panel(
            f"current provider: [bold cyan]{PROVIDER_LABELS.get(state.provider, state.provider)}[/]\n\n"
            "  [cyan]1[/]  Anthropic     [dim](Claude models)[/]\n"
            "  [cyan]2[/]  OpenRouter    [dim](free & paid)[/]\n"
            "  [cyan]3[/]  OpenCode Go   [dim](GLM, Kimi, DeepSeek, MiMo, MiniMax, Qwen)[/]\n\n"
            "usage: [dim]/provider anthropic[/] or [dim]/provider openrouter[/] or [dim]/provider opencode[/]",
            title="🌐 provider", border_style="cyan",
        ))
        try:
            sel = console.input("choose [1/2/3, enter to cancel]: ").strip().lower()
        except (RuntimeError, EOFError):
            console.print("[dim]TUI mode — run [cyan]/provider anthropic[/], "
                          "[cyan]/provider openrouter[/], or [cyan]/provider opencode[/] to switch.[/]")
            return
        if sel in ("1", "anthropic", "a"):       target = "anthropic"
        elif sel in ("2", "openrouter", "or"):   target = "openrouter"
        elif sel in ("3", "opencode", "oc"):     target = "opencode"
        else: return
    if target not in ("anthropic", "openrouter", "opencode"):
        console.print(f"[red]unknown provider: {target}[/]"); return
    if target == state.provider:
        console.print(f"[dim]already on {PROVIDER_LABELS[target]}[/]"); return

    prev_provider = state.provider
    state.provider = target
    _secure_write(PROVIDER_FILE, target)

    # Switch to a sensible default model for the new provider.
    if target == "openrouter":
        if "/" not in state.MODEL:
            state.MODEL = OPENROUTER_DEFAULT_MODEL
        if not os.getenv("OPENROUTER_API_KEY") and not OPENROUTER_KEY_FILE.exists():
            prompt_for_openrouter_key()
    elif target == "opencode":
        if state.MODEL not in _OPENCODE_MODEL_IDS:
            state.MODEL = OPENCODE_DEFAULT_MODEL
        if not os.getenv("OPENCODE_API_KEY") and not OPENCODE_KEY_FILE.exists():
            prompt_for_opencode_key()
    else:
        if "/" in state.MODEL or state.MODEL in _OPENCODE_MODEL_IDS:
            state.MODEL = _DEFAULT_ANTHROPIC_MODEL

    try:
        if target == "opencode":
            state.client = _build_opencode_client()
        else:
            state.client = _build_client_from_mode(
                "openrouter" if target == "openrouter" else state.auth_mode
            )
        console.print(f"[green]✓ switched to[/] [bold cyan]{PROVIDER_LABELS[target]}[/] "
                      f"[dim](model: {state.MODEL})[/]")
        header_panel()
        save_last_model()
    except Exception as e:
        console.print(f"[red]failed to switch provider: {e}[/]")
        state.provider = prev_provider
        _secure_write(PROVIDER_FILE, prev_provider)


# ── mode helpers ───────────────────────────────────────────────────────────────

_VALID_MODES = ("default", "coding")


def _toggle_coding_mode() -> None:
    """/coding — toggle between default and coding modes."""
    if state.active_mode == "coding":
        state.active_mode = "default"
        console.print(
            "[dim]🔘 Coding mode [red]OFF[/] — back to default system prompt.[/]"
        )
    else:
        state.active_mode = "coding"
        console.print(
            "[bold #00d7af]⚡ Coding mode ON[/] [dim]— CODING_ADDON rules are now active.[/]"
        )
    header_panel(compact=True)


def _handle_mode(arg: str) -> None:
    """/mode [name] — show current mode or switch to a named mode."""
    target = arg.strip().lower()
    if not target:
        # Show available modes
        label, colour, _style = state.MODE_LABELS.get(
            state.active_mode, (state.active_mode, "#ffffff", "")
        )
        lines = [
            f"current mode: [{colour}]{label}[/]\n",
            "available modes:",
        ]
        for m in _VALID_MODES:
            lbl, col, _s = state.MODE_LABELS.get(m, (m, "#ffffff", ""))
            marker = " ← active" if m == state.active_mode else ""
            lines.append(f"  [cyan]{m}[/]  [{col}]{lbl}[/]{marker}")
        lines.append("\nusage: [dim]/mode coding[/]  or  [dim]/coding[/] to toggle  or  [dim]Tab[/] in TUI")
        console.print(Panel("\n".join(lines), title="🎛  mode", border_style="cyan"))
        return

    if target not in _VALID_MODES:
        console.print(
            f"[red]unknown mode: {target}[/]  "
            f"valid: {', '.join(_VALID_MODES)}"
        )
        return

    if target == state.active_mode:
        console.print(f"[dim]already in {target} mode[/]")
        return

    state.active_mode = target
    lbl, col, _s = state.MODE_LABELS.get(target, (target, "#ffffff", ""))
    console.print(f"[{col}]✓ mode switched to {lbl}[/]")
    header_panel(compact=True)
