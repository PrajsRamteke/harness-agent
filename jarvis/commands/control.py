"""Handlers for /think /auto /plan /verbose /multi /tokens /cost /stats /model and auth subcmds."""
import os, time

from ..console import console, Panel, Table
from ..constants import (
    KEY_FILE, OPENROUTER_KEY_FILE, OPENCODE_KEY_FILE, OPENCODE_ZEN_KEY_FILE,
    AUTH_MODE_FILE, PROVIDER_FILE, PROVIDERS, PROVIDER_LABELS, MODEL_SOURCE_LABELS,
    OPENROUTER_DEFAULT_MODEL,
    HARNESS_AGENT_DEFAULT_MODEL, HARNESS_AGENT_MODEL_IDS,
    THINK_EFFORTS, DEFAULT_THINK_EFFORT,
    is_catalog_provider, provider_label,
    models_for, is_harness_agent_model, normalize_model_for_provider,
    model_belongs_to_provider,
    PROVIDER_ANTHROPIC, PROVIDER_OPENROUTER, PROVIDER_OPENCODE, PROVIDER_OPENCODE_ZEN,
    PROVIDER_HARNESS_AGENT,
    PROVIDER_OPENAI_CODEX, PROVIDER_OPENAI_CODEX_AUTH,
    PROVIDER_ANTHROPIC_API, PROVIDER_ANTHROPIC_AUTH,
    AUTH_API_KEY, AUTH_OAUTH,
)
from ..utils.io import _secure_write
from ..utils.time_fmt import fmt_duration
from ..auth.oauth_tokens import load_oauth_tokens
from ..auth.codex_oauth_tokens import load_codex_oauth_tokens
from ..auth.openrouter import prompt_for_openrouter_key, load_openrouter_key
from ..auth.opencode import prompt_for_opencode_key, load_opencode_key
from ..auth.opencode_zen import prompt_for_opencode_zen_key, has_opencode_zen_key
from ..auth.harness_agent import should_use_harness_agent_client
from ..auth.client import (
    _build_client_from_mode, _build_opencode_client,
    _build_opencode_zen_client_for_model, _has_anthropic_api_key,
)
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
    if c == "/plan":
        _handle_plan(arg)
        return True, None
    if c in ("/verbose", "/debug"):
        state.show_internal = not state.show_internal
        state.save_trace_config()
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
    if c in ("/model", "/mode"):
        _handle_model(arg)
        return True, None
    if c == "/theme":
        _handle_theme(arg)
        return True, None
    if c == "/provider":
        _handle_provider(arg)
        return True, None
    return False, None


def _handle_plan(arg: str = "") -> None:
    """/plan [on|off] — toggle read-only plan mode (session-scoped)."""
    value = (arg or "").strip().lower()
    if not value:
        state.plan_mode = not state.plan_mode
    elif value in ("on", "true", "yes"):
        state.plan_mode = True
    elif value in ("off", "false", "no"):
        state.plan_mode = False
    else:
        console.print("[red]usage:[/] /plan [on|off]")
        return

    if state.plan_mode:
        console.print(
            "[green]✓ plan mode on[/] [dim]— read-only tools only; the agent "
            "researches, presents a plan, and asks for your approval before "
            "any file edits or shell commands. /plan off to exit manually.[/]"
        )
    else:
        console.print("[green]✓ plan mode off[/] [dim]— full tool access restored[/]")
    header_panel()


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


def _all_models(live: bool = False):
    """Combined list: [(source, model_id, description), ...] for /model.

    ``live=False`` reads the cached catalogs (instant). ``live=True`` refreshes
    the free-model catalogs over the network first.
    """
    try:
        from ..tui.model_modal import model_picker_rows
        return model_picker_rows(live=live)
    except Exception:
        from ..constants import all_model_picker_rows
        return all_model_picker_rows(live=live, cached=not live)


def refresh_model_catalog() -> int:
    """/model refresh — re-read the live free-model catalogs. Returns row count."""
    from ..constants import refresh_model_catalogs

    refresh_model_catalogs(retry_blocked=True)
    rows = _all_models()
    free = sum(1 for _s, _m, desc in rows if "free" in desc.lower())
    console.print(
        f"[green]✓ model catalog refreshed[/] [dim]— {len(rows)} models "
        f"({free} free)[/]"
    )
    return len(rows)


