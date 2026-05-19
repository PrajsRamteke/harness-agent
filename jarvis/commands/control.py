"""Handlers for /think /auto /verbose /multi /tokens /cost /stats /model and auth subcmds."""
import os, time

from ..console import console, Panel, Table
from ..constants import (
    KEY_FILE, OPENROUTER_KEY_FILE, OPENCODE_KEY_FILE, OPENCODE_ZEN_KEY_FILE,
    AUTH_MODE_FILE, PROVIDER_FILE, PROVIDERS, PROVIDER_LABELS, MODEL_SOURCE_LABELS,
    OPENROUTER_DEFAULT_MODEL, OPENCODE_DEFAULT_MODEL, OPENCODE_ZEN_DEFAULT_MODEL,
    OPENCODE_ZEN_MODELS, THINK_EFFORTS, DEFAULT_THINK_EFFORT,
    models_for,
    PROVIDER_ANTHROPIC, PROVIDER_OPENROUTER, PROVIDER_OPENCODE, PROVIDER_OPENCODE_ZEN,
    PROVIDER_OPENAI_CODEX, PROVIDER_OPENAI_CODEX_AUTH,
    PROVIDER_ANTHROPIC_API, PROVIDER_ANTHROPIC_AUTH,
    AUTH_API_KEY, AUTH_OAUTH,
)
from ..constants.models import MODEL as _DEFAULT_ANTHROPIC_MODEL
from ..utils.io import _secure_write
from ..utils.time_fmt import fmt_duration
from ..auth.oauth_tokens import load_oauth_tokens, clear_oauth_tokens
from ..auth.codex_oauth_tokens import load_codex_oauth_tokens, clear_codex_oauth_tokens
from ..auth.oauth_flow import oauth_login
from ..auth.anthropic_models import sync_anthropic_model_ids, format_anthropic_model_lines
from ..auth.openrouter import prompt_for_openrouter_key, load_openrouter_key
from ..auth.opencode import prompt_for_opencode_key, load_opencode_key
from ..auth.opencode_zen import prompt_for_opencode_zen_key, load_opencode_zen_key
from ..auth.client import _build_client_from_mode, _build_opencode_client, _build_opencode_zen_client
from ..repl.banners import header_panel
from ..repl.stats import estimated_cost
from ..storage.prefs import save_last_model
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
        _handle_think(arg)
        return True, None
    if c == "/auto":
        state.auto_approve = not state.auto_approve; header_panel(); return True, None
    if c in ("/verbose", "/debug"):
        state.show_internal = not state.show_internal
        mode = "shown" if state.show_internal else "hidden"
        console.print(f"[green]internal tool trace {mode}[/]")
        header_panel()
        return True, None
    if c == "/tokens":
        console.print(f"in:{state.total_in}  out:{state.total_out}  total:{state.total_tokens}")
        return True, None
    if c == "/cost":
        console.print(f"[green]≈ ${estimated_cost():.4f}[/] "
                      f"[dim]({state.total_in} in + {state.total_out} out = {state.total_tokens} total @ {state.MODEL})[/]")
        return True, None
    if c == "/version":
        from ..constants import VERSION
        console.print(
            Panel(
                f"[bold #58a6ff]Jarvis[/] [dim]v[/][bold]{VERSION}[/]\n"
                f"[dim]{state.MODEL} · {state.provider} · agent: {state.active_agent_name or '—'}[/]",
                border_style="magenta",
                padding=(0, 2),
            )
        )
        return True, None
    if c == "/stats":
        import pathlib
        t = Table(show_header=False, box=None, padding=(0, 2))
        t.add_row("⏱  elapsed", fmt_duration(time.time() - state.session_start))
        t.add_row("◈ messages", str(len(state.messages)))
        t.add_row("⚙ tool calls", str(state.tool_calls_count))
        t.add_row("⚙  internals", "shown" if state.show_internal else "hidden")
        t.add_row("⇅ tokens in/out/total", f"{state.total_in} / {state.total_out} / {state.total_tokens}")
        t.add_row("✦ est. cost", f"${estimated_cost():.4f}")
        t.add_row("✦ model", state.MODEL)
        t.add_row("▣ cwd", str(pathlib.Path.cwd()))
        console.print(Panel(t, title="◆ session stats", border_style="cyan"))
        return True, None
    if c == "/model":
        _handle_model(arg)
        return True, None
    if c == "/theme":
        _handle_theme(arg)
        return True, None
    if c == "/key" and arg == "reset":
        KEY_FILE.unlink(missing_ok=True)
        console.print("[green]key deleted — restart the agent[/]")
        return True, None
    if c == "/login":
        clear_oauth_tokens()
        tokens = oauth_login()
        if not tokens:
            return True, None
        _secure_write(AUTH_MODE_FILE, AUTH_OAUTH)
        state.auth_mode = AUTH_OAUTH
        try:
            state.client = _build_client_from_mode(AUTH_OAUTH)
            model_ids = sync_anthropic_model_ids(state.client)
            if not model_ids:
                state.client.models.list(limit=1)
            console.print("[green]✓ OAuth client active[/]")
            if model_ids:
                console.print("[dim]Available models:[/]")
                for line in format_anthropic_model_lines(model_ids):
                    console.print(f"  [cyan]{line}[/]")
        except Exception as e:
            console.print(f"[red]login validation failed: {e}[/]")
        return True, None
    if c == "/logout":
        clear_oauth_tokens()
        if KEY_FILE.exists() or os.getenv("ANTHROPIC_API_KEY"):
            _secure_write(AUTH_MODE_FILE, AUTH_API_KEY)
            state.auth_mode = AUTH_API_KEY
            try:
                state.client = _build_client_from_mode(AUTH_API_KEY)
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