def resolve_model_arg(arg: str) -> tuple[str, str] | None:
    """Map a ``/model <arg>`` argument to ``(model_id, source)``.

    Accepts a 1-based row number, an exact id, or a unique substring. Returns
    None for an unmatched non-numeric arg so callers can fall back to treating
    it as a raw provider model id.
    """
    arg = (arg or "").strip()
    if not arg:
        return None
    rows = _all_models()
    if arg.isdigit():
        i = int(arg) - 1
        if 0 <= i < len(rows):
            src, mid, _d = rows[i]
            return mid, src
        return None
    for src, mid, _d in rows:
        if arg == mid:
            return mid, src
    for src, mid, _d in rows:
        if arg in mid:
            return mid, src
    return None


_HARNESS_AGENT_MODEL_IDS = set(HARNESS_AGENT_MODEL_IDS)


def _provider_for_model(model: str) -> str:
    """Determine provider from model id."""
    # Not a frozen id set: the Codex line-up is discovered at runtime.
    if model_belongs_to_provider(model, PROVIDER_OPENAI_CODEX):
        return PROVIDER_OPENAI_CODEX
    if model in _HARNESS_AGENT_MODEL_IDS:
        return PROVIDER_OPENCODE_ZEN
    # OpenCode Go / Zen line-ups are live (models.dev + what each gateway
    # serves). Zen also serves Claude / GPT ids, so it is asked last: a typed
    # claude-… keeps going to Anthropic, gpt-… to ChatGPT (checked above).
    if model_belongs_to_provider(model, PROVIDER_OPENCODE):
        return PROVIDER_OPENCODE
    if "/" in model:
        return PROVIDER_OPENROUTER
    if model.startswith("claude-"):
        return PROVIDER_ANTHROPIC
    if model_belongs_to_provider(model, PROVIDER_OPENCODE_ZEN):
        return PROVIDER_OPENCODE_ZEN
    return PROVIDER_ANTHROPIC


def _apply_model_selection(chosen: str, *, source: str = ""):
    target_provider = _provider_for_model(chosen)
    if source == PROVIDER_HARNESS_AGENT:
        target_provider = PROVIDER_OPENCODE_ZEN
    elif source in (PROVIDER_OPENCODE_ZEN, PROVIDER_OPENCODE, PROVIDER_OPENROUTER):
        # The picked source decides: OpenCode Go serves gpt-… ids too, and
        # guessing from the id would send those to ChatGPT (Codex).
        target_provider = source
    elif is_catalog_provider(source):
        target_provider = source
    elif source in (PROVIDER_ANTHROPIC_API, PROVIDER_ANTHROPIC_AUTH):
        target_provider = PROVIDER_ANTHROPIC
    elif source == PROVIDER_OPENAI_CODEX_AUTH:
        target_provider = PROVIDER_OPENAI_CODEX

    skip_key = source == PROVIDER_HARNESS_AGENT
    if target_provider != state.provider:
        auth_mode = ""
        if source == PROVIDER_ANTHROPIC_AUTH:
            if not load_oauth_tokens():
                console.print("[yellow]Anthropic OAuth not configured — run /login first[/]")
                return
            auth_mode = AUTH_OAUTH
        elif source == PROVIDER_ANTHROPIC_API:
            if not _has_anthropic_api_key():
                console.print("[yellow]Anthropic API key not configured — add one with /key[/]")
                return
            auth_mode = AUTH_API_KEY
        _handle_provider(target_provider, skip_key_prompt=skip_key, auth_mode=auth_mode)
        if state.provider != target_provider:
            return  # switch failed (e.g. user cancelled key prompt)

    if state.provider == PROVIDER_OPENCODE_ZEN and source in (
        PROVIDER_HARNESS_AGENT, PROVIDER_OPENCODE_ZEN,
    ):
        try:
            state.client = _build_opencode_zen_client_for_model(chosen, source=source)
        except Exception as e:
            label = "Harness Agent" if should_use_harness_agent_client(chosen, source=source) else "OpenCode Zen"
            console.print(f"[red]failed to connect {label}: {e}[/]")
            return

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
                state.client = _build_client_from_mode(target_auth, interactive=False)
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
    src_label = MODEL_SOURCE_LABELS.get(source) or provider_label(state.provider)
    console.print(f"[green]✓ model switched to[/] [cyan]{state.MODEL}[/] "
                  f"[dim]({src_label})[/]")
    header_panel()


def _catalogs_fresh() -> bool:
    try:
        from ..constants import model_catalogs_are_fresh
        return model_catalogs_are_fresh()
    except Exception:
        return True


def _handle_model(arg: str):
    arg = (arg or "").strip()
    if arg.lower() in ("refresh", "reload", "sync"):
        refresh_model_catalog()
        return
    # Listing is the one place worth paying for a live fetch: the user is about
    # to choose, and a freshly retired free model is a dead end.
    rows = _all_models(live=not arg and not _catalogs_fresh())
    if arg:
        chosen = None
        hit = resolve_model_arg(arg)
        if hit:
            _apply_model_selection(hit[0], source=hit[1])
            return
        if not arg.isdigit():
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
                (src == PROVIDER_HARNESS_AGENT and state.provider == PROVIDER_OPENCODE_ZEN and state.harness_agent_free)
                or (src == PROVIDER_OPENCODE_ZEN and state.provider == PROVIDER_OPENCODE_ZEN and not state.harness_agent_free)
                or (src == PROVIDER_ANTHROPIC_AUTH and state.auth_mode == AUTH_OAUTH and state.provider == PROVIDER_ANTHROPIC)
                or (src == PROVIDER_ANTHROPIC_API and state.auth_mode == AUTH_API_KEY and state.provider == PROVIDER_ANTHROPIC)
                or (src == PROVIDER_OPENAI_CODEX_AUTH and state.provider == PROVIDER_OPENAI_CODEX)
                or (src not in (PROVIDER_HARNESS_AGENT, PROVIDER_OPENCODE_ZEN, PROVIDER_ANTHROPIC_API, PROVIDER_ANTHROPIC_AUTH, PROVIDER_OPENAI_CODEX_AUTH) and src == state.provider)
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
    lines = [f"provider: [bold cyan]{provider_label(state.provider)}[/]"]
    if is_catalog_provider(state.provider):
        from ..auth import catalog_keys

        source, value = catalog_keys.key_source(state.provider)
        lines.append("auth: [bold]API key[/] [dim](provider from models.dev)[/]")
        if source == "env":
            lines.append(f"source: env {catalog_keys.env_var_for(state.provider)}")
        elif source == "file":
            lines.append(f"source: {catalog_keys._file()}")
        if value:
            lines.append(f"key: …{value[-6:]}")
        lines.append(f"model: [cyan]{state.MODEL}[/]")
    elif state.provider == PROVIDER_OPENROUTER:
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
        if state.harness_agent_free and is_harness_agent_model(state.MODEL):
            lines.append("auth: [bold]Harness Agent[/] [dim](free — no API key)[/]")
        else:
            has_env = bool(os.getenv("OPENCODE_ZEN_API_KEY"))
            lines.append("auth: [bold]OpenCode Zen API key[/]")
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


def _revert_provider_switch(prev_provider: str) -> None:
    state.provider = prev_provider
    _secure_write(PROVIDER_FILE, prev_provider)