def _handle_think(arg: str = "") -> None:
    value = (arg or "").strip().lower()
    if not value:
        state.think_mode = not state.think_mode
    elif value in ("mode", "modes", "select"):
        console.print(
            "[cyan]thinking efforts:[/] xhigh, high, medium, low, minimal, none\n"
            "[dim]In the TUI, /think mode opens a picker.[/]"
        )
        return
    elif value in ("on", "true", "yes"):
        state.think_mode = True
        if state.think_effort == "none":
            state.think_effort = DEFAULT_THINK_EFFORT
    elif value in ("off", "false", "no"):
        state.think_mode = False
    elif value in THINK_EFFORTS:
        state.think_mode = value != "none"
        state.think_effort = value
    else:
        console.print(
            "[red]usage:[/] /think [on|off|xhigh|high|medium|low|minimal|none]"
        )
        return

    state.save_think_config()
    header_panel()


def _all_models():
    """Combined list: [(source, model_id, description), ...] for configured auth."""
    from ..constants import connected_model_sources, models_for_source
    rows = []
    for src in connected_model_sources():
        rows += [(src, m, d) for m, d in models_for_source(src)]
    return rows


_OPENCODE_MODEL_IDS = {m for m, _ in models_for(PROVIDER_OPENCODE)}
_OPENCODE_ZEN_MODEL_IDS = {m for m, _ in models_for(PROVIDER_OPENCODE_ZEN)}
_CODEX_MODEL_IDS = {m for m, _ in models_for(PROVIDER_OPENAI_CODEX)}


def _provider_for_model(model: str) -> str:
    """Determine provider from model id."""
    if model in _CODEX_MODEL_IDS:
        return PROVIDER_OPENAI_CODEX
    if model in _OPENCODE_MODEL_IDS:
        return PROVIDER_OPENCODE
    if model in _OPENCODE_ZEN_MODEL_IDS:
        return PROVIDER_OPENCODE_ZEN
    if "/" in model:
        return PROVIDER_OPENROUTER
    return PROVIDER_ANTHROPIC


def _apply_model_selection(chosen: str, *, source: str = ""):
    target_provider = _provider_for_model(chosen)
    if source in (PROVIDER_ANTHROPIC_API, PROVIDER_ANTHROPIC_AUTH):
        target_provider = PROVIDER_ANTHROPIC
    elif source == PROVIDER_OPENAI_CODEX_AUTH:
        target_provider = PROVIDER_OPENAI_CODEX

    if target_provider != state.provider:
        _handle_provider(target_provider)
        if state.provider != target_provider:
            return  # switch failed (e.g. user cancelled key prompt)

    if state.provider == PROVIDER_ANTHROPIC and source:
        target_auth = AUTH_OAUTH if source == PROVIDER_ANTHROPIC_AUTH else AUTH_API_KEY
        if target_auth != state.auth_mode:
            if target_auth == AUTH_OAUTH and not load_oauth_tokens():
                console.print("[yellow]OAuth not configured — run /login first[/]")
                return
            if target_auth == AUTH_API_KEY and not KEY_FILE.exists() and not os.getenv("ANTHROPIC_API_KEY"):
                console.print("[yellow]Anthropic API key not configured[/]")
                return
            state.auth_mode = target_auth
            _secure_write(AUTH_MODE_FILE, target_auth)
            try:
                state.client = _build_client_from_mode(target_auth)
            except Exception as e:
                console.print(f"[red]failed to switch auth mode: {e}[/]")
                return

    if state.provider == PROVIDER_OPENAI_CODEX and source == PROVIDER_OPENAI_CODEX_AUTH:
        if not load_codex_oauth_tokens():
            console.print("[yellow]OpenAI Codex OAuth not configured — run /login first[/]")
            return
        state.auth_mode = AUTH_OAUTH
        _secure_write(AUTH_MODE_FILE, AUTH_OAUTH)
        try:
            from ..auth.client import _build_codex_client
            state.client = _build_codex_client()
        except Exception as e:
            console.print(f"[red]failed to switch to Codex OAuth: {e}[/]")
            return

    state.MODEL = chosen
    save_last_model()
    src_label = MODEL_SOURCE_LABELS.get(source) or PROVIDER_LABELS.get(state.provider, state.provider)
    console.print(f"[green]✓ model switched to[/] [cyan]{state.MODEL}[/] "
                  f"[dim]({src_label})[/]")
    header_panel()


def _handle_model(arg: str):
    rows = _all_models()
    if arg:
        chosen = None
        if arg.isdigit():
            i = int(arg) - 1
            if 0 <= i < len(rows):
                src, chosen, _d = rows[i]
                _apply_model_selection(chosen, source=src)
                return
        else:
            for src, m, _d in rows:
                if arg == m or arg in m:
                    _apply_model_selection(m, source=src)
                    return
            if arg:
                # Freeform: accept any string. '/' → OpenRouter slug, else Anthropic id.
                _apply_model_selection(arg)
                return
        console.print(f"[red]unknown model: {arg}[/]")
        return

    t = Table(show_header=True, box=None, pad_edge=False)
    t.add_column("#", style="dim")
    t.add_column("model", style="cyan")
    t.add_column("description")
    t.add_column("provider", style="magenta")
    t.add_column("")
    for i, (src, m, desc) in enumerate(rows, 1):
        marker = "[green]● current[/]" if (
            m == state.MODEL and (
                (src == PROVIDER_ANTHROPIC_AUTH and state.auth_mode == AUTH_OAUTH and state.provider == PROVIDER_ANTHROPIC)
                or (src == PROVIDER_ANTHROPIC_API and state.auth_mode == AUTH_API_KEY and state.provider == PROVIDER_ANTHROPIC)
                or (src == PROVIDER_OPENAI_CODEX_AUTH and state.provider == PROVIDER_OPENAI_CODEX)
                or (src not in (PROVIDER_ANTHROPIC_API, PROVIDER_ANTHROPIC_AUTH, PROVIDER_OPENAI_CODEX_AUTH) and src == state.provider)
            )
        ) else ""
        t.add_row(str(i), m, desc, MODEL_SOURCE_LABELS.get(src, src), marker)
    console.print(Panel(t, title="✦ available models — all providers", border_style="cyan"))
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
    if state.provider == PROVIDER_OPENROUTER:
        has_env = bool(os.getenv("OPENROUTER_API_KEY"))
        lines.append("auth: [bold]API key[/]")
        lines.append("source: " + ("env OPENROUTER_API_KEY" if has_env else f"{OPENROUTER_KEY_FILE}"))
        if not has_env and OPENROUTER_KEY_FILE.exists():
            k = OPENROUTER_KEY_FILE.read_text().strip()
            lines.append(f"key: sk-or-…{k[-6:]}")
        lines.append(f"model: [cyan]{state.MODEL}[/]")
    elif state.provider == PROVIDER_OPENCODE:
        has_env = bool(os.getenv("OPENCODE_API_KEY"))
        lines.append("auth: [bold]API key[/]")
        lines.append("source: " + ("env OPENCODE_API_KEY" if has_env else f"{OPENCODE_KEY_FILE}"))
        if not has_env and OPENCODE_KEY_FILE.exists():
            k = OPENCODE_KEY_FILE.read_text().strip()
            lines.append(f"key: …{k[-6:]}")
        lines.append(f"model: [cyan]{state.MODEL}[/]")
    elif state.provider == PROVIDER_OPENCODE_ZEN:
        has_env = bool(os.getenv("OPENCODE_ZEN_API_KEY"))
        lines.append("auth: [bold]API key[/]")
        lines.append("source: " + ("env OPENCODE_ZEN_API_KEY" if has_env else f"{OPENCODE_ZEN_KEY_FILE}"))
        if not has_env and OPENCODE_ZEN_KEY_FILE.exists():
            k = OPENCODE_ZEN_KEY_FILE.read_text().strip()
            lines.append(f"key: …{k[-6:]}")
        lines.append(f"model: [cyan]{state.MODEL}[/]")
    else:
        lines.append(f"auth: [bold]{state.auth_mode}[/]")
        if state.auth_mode == AUTH_OAUTH:
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
    console.print(Panel("\n".join(lines), title="⬟ auth", border_style="cyan"))