def apply_key_change(provider: str, *, removed: bool = False) -> str:
    """Make a key saved or deleted in /key take effect in this session.

    Saving the key the session is running on rebuilds the client, so the new
    key is used on the next turn. Deleting it drops to the free Harness Agent
    tier instead of leaving the old key live in memory. Keys for other
    providers only change what /model lists, which it re-reads on open.

    Returns a short note for the caller to show ("" when nothing changed).
    """
    label = provider_label(provider)
    if provider == PROVIDER_ANTHROPIC:
        in_use = state.provider == provider and state.auth_mode == AUTH_API_KEY
    elif provider == PROVIDER_OPENCODE_ZEN:
        in_use = state.provider == provider and not state.harness_agent_free
    else:
        in_use = state.provider == provider

    if removed:
        if not in_use:
            return ""
        from ..auth.client import _fallback_harness_agent_client

        keep = state.MODEL if is_harness_agent_model(state.MODEL) else HARNESS_AGENT_DEFAULT_MODEL
        state.client = _fallback_harness_agent_client(preferred_model=keep)
        save_last_model()
        return f"switched to Harness Agent (free) · {state.MODEL}"

    if not in_use:
        return f"{label} models are now in /model"
    try:
        if provider == PROVIDER_OPENCODE:
            state.client = _build_opencode_client()
        elif provider == PROVIDER_OPENCODE_ZEN:
            state.client = _build_opencode_zen_client_for_model(
                state.MODEL, source=PROVIDER_OPENCODE_ZEN,
            )
        elif is_catalog_provider(provider):
            from ..auth.client import _build_catalog_client

            state.client = _build_catalog_client(provider)
        else:  # OpenRouter, or Anthropic on its API key
            state.client = _build_client_from_mode(state.auth_mode, interactive=False)
    except Exception as e:
        return f"saved, but reconnecting failed: {e}"
    return f"now in use for {label}"


def _prompt_provider_key_if_needed(
    target: str, prev_provider: str, *, skip_key_prompt: bool = False,
) -> bool:
    """Prompt for a missing API key when switching providers. Returns False on cancel."""
    try:
        if target == PROVIDER_OPENROUTER:
            if not os.getenv("OPENROUTER_API_KEY") and not OPENROUTER_KEY_FILE.exists():
                prompt_for_openrouter_key()
        elif target == PROVIDER_OPENCODE:
            if not os.getenv("OPENCODE_API_KEY") and not OPENCODE_KEY_FILE.exists():
                prompt_for_opencode_key()
        elif target == PROVIDER_OPENCODE_ZEN:
            if not skip_key_prompt and not has_opencode_zen_key():
                prompt_for_opencode_zen_key()
        elif is_catalog_provider(target):
            from ..auth.catalog_keys import has_key
            from ..auth.client import prompt_for_catalog_key
            if not has_key(target) and not prompt_for_catalog_key(target):
                raise EOFError
    except (EOFError, KeyboardInterrupt):
        console.print("[dim]provider switch cancelled[/]")
        _revert_provider_switch(prev_provider)
        return False
    except SystemExit:
        console.print("[red]invalid key — provider switch cancelled[/]")
        _revert_provider_switch(prev_provider)
        return False
    return True


def _catalog_provider_known(provider: str) -> bool:
    try:
        from ..auth import models_dev

        return models_dev.get_provider(provider) is not None
    except Exception:
        return False


def _usable_anthropic_auth_mode(preferred: str = "") -> str | None:
    """Anthropic auth mode whose credential exists right now, or None.

    Tries ``preferred``, then the current mode, then API key, then OAuth.
    """
    available = {
        AUTH_API_KEY: _has_anthropic_api_key(),
        AUTH_OAUTH: load_oauth_tokens() is not None,
    }
    for mode in (preferred, state.auth_mode, AUTH_API_KEY, AUTH_OAUTH):
        if available.get(mode):
            return mode
    return None


def _handle_provider(arg: str, *, skip_key_prompt: bool = False, auth_mode: str = ""):
    """/provider [anthropic|openrouter|opencode] — switch provider mid-session.

    ``auth_mode`` picks the Anthropic credential (OAuth vs API key) when the
    caller knows it, e.g. a model chosen under "Anthropic Auth" in /model.
    """
    target = arg.strip().lower() if arg else ""
    if not target:
        console.print(Panel(
            f"current provider: [bold cyan]{PROVIDER_LABELS.get(state.provider, state.provider)}[/]\n\n"
            "  [cyan]1[/]  Anthropic          [dim](Claude models)[/]\n"
            "  [cyan]2[/]  OpenRouter         [dim](free & paid)[/]\n"
            "  [cyan]3[/]  OpenCode Go        [dim](GLM, Kimi, DeepSeek, MiMo, MiniMax, Qwen)[/]\n"
            "  [cyan]4[/]  OpenCode Zen       [dim](MiniMax, HY3, Nemotron)[/]\n"
            "  [dim]…or any provider id from models.dev, e.g.[/] [cyan]deepseek[/] [dim](/key lists them all)[/]\n\n"
            "usage: [dim]/provider <name>[/]",
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
        elif sel:                                      target = sel
        else: return
    builtin = (
        PROVIDER_ANTHROPIC, PROVIDER_OPENROUTER, PROVIDER_OPENCODE,
        PROVIDER_OPENCODE_ZEN, PROVIDER_OPENAI_CODEX,
    )
    if target not in builtin:
        # A provider from models.dev: "md:deepseek", or just "deepseek".
        from ..auth import models_dev

        target = models_dev.provider_id(target) or target
    if target not in builtin and not (is_catalog_provider(target) and _catalog_provider_known(target)):
        console.print(f"[red]unknown provider: {target}[/] [dim]— /key lists every provider[/]"); return
    if target == state.provider:
        console.print(f"[dim]already on {provider_label(target)}[/]"); return

    prev_provider = state.provider
    state.provider = target
    _secure_write(PROVIDER_FILE, target)

    # Switch to a sensible default model for the new provider.
    if target == PROVIDER_OPENROUTER:
        if "/" not in state.MODEL:
            from ..constants import openrouter_default_model
            state.MODEL = openrouter_default_model()
    elif target == PROVIDER_OPENCODE:
        state.MODEL = normalize_model_for_provider(state.MODEL, PROVIDER_OPENCODE)
        if not state.MODEL:
            console.print("[yellow]OpenCode Go's model list couldn't be loaded (models.dev is unreachable) "
                          "— check the connection, then run /model[/]")
    elif target == PROVIDER_OPENCODE_ZEN:
        state.MODEL = normalize_model_for_provider(state.MODEL, PROVIDER_OPENCODE_ZEN)
    elif target == PROVIDER_OPENAI_CODEX:
        state.MODEL = normalize_model_for_provider(state.MODEL, PROVIDER_OPENAI_CODEX)
        if not load_codex_oauth_tokens():
            console.print("[yellow]OpenAI Codex OAuth not configured — run /login first[/]")
            state.provider = prev_provider
            _secure_write(PROVIDER_FILE, prev_provider)
            return
        state.auth_mode = AUTH_OAUTH
        _secure_write(AUTH_MODE_FILE, AUTH_OAUTH)
    elif is_catalog_provider(target):
        state.MODEL = normalize_model_for_provider(state.MODEL, target)
    else:
        state.MODEL = normalize_model_for_provider(state.MODEL, PROVIDER_ANTHROPIC)
        # Use whichever Anthropic credential exists now — a stale auth_mode
        # (e.g. api_key right after an OAuth sign-in) would prompt for a key.
        mode = _usable_anthropic_auth_mode(auth_mode)
        if mode:
            state.auth_mode = mode
            _secure_write(AUTH_MODE_FILE, mode)

    if target in (PROVIDER_OPENROUTER, PROVIDER_OPENCODE, PROVIDER_OPENCODE_ZEN) or is_catalog_provider(target):
        if not _prompt_provider_key_if_needed(
            target, prev_provider, skip_key_prompt=skip_key_prompt,
        ):
            return
    if target == PROVIDER_OPENCODE_ZEN:
        state.harness_agent_free = skip_key_prompt

    try:
        if target == PROVIDER_OPENCODE:
            state.client = _build_opencode_client()
        elif target == PROVIDER_OPENCODE_ZEN:
            state.client = _build_opencode_zen_client_for_model(
                state.MODEL,
                source=PROVIDER_HARNESS_AGENT if state.harness_agent_free else PROVIDER_OPENCODE_ZEN,
            )
        elif target == PROVIDER_OPENAI_CODEX:
            from ..auth.client import _build_codex_client
            state.client = _build_codex_client()
            if state.client is None:
                raise RuntimeError("Codex OAuth client unavailable")
        elif is_catalog_provider(target):
            from ..auth.client import _build_catalog_client
            state.client = _build_catalog_client(target)
        else:
            state.client = _build_client_from_mode(
                PROVIDER_OPENROUTER if target == PROVIDER_OPENROUTER else state.auth_mode
            )
        if target != PROVIDER_OPENCODE_ZEN:
            state.harness_agent_free = False
        console.print(f"[green]✓ switched to[/] [bold cyan]{provider_label(target)}[/] "
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