def _handle_provider(arg: str):
    """/provider [anthropic|openrouter|opencode] — switch provider mid-session."""
    target = arg.strip().lower() if arg else ""
    if not target:
        console.print(Panel(
            f"current provider: [bold cyan]{PROVIDER_LABELS.get(state.provider, state.provider)}[/]\n\n"
            "  [cyan]1[/]  Anthropic          [dim](Claude models)[/]\n"
            "  [cyan]2[/]  OpenRouter         [dim](free & paid)[/]\n"
            "  [cyan]3[/]  OpenCode Go        [dim](GLM, Kimi, DeepSeek, MiMo, MiniMax, Qwen)[/]\n"
            "  [cyan]4[/]  OpenCode Zen       [dim](MiniMax, HY3, Nemotron)[/]\n\n"
            "usage: [dim]/provider anthropic[/], [dim]/provider openrouter[/], "
            "[dim]/provider opencode[/], or [dim]/provider opencode_zen[/]",
            title="◎ provider", border_style="cyan",
        ))
        try:
            sel = console.input("choose [1=Anthropic, 2=OpenRouter, 3=OpenCode Go, 4=OpenCode Zen, enter to cancel]: ").strip().lower()
        except (RuntimeError, EOFError):
            console.print("[dim]TUI mode — run [cyan]/provider anthropic[/], "
                          "[cyan]/provider openrouter[/], [cyan]/provider opencode[/], "
                          "or [cyan]/provider opencode_zen[/] to switch.[/]")
            return
        if sel in ("1", "anthropic", "a"):             target = PROVIDER_ANTHROPIC
        elif sel in ("2", "openrouter", "or"):         target = PROVIDER_OPENROUTER
        elif sel in ("3", "opencode", "oc"):           target = PROVIDER_OPENCODE
        elif sel in ("4", "opencode_zen", "zen", "z"): target = PROVIDER_OPENCODE_ZEN
        else: return
    if target not in (
        PROVIDER_ANTHROPIC, PROVIDER_OPENROUTER, PROVIDER_OPENCODE,
        PROVIDER_OPENCODE_ZEN, PROVIDER_OPENAI_CODEX,
    ):
        console.print(f"[red]unknown provider: {target}[/]"); return
    if target == state.provider:
        console.print(f"[dim]already on {PROVIDER_LABELS[target]}[/]"); return

    prev_provider = state.provider
    state.provider = target
    _secure_write(PROVIDER_FILE, target)

    # Switch to a sensible default model for the new provider.
    if target == PROVIDER_OPENROUTER:
        if "/" not in state.MODEL:
            state.MODEL = OPENROUTER_DEFAULT_MODEL
        if not os.getenv("OPENROUTER_API_KEY") and not OPENROUTER_KEY_FILE.exists():
            prompt_for_openrouter_key()
    elif target == PROVIDER_OPENCODE:
        if state.MODEL not in _OPENCODE_MODEL_IDS:
            state.MODEL = OPENCODE_DEFAULT_MODEL
        if not os.getenv("OPENCODE_API_KEY") and not OPENCODE_KEY_FILE.exists():
            prompt_for_opencode_key()
    elif target == PROVIDER_OPENCODE_ZEN:
        if state.MODEL not in _OPENCODE_ZEN_MODEL_IDS:
            state.MODEL = OPENCODE_ZEN_DEFAULT_MODEL
        if not os.getenv("OPENCODE_ZEN_API_KEY") and not OPENCODE_ZEN_KEY_FILE.exists():
            prompt_for_opencode_zen_key()
    elif target == PROVIDER_OPENAI_CODEX:
        from ..constants import CODEX_DEFAULT_MODEL
        if state.MODEL not in _CODEX_MODEL_IDS:
            state.MODEL = CODEX_DEFAULT_MODEL
        if not load_codex_oauth_tokens():
            console.print("[yellow]OpenAI Codex OAuth not configured — run /login first[/]")
            state.provider = prev_provider
            _secure_write(PROVIDER_FILE, prev_provider)
            return
        state.auth_mode = AUTH_OAUTH
        _secure_write(AUTH_MODE_FILE, AUTH_OAUTH)
    else:
        if "/" in state.MODEL or state.MODEL in _OPENCODE_MODEL_IDS:
            state.MODEL = _DEFAULT_ANTHROPIC_MODEL

    try:
        if target == PROVIDER_OPENCODE:
            state.client = _build_opencode_client()
        elif target == PROVIDER_OPENCODE_ZEN:
            state.client = _build_opencode_zen_client()
        elif target == PROVIDER_OPENAI_CODEX:
            from ..auth.client import _build_codex_client
            state.client = _build_codex_client()
            if state.client is None:
                raise RuntimeError("Codex OAuth client unavailable")
        else:
            state.client = _build_client_from_mode(
                PROVIDER_OPENROUTER if target == PROVIDER_OPENROUTER else state.auth_mode
            )
        console.print(f"[green]✓ switched to[/] [bold cyan]{PROVIDER_LABELS[target]}[/] "
                      f"[dim](model: {state.MODEL})[/]")
        header_panel()
        save_last_model()
    except Exception as e:
        console.print(f"[red]failed to switch provider: {e}[/]")
        state.provider = prev_provider
        _secure_write(PROVIDER_FILE, prev_provider)


def _handle_theme(arg: str) -> None:
    """/theme [red|purple] — switch visual theme."""
    target = arg.strip().lower()
    valid = list(state.THEMES.keys())
    if not target:
        cur = state.theme
        lines = [
            f"current theme: [bold]{cur}[/]\n",
            "available themes:",
        ]
        for t in valid:
            marker = " ← active" if t == cur else ""
            lines.append(f"  [cyan]{t}[/]{marker}")
        lines.append("\nusage: [dim]/theme <name>[/]  —  " + ", ".join(valid))
        console.print(Panel("\n".join(lines), title="✦ theme", border_style="cyan"))
        return

    if target not in valid:
        console.print(f"[red]unknown theme: {target}[/]  valid: {', '.join(valid)}")
        return

    if target == state.theme:
        console.print(f"[dim]already on {target} theme[/]")
        return

    state.theme = target
    state.theme_colors = state.THEMES[target]
    # Persist so it survives restarts (unified settings.json).
    try:
        from ..storage.settings import get_settings
        get_settings().set("theme", target)
    except Exception:
        pass
    console.print(f"[green]✓ switched to [bold]{target}[/] theme[/]")
    header_panel(compact=True)
