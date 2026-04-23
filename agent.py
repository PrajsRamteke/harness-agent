#!/usr/bin/env python3
"""
agent.py — Claude Code-style terminal agent
Default model: claude-sonnet-4-6
"""
import os, sys, json, subprocess, pathlib, shlex, time, secrets, hashlib, base64, webbrowser, urllib.request, urllib.parse, urllib.error, html, re, sqlite3
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from getpass import getpass
from typing import Dict, List, Any, Optional, Union

try:
    from anthropic import Anthropic, APIStatusError, APIConnectionError, RateLimitError
    from rich.console import Console
    from rich.panel import Panel
    from rich.markdown import Markdown
    from rich.table import Table
    from rich.syntax import Syntax
    from rich.live import Live
    from rich.spinner import Spinner
except ModuleNotFoundError:
    root = pathlib.Path(__file__).resolve().parent
    script = pathlib.Path(__file__).resolve()
    venv_python = root / ".venv" / "bin" / "python"
    print(
        "Your `python3` is the system (e.g. Homebrew) interpreter. "
        "`anthropic` and `rich` are installed only in this project's `.venv`, "
        "so that interpreter cannot import them.\n\n"
        "Run:\n\n"
        f"  {venv_python} {script}\n\n"
        "Or from the project folder:\n\n"
        f"  cd {root}\n"
        "  source .venv/bin/activate\n"
        f"  python {script.name}\n",
        file=sys.stderr,
    )
    raise SystemExit(1)

console = Console()
CWD = pathlib.Path.cwd()
CONFIG_DIR = pathlib.Path.home() / ".config" / "claude-agent"
KEY_FILE = CONFIG_DIR / "key"
OAUTH_FILE = CONFIG_DIR / "oauth.json"
AUTH_MODE_FILE = CONFIG_DIR / "auth_mode"
HIST_FILE = CONFIG_DIR / "history.json"
NOTES_FILE = CONFIG_DIR / "notes.md"
PIN_FILE = CONFIG_DIR / "pinned.txt"
ALIAS_FILE = CONFIG_DIR / "aliases.json"
SESSIONS_DB = CONFIG_DIR / "sessions.db"

# ── Anthropic OAuth (Claude Pro/Max subscription) ──
OAUTH_CLIENT_ID = "9d1c250a-e61b-44d9-88ed-5944d1962f5e"
OAUTH_AUTHORIZE_URL = "https://claude.ai/oauth/authorize"
OAUTH_TOKEN_URL = "https://console.anthropic.com/v1/oauth/token"
OAUTH_REDIRECT_URI = "https://console.anthropic.com/oauth/code/callback"
OAUTH_SCOPES = "org:create_api_key user:profile user:inference"
OAUTH_BETA_HEADER = "oauth-2025-04-20"
CLAUDE_CODE_IDENTITY = "You are Claude Code, Anthropic's official CLI for Claude."

auth_mode: str = "api_key"  # "api_key" or "oauth" — set by make_client()
MODEL = os.getenv("CLAUDE_MODEL", "claude-sonnet-4-6")
AVAILABLE_MODELS = [
    ("claude-haiku-4-6", "Haiku 4.6 — fastest, cheapest"),
    ("claude-sonnet-4-6", "Sonnet 4.6 — balanced"),
    ("claude-opus-4-6", "Opus 4.6 — high capability"),
    ("claude-opus-4-7", "Opus 4.7 — most capable"),
]
MAX_TOOL_OUTPUT = 15000
MAX_FILE_READ = 200_000

# Approx pricing per 1M tokens (USD) — used only for /cost estimates.
PRICING = {
    "claude-sonnet-4-6": (3.0, 15.0),
    "claude-opus-4-7":   (15.0, 75.0),
    "claude-haiku-4-5":  (1.0, 5.0),
}

# emoji icons per tool category — makes the event log skimmable
TOOL_ICONS = {
    "read_file": "📄", "write_file": "✍️ ", "edit_file": "✏️ ",
    "list_dir": "📁", "run_bash": "⚡", "search_code": "🔎",
    "glob_files": "🔭", "git_status": "🌿", "git_diff": "🌿",
    "git_log": "🌿",
    "launch_app": "🚀", "focus_app": "🎯", "quit_app": "💤",
    "list_apps": "📋", "frontmost_app": "👁️ ", "applescript": "🍎",
    "read_ui": "👀", "click_element": "🖱️ ", "wait": "⏳",
    "check_permissions": "🔐", "type_text": "⌨️ ", "key_press": "⌨️ ",
    "click_menu": "📜", "click_at": "🖱️ ",
    "clipboard_get": "📋", "clipboard_set": "📋",
    "open_url": "🌐", "notify": "🔔",
    "shortcut_run": "⚙️ ", "mac_control": "🎛️ ",
    "web_search": "🌐", "fetch_url": "📡", "verified_search": "🔬",
}


# ────────────────────────── Auth: API key + Anthropic OAuth ──────────────────────────
def _secure_write(path: pathlib.Path, data: str):
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    path.write_text(data)
    try: os.chmod(path, 0o600)
    except OSError: pass


def prompt_for_key(reason: str = "") -> str:
    if reason:
        console.print(f"[red]{reason}[/]")
    console.print(Panel(
        "[bold yellow]Anthropic API key needed[/]\n\n"
        "Get one at: https://console.anthropic.com/settings/keys\n"
        "Must start with [cyan]sk-ant-[/]\n\n"
        f"Saved to: [dim]{KEY_FILE}[/] (chmod 600)",
        title="Setup · API key", border_style="yellow"
    ))
    key = getpass("Paste sk-ant- key (hidden): ").strip()
    if not key.startswith("sk-ant-"):
        console.print("[red]Key must start with sk-ant-[/]"); sys.exit(1)
    _secure_write(KEY_FILE, key)
    console.print("[green]✓ API key saved[/]")
    return key


def load_key() -> str:
    if os.getenv("ANTHROPIC_API_KEY"):
        return os.environ["ANTHROPIC_API_KEY"]
    if KEY_FILE.exists():
        k = KEY_FILE.read_text().strip()
        if k.startswith("sk-ant-"):
            return k
    return prompt_for_key()


# ── PKCE helpers ──
def _b64url(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def _pkce_pair() -> tuple:
    verifier = _b64url(secrets.token_bytes(32))
    challenge = _b64url(hashlib.sha256(verifier.encode()).digest())
    return verifier, challenge


def _http_json(url: str, payload: dict, timeout: int = 30) -> tuple:
    """POST JSON, return (status, body_dict_or_text)."""
    body = json.dumps(payload).encode()
    req = urllib.request.Request(
        url, data=body, method="POST",
        headers={"Content-Type": "application/json", "Accept": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            raw = r.read().decode("utf-8", errors="replace")
            try: return r.status, json.loads(raw)
            except json.JSONDecodeError: return r.status, raw
    except urllib.error.HTTPError as e:
        raw = e.read().decode("utf-8", errors="replace")
        try: return e.code, json.loads(raw)
        except json.JSONDecodeError: return e.code, raw
    except urllib.error.URLError as e:
        return 0, f"network error: {e.reason}"


def load_oauth_tokens() -> Optional[dict]:
    if not OAUTH_FILE.exists(): return None
    try:
        data = json.loads(OAUTH_FILE.read_text())
        if not data.get("access_token") or not data.get("refresh_token"):
            return None
        return data
    except (json.JSONDecodeError, OSError):
        return None


def save_oauth_tokens(data: dict):
    _secure_write(OAUTH_FILE, json.dumps(data, indent=2))


def clear_oauth_tokens():
    OAUTH_FILE.unlink(missing_ok=True)


def oauth_login() -> Optional[dict]:
    """Run the PKCE authorize→paste→exchange flow. Returns token dict, or None if
    the user wants to go back / cancels / exchange fails (so the caller can offer
    a different auth mode instead of exiting)."""
    verifier, challenge = _pkce_pair()
    params = {
        "code": "true",
        "client_id": OAUTH_CLIENT_ID,
        "response_type": "code",
        "redirect_uri": OAUTH_REDIRECT_URI,
        "scope": OAUTH_SCOPES,
        "code_challenge": challenge,
        "code_challenge_method": "S256",
        "state": verifier,
    }
    url = OAUTH_AUTHORIZE_URL + "?" + urllib.parse.urlencode(params)
    console.print(Panel(
        "[bold]Log in with your Anthropic (Pro/Max) account[/]\n\n"
        "1. A browser window will open. Sign in and approve access.\n"
        "2. You'll land on a page showing a code like [cyan]abc123#xyz789[/].\n"
        "3. Copy the ENTIRE code (including the [cyan]#[/]) and paste it back here.\n\n"
        f"If the browser doesn't open, visit this URL manually:\n[dim]{url}[/]\n\n"
        "[dim]Type [cyan]b[/dim][dim] to go back to the auth method picker.[/]",
        title="🔐 Anthropic OAuth login", border_style="cyan",
    ))
    try: webbrowser.open(url)
    except Exception: pass

    try:
        pasted = console.input("Paste the code here (or 'b' to go back): ").strip()
    except (EOFError, KeyboardInterrupt):
        console.print("\n[yellow]login cancelled — returning to auth picker[/]")
        return None
    if pasted.lower() in ("b", "back"):
        return None
    if not pasted:
        console.print("[yellow]no code pasted — returning to auth picker[/]")
        return None

    # The callback page shows "<code>#<state>". Split on "#" if present.
    if "#" in pasted:
        code, returned_state = pasted.split("#", 1)
    else:
        code, returned_state = pasted, verifier  # tolerate either form
    code = code.strip(); returned_state = returned_state.strip()

    status, body = _http_json(OAUTH_TOKEN_URL, {
        "grant_type": "authorization_code",
        "code": code,
        "state": returned_state,
        "client_id": OAUTH_CLIENT_ID,
        "redirect_uri": OAUTH_REDIRECT_URI,
        "code_verifier": verifier,
    })
    if status != 200 or not isinstance(body, dict) or "access_token" not in body:
        console.print(f"[red]Token exchange failed (HTTP {status}): {body}[/]")
        console.print("[yellow]Returning to auth picker — you can try API key instead.[/]")
        return None

    expires_in = int(body.get("expires_in") or 3600)
    tokens = {
        "access_token": body["access_token"],
        "refresh_token": body.get("refresh_token", ""),
        "expires_at": int(time.time()) + expires_in,
        "scopes": body.get("scope", OAUTH_SCOPES).split() if isinstance(body.get("scope"), str) else body.get("scope", []),
    }
    save_oauth_tokens(tokens)
    console.print("[green]✓ Logged in with Anthropic[/]")
    return tokens


def oauth_refresh(tokens: dict) -> Optional[dict]:
    """Refresh access token using refresh_token. Returns new token dict or None on failure."""
    if not tokens.get("refresh_token"):
        return None
    status, body = _http_json(OAUTH_TOKEN_URL, {
        "grant_type": "refresh_token",
        "refresh_token": tokens["refresh_token"],
        "client_id": OAUTH_CLIENT_ID,
    })
    if status != 200 or not isinstance(body, dict) or "access_token" not in body:
        return None
    expires_in = int(body.get("expires_in") or 3600)
    new_tokens = {
        "access_token": body["access_token"],
        "refresh_token": body.get("refresh_token") or tokens["refresh_token"],
        "expires_at": int(time.time()) + expires_in,
        "scopes": tokens.get("scopes", []),
    }
    save_oauth_tokens(new_tokens)
    return new_tokens


def get_fresh_oauth_token() -> Optional[dict]:
    """Load tokens; refresh if within 60s of expiry. Returns None if unrecoverable."""
    tokens = load_oauth_tokens()
    if not tokens: return None
    if tokens.get("expires_at", 0) - time.time() < 60:
        refreshed = oauth_refresh(tokens)
        if not refreshed:
            console.print("[yellow]OAuth token refresh failed — please log in again.[/]")
            return None
        tokens = refreshed
    return tokens


def _build_client_from_mode(mode: str) -> Anthropic:
    global auth_mode
    if mode == "oauth":
        tokens = get_fresh_oauth_token()
        if not tokens:
            tokens = oauth_login()
        if not tokens:
            # User backed out or OAuth failed — fall back to the picker so they
            # can switch to API key without restarting the program.
            new_mode = _choose_auth_mode()
            auth_mode = new_mode
            _secure_write(AUTH_MODE_FILE, new_mode)
            return _build_client_from_mode(new_mode)
        return Anthropic(
            api_key=None,
            auth_token=tokens["access_token"],
            default_headers={"anthropic-beta": OAUTH_BETA_HEADER},
        )
    return Anthropic(api_key=load_key())


def _choose_auth_mode() -> str:
    """Prompt user to pick API key or OAuth login on first run."""
    console.print(Panel(
        "[bold]How would you like to authenticate?[/]\n\n"
        "  [cyan]1[/]  API key  [dim](pay-as-you-go, sk-ant-…)[/]\n"
        "  [cyan]2[/]  Log in with Anthropic  [dim](Claude Pro/Max subscription)[/]\n",
        title="🔐 Setup", border_style="cyan",
    ))
    while True:
        try:
            ch = console.input("choice [1/2]: ").strip()
        except (EOFError, KeyboardInterrupt):
            console.print("\n[yellow]cancelled[/]"); sys.exit(1)
        if ch in ("1", "key", "api", "api_key"): return "api_key"
        if ch in ("2", "oauth", "login"):        return "oauth"
        console.print("[red]enter 1 or 2[/]")


def make_client() -> Anthropic:
    """Resolve auth mode, build client, validate; handle 401 with refresh/re-auth."""
    global auth_mode

    # Priority: env var API key → stored auth mode → pick interactively
    if os.getenv("ANTHROPIC_API_KEY"):
        auth_mode = "api_key"
    elif AUTH_MODE_FILE.exists():
        stored = AUTH_MODE_FILE.read_text().strip()
        if stored in ("api_key", "oauth"):
            auth_mode = stored
            # If oauth was chosen but tokens are gone and no api key either, fall through to picker
            if auth_mode == "oauth" and not load_oauth_tokens():
                pass  # oauth_login() will be triggered below
            elif auth_mode == "api_key" and not KEY_FILE.exists() and not os.getenv("ANTHROPIC_API_KEY"):
                auth_mode = _choose_auth_mode()
        else:
            auth_mode = _choose_auth_mode()
    elif KEY_FILE.exists():
        auth_mode = "api_key"
    elif load_oauth_tokens():
        auth_mode = "oauth"
    else:
        auth_mode = _choose_auth_mode()

    _secure_write(AUTH_MODE_FILE, auth_mode)

    for attempt in range(3):
        try:
            c = _build_client_from_mode(auth_mode)
            c.models.list(limit=1)  # cheap validation
            return c
        except APIStatusError as e:
            if e.status_code == 401:
                if auth_mode == "oauth":
                    tokens = load_oauth_tokens()
                    if tokens and oauth_refresh(tokens):
                        console.print("[dim]refreshed OAuth token, retrying…[/]")
                        continue
                    console.print("[yellow]OAuth session invalid — re-login required.[/]")
                    clear_oauth_tokens()
                    oauth_login()
                    continue
                else:
                    KEY_FILE.unlink(missing_ok=True)
                    prompt_for_key(reason="Stored key rejected (401). Please re-enter.")
                    continue
            raise
        except APIConnectionError as e:
            console.print(f"[red]Network error: {e}[/]"); sys.exit(1)
    console.print("[red]Too many auth failures[/]"); sys.exit(1)


client = make_client()


# ────────────────────────── state ──────────────────────────
SYSTEM = f"""You are a Jarvis-style macOS agent running in {CWD}. You can control the whole Mac.

CAPABILITIES
- Files & shell: read_file, write_file, edit_file, list_dir, run_bash, search_code, glob_files, git_*
- Mac control: launch_app, focus_app, quit_app, list_apps, frontmost_app, applescript,
  read_ui, click_element, type_text, key_press, click_menu, click_at, wait,
  check_permissions, clipboard_get, clipboard_set, open_url, notify, shortcut_run, mac_control
- Internet (no browser needed): web_search (quick DuckDuckGo lookup), fetch_url (fetch any URL as plain text),
  verified_search (PREFERRED for facts — fetches 5-10 independent sites, scores trust 1-10, cross-checks claims, returns verified/contested breakdown)

INTERNET RULES
- For any factual question, news, health, science, or current events → always use verified_search, NOT web_search.
- Never trust a single website or blog. verified_search cross-checks 5-10 sources automatically.
- web_search is only for quick reference lookups where accuracy is not critical.

WORKFLOW FOR GUI TASKS (e.g. "send WhatsApp to Alice saying hi")
1. launch_app or focus_app to bring the target app forward.
2. Call read_ui to inspect the current screen as structured accessibility text
   (window titles, buttons, text fields, chat messages — everything visible to VoiceOver).
3. Decide next action from the UI tree. To click a thing, prefer click_element
   (find by visible text) over click_at. Use key_press for chords (cmd+f, return,
   arrows) and type_text for typing strings.
4. After EVERY action (click, keystroke, app switch) call `wait` (0.4–1.0s) then
   read_ui again to confirm the UI changed as expected. Never chain actions blindly.
5. Prefer keyboard shortcuts over clicks (cmd+f to search a chat, enter to send, etc.).
6. For apps with good AppleScript support (Messages, Mail, Safari, Music, Finder, Notes,
   Reminders, Calendar, System Events) use `applescript` directly — faster and more reliable.
7. WhatsApp has no AppleScript API — drive it via focus_app → read_ui → keyboard / click_element.
8. If read_ui returns "(empty UI tree)" or an ACCESSIBILITY DENIED error, call
   check_permissions and tell the user exactly what to enable in System Settings.

RULES
- Be concise. Don't narrate obvious steps. Report results, not intentions.
- Never do anything destructive (delete files, send money, post publicly) without confirming.
- When a task is done, stop calling tools and summarize.

PERSONALITY & TONE
- You are Jarvis — direct, calm, confident. Like a skilled engineer, not a customer service bot.
- NEVER say: "You're absolutely right", "Great question!", "Certainly!", "Of course!",
  "I apologize", "I'm sorry", "My bad", "I understand your frustration", or any sycophantic filler.
- NEVER use excessive emojis. One emoji max per response, only if it genuinely adds clarity.
- If you made an error: just fix it silently and state the correct answer. No self-flagellation.
- If the user is wrong: say so plainly and explain why. Don't soften facts to please them.
- Respond like a senior engineer pair-programming: terse, precise, no fluff.

NO HALLUCINATION — NON-NEGOTIABLE RULES
- NEVER invent or guess: version numbers, release dates, URLs, domain names, website names,
  quotes, changelogs, statistics, pricing, API docs, or any specific factual claim.
- NEVER write things like "v2.1.118", "April 23, 2026", "according to the official docs",
  "the changelog states", "as per codeude.com" — unless you fetched that page yourself this session.
- BEFORE stating ANY specific fact (number, date, name, version, URL): ask yourself —
  "Did I fetch this from a real source in this session using verified_search or fetch_url?"
  If NO → do not state it. Say "I don't know — want me to look it up?" and call verified_search.
- A confident-sounding wrong answer is worse than saying "I don't know".
- Uncertainty is honest. Fabrication is a failure. Never dress up a guess as a fact."""

backups: List[tuple] = []  # [(path, prev_content), ...] stack for /undo
messages: List[Dict] = []
think_mode = False
total_in = 0
total_out = 0
auto_approve = False  # if False, ask before run_bash/write_file/edit_file
session_start = time.time()
tool_calls_count = 0
last_assistant_text = ""
pinned_context = PIN_FILE.read_text() if PIN_FILE.exists() else ""
aliases: Dict[str, str] = (
    json.loads(ALIAS_FILE.read_text()) if ALIAS_FILE.exists() else {}
)


def build_system() -> Union[str, List[Dict]]:
    """System prompt + live date/time + any pinned user context.

    When authenticated via OAuth, Anthropic requires the FIRST system block to be
    exactly the Claude Code identity string — so we return a 2-block list in that
    case and keep our real instructions in the second block.
    """
    now = datetime.now()
    date_line = (
        f"CURRENT DATE & TIME: {now.strftime('%A, %B %d, %Y')} "
        f"at {now.strftime('%I:%M %p')} "
        f"(timezone: {datetime.now().astimezone().tzname()})\n"
        f"Never assume or guess the date — the above is the real current date injected at runtime."
    )
    body = SYSTEM + "\n\n" + date_line
    if pinned_context.strip():
        body += "\n\nPINNED CONTEXT (user-supplied, always remember):\n" + pinned_context.strip()
    if auth_mode == "oauth":
        return [
            {"type": "text", "text": CLAUDE_CODE_IDENTITY},
            {"type": "text", "text": body},
        ]
    return body


# ────────────────────────── tools ──────────────────────────
def _save_backup(p: pathlib.Path):
    if p.exists():
        backups.append((str(p), p.read_text(errors="ignore")))


def read_file(path: str, offset: int = 0, limit: int = 0) -> str:
    p = (CWD / path).resolve() if not os.path.isabs(path) else pathlib.Path(path)
    if not p.exists(): return f"ERROR: {path} not found"
    if p.is_dir(): return f"ERROR: {path} is a directory"
    txt = p.read_text(errors="ignore")
    if offset or limit:
        lines = txt.splitlines()
        end = offset + limit if limit else len(lines)
        return "\n".join(f"{i+1}\t{l}" for i, l in enumerate(lines[offset:end], start=offset))
    return txt[:MAX_FILE_READ]


def write_file(path: str, content: str) -> str:
    p = (CWD / path).resolve() if not os.path.isabs(path) else pathlib.Path(path)
    _save_backup(p)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(content)
    return f"WROTE {p} ({len(content)} bytes)"


def edit_file(path: str, old_str: str, new_str: str, replace_all: bool = False) -> str:
    p = (CWD / path).resolve() if not os.path.isabs(path) else pathlib.Path(path)
    if not p.exists(): return f"ERROR: {path} not found"
    txt = p.read_text(errors="ignore")
    n = txt.count(old_str)
    if n == 0: return "ERROR: old_str not found"
    if n > 1 and not replace_all:
        return f"ERROR: old_str matches {n} times; pass replace_all=true or add more context"
    _save_backup(p)
    new_txt = txt.replace(old_str, new_str) if replace_all else txt.replace(old_str, new_str, 1)
    p.write_text(new_txt)
    return f"EDITED {p} ({n} replacement{'s' if n>1 else ''})"


def list_dir(path: str = ".") -> str:
    p = (CWD / path).resolve() if not os.path.isabs(path) else pathlib.Path(path)
    if not p.exists(): return f"ERROR: {path} not found"
    items = sorted(p.iterdir(), key=lambda x: (not x.is_dir(), x.name.lower()))[:300]
    return "\n".join(f"{'d' if x.is_dir() else 'f'} {x.name}" for x in items)


def run_bash(cmd: str, timeout: int = 60) -> str:
    DANGEROUS = ["rm -rf /", "mkfs", ":(){:|:&};:", "dd if=/dev/zero"]
    if any(d in cmd for d in DANGEROUS): return "BLOCKED: dangerous command"
    if not auto_approve:
        console.print(f"[yellow]→ run:[/] [cyan]{cmd}[/]")
        ok = console.input("[dim]approve? [Y/n/a=always] [/]").strip().lower()
        if ok == "a":
            globals()["auto_approve"] = True
        elif ok == "n":
            return "USER DENIED"
    try:
        r = subprocess.run(cmd, shell=True, capture_output=True, text=True,
                           timeout=timeout, cwd=str(CWD))
        out = (r.stdout or "") + (f"\n[stderr]\n{r.stderr}" if r.stderr else "")
        return f"$ {cmd}\nexit={r.returncode}\n{out[-MAX_TOOL_OUTPUT:]}"
    except subprocess.TimeoutExpired:
        return f"TIMEOUT after {timeout}s"


def search_code(pattern: str, path: str = ".") -> str:
    has_rg = subprocess.run("which rg", shell=True, capture_output=True).returncode == 0
    cmd = (f"rg -n --max-count 50 {shlex.quote(pattern)} {shlex.quote(path)}"
           if has_rg else
           f"grep -rn --max-count=50 {shlex.quote(pattern)} {shlex.quote(path)}")
    return run_bash(cmd, 20)


def glob_files(pattern: str) -> str:
    matches = sorted(pathlib.Path(CWD).glob(pattern))[:200]
    return "\n".join(str(m.relative_to(CWD)) for m in matches) or "no matches"


def git_status(): return run_bash("git status -sb", 10)
def git_diff(path: str = ""): return run_bash(f"git diff {shlex.quote(path)}" if path else "git diff", 15)
def git_log(n: int = 10): return run_bash(f"git log --oneline -n {int(n)}", 10)


# ────────────────────────── macOS control ──────────────────────────
def _osa(script: str, timeout: int = 30, lang: str = "AppleScript") -> str:
    try:
        r = subprocess.run(
            ["osascript", "-l", lang, "-e", script],
            capture_output=True, text=True, timeout=timeout,
        )
        out = r.stdout.strip()
        if r.returncode != 0:
            return f"ERROR (exit {r.returncode}): {r.stderr.strip()}\n{out}"
        return out or "OK"
    except subprocess.TimeoutExpired:
        return f"TIMEOUT after {timeout}s"


def applescript(code: str, timeout: int = 60) -> str:
    """Run arbitrary AppleScript. Return the script result (or error)."""
    return _osa(code, timeout)[:MAX_TOOL_OUTPUT]


def launch_app(name: str) -> str:
    r = subprocess.run(["open", "-a", name], capture_output=True, text=True)
    if r.returncode != 0:
        return f"ERROR: {r.stderr.strip() or 'could not open ' + name}"
    # wait for the process to register with System Events, then bring to front
    for _ in range(20):
        time.sleep(0.2)
        probe = subprocess.run(
            ["osascript", "-e",
             f'tell application "System Events" to exists (process "{name}")'],
            capture_output=True, text=True, timeout=3,
        )
        if probe.stdout.strip() == "true":
            break
    subprocess.run(["osascript", "-e", f'tell application "{name}" to activate'],
                   capture_output=True, text=True, timeout=5)
    time.sleep(0.5)
    return f"launched and focused {name}"


def focus_app(name: str) -> str:
    return _osa(f'tell application "{name}" to activate')


def quit_app(name: str) -> str:
    return _osa(f'tell application "{name}" to quit')


def list_apps() -> str:
    return _osa('tell application "System Events" to get name of (every process whose background only is false)')


def frontmost_app() -> str:
    return _osa('tell application "System Events" to get name of first process whose frontmost is true')


_READ_UI_JXA = r"""
function run(argv) {
  const targetApp = argv[0] || "";
  const maxDepth = parseInt(argv[1] || "7", 10);
  const maxLines = parseInt(argv[2] || "400", 10);
  const se = Application("System Events");
  let appName = targetApp;
  if (!appName) {
    try { appName = se.processes.whose({frontmost: true})[0].name(); }
    catch(e) { return "ERROR: no frontmost app"; }
  }
  let proc;
  try { proc = se.processes.byName(appName); proc.name(); }
  catch(e) { return "ERROR: process '" + appName + "' not found: " + e.message; }

  const out = ["[UI of " + appName + "]"];
  const truncate = (s, n) => {
    s = String(s == null ? "" : s).replace(/\s+/g, " ").trim();
    return s.length > n ? s.slice(0, n) + "…" : s;
  };

  function walk(el, depth) {
    if (out.length >= maxLines) return;
    let children = [];
    try { children = el.uiElements(); } catch(e) {}
    for (const c of children) {
      if (out.length >= maxLines) return;
      let role = "?", nm = "", vl = "", ds = "", pos = "";
      try { role = c.role(); } catch(e) {}
      try { nm = c.name() || ""; } catch(e) {}
      try { const v = c.value(); if (v != null) vl = String(v); } catch(e) {}
      try { ds = c.description() || ""; } catch(e) {}
      try {
        const p = c.position(), s = c.size();
        if (p && s) pos = "@" + Math.round(p[0]+s[0]/2) + "," + Math.round(p[1]+s[1]/2);
      } catch(e) {}
      let line = "  ".repeat(depth) + "[" + role + "]";
      if (nm) line += ' name="' + truncate(nm, 80) + '"';
      if (vl && vl !== nm) line += ' value="' + truncate(vl, 120) + '"';
      if (ds && ds !== nm && ds !== vl) line += ' desc="' + truncate(ds, 80) + '"';
      if (pos) line += " " + pos;
      out.push(line);
      if (depth < maxDepth) walk(c, depth + 1);
    }
  }

  let wins = [];
  try { wins = proc.windows(); } catch(e) {}
  if (wins.length === 0) {
    out.push("(no windows — app may be launching or backgrounded)");
    try { walk(proc, 0); } catch(e) {}
  } else {
    for (let i = 0; i < wins.length; i++) {
      const w = wins[i];
      let title = ""; try { title = w.name() || ""; } catch(e) {}
      out.push('[Window ' + (i+1) + '] title="' + truncate(title, 100) + '"');
      walk(w, 1);
      if (out.length >= maxLines) { out.push("… [truncated at " + maxLines + " lines]"); break; }
    }
  }
  return out.join("\n");
}
"""


def read_ui(app: str = "", max_depth: int = 7, max_lines: int = 400, max_chars: int = 14000) -> str:
    """
    Read the accessibility UI tree of `app` (blank = frontmost). Returns a
    hierarchical text dump: role, name, value, description, and center-point
    coordinates for each element. No screenshots, no OCR.
    """
    try:
        r = subprocess.run(
            ["osascript", "-l", "JavaScript", "-e", _READ_UI_JXA,
             app or "", str(max_depth), str(max_lines)],
            capture_output=True, text=True, timeout=25,
        )
        if r.returncode != 0:
            return f"ERROR: {r.stderr.strip() or r.stdout.strip()}"
        out = r.stdout.rstrip()
        if len(out) > max_chars:
            out = out[:max_chars] + f"\n… [truncated, {len(out)} chars total]"
        return out or "(empty UI tree)"
    except subprocess.TimeoutExpired:
        return "TIMEOUT reading UI (app may be unresponsive or not accessibility-enabled)"


_FIND_CLICK_JXA = r"""
function run(argv) {
  const appName = argv[0];
  const query = (argv[1] || "").toLowerCase();
  const roleFilter = (argv[2] || "").toLowerCase();
  const nth = parseInt(argv[3] || "1", 10);
  const se = Application("System Events");
  let proc;
  try { proc = se.processes.byName(appName); proc.name(); }
  catch(e) { return "ERROR: process not found"; }

  const hits = [];
  function walk(el, depth) {
    if (depth > 10 || hits.length >= nth + 5) return;
    let children = [];
    try { children = el.uiElements(); } catch(e) { return; }
    for (const c of children) {
      let role = "", nm = "", vl = "", ds = "";
      try { role = (c.role() || "").toLowerCase(); } catch(e) {}
      try { nm = (c.name() || "").toLowerCase(); } catch(e) {}
      try { const v = c.value(); if (v != null) vl = String(v).toLowerCase(); } catch(e) {}
      try { ds = (c.description() || "").toLowerCase(); } catch(e) {}
      const hay = nm + "\n" + vl + "\n" + ds;
      const roleOk = !roleFilter || role.indexOf(roleFilter) >= 0;
      if (roleOk && query && hay.indexOf(query) >= 0) hits.push(c);
      walk(c, depth + 1);
    }
  }
  try {
    const wins = proc.windows();
    if (wins.length) for (const w of wins) walk(w, 0); else walk(proc, 0);
  } catch(e) { return "ERROR walking: " + e.message; }

  if (hits.length < nth) return "NOT_FOUND (" + hits.length + " matches)";
  const target = hits[nth - 1];
  try {
    const p = target.position(), s = target.size();
    const cx = Math.round(p[0] + s[0]/2), cy = Math.round(p[1] + s[1]/2);
    // prefer AXPress action when available (works even if off-screen)
    try { target.actions.byName("AXPress").perform(); return "PRESSED at " + cx + "," + cy; }
    catch(e) {
      se.click({at: [cx, cy]});
      return "CLICKED at " + cx + "," + cy;
    }
  } catch(e) { return "ERROR clicking: " + e.message; }
}
"""


def click_element(app: str, query: str, role: str = "", nth: int = 1) -> str:
    """
    Find a UI element in `app` whose name/value/description contains `query`
    (case-insensitive) and click it. Optional `role` filter (e.g. 'button',
    'row', 'textfield'). `nth` picks the nth match (1-based).
    """
    try:
        r = subprocess.run(
            ["osascript", "-l", "JavaScript", "-e", _FIND_CLICK_JXA,
             app, query, role, str(nth)],
            capture_output=True, text=True, timeout=15,
        )
        return (r.stdout or r.stderr).strip() or "OK"
    except subprocess.TimeoutExpired:
        return "TIMEOUT"


def wait(seconds: float = 0.8) -> str:
    """Sleep — useful after launching/clicking so the UI settles before read_ui."""
    time.sleep(max(0.0, min(float(seconds), 10.0)))
    return f"slept {seconds}s"


def check_permissions() -> str:
    """Verify Accessibility permission. Returns a diagnostic string."""
    probe = 'tell application "System Events" to get name of first process whose frontmost is true'
    r = subprocess.run(["osascript", "-e", probe], capture_output=True, text=True, timeout=5)
    if r.returncode == 0:
        return f"Accessibility OK. Frontmost app: {r.stdout.strip()}"
    return ("ACCESSIBILITY DENIED.\n"
            "Open System Settings → Privacy & Security → Accessibility, add & enable your "
            "Terminal app (Terminal.app / iTerm / the one you launched this agent from), then "
            "also enable it under 'Automation' if prompted. Error: " + r.stderr.strip())


def type_text(text: str) -> str:
    # escape backslashes and quotes for AppleScript string literal
    esc = text.replace("\\", "\\\\").replace('"', '\\"')
    return _osa(f'tell application "System Events" to keystroke "{esc}"')


_KEYCODES = {
    "return": 36, "enter": 36, "tab": 48, "space": 49, "delete": 51, "backspace": 51,
    "escape": 53, "esc": 53, "left": 123, "right": 124, "down": 125, "up": 126,
    "home": 115, "end": 119, "pageup": 116, "pagedown": 121,
    "f1": 122, "f2": 120, "f3": 99, "f4": 118, "f5": 96, "f6": 97, "f7": 98,
    "f8": 100, "f9": 101, "f10": 109, "f11": 103, "f12": 111,
}


def key_press(keys: str) -> str:
    """
    Press a key or chord. Examples: 'return', 'cmd+f', 'cmd+shift+t', 'down'.
    A single printable character (like 'a') will also work.
    """
    parts = [p.strip().lower() for p in keys.split("+")]
    mods = {"cmd": "command down", "command": "command down",
            "shift": "shift down", "opt": "option down", "option": "option down",
            "alt": "option down", "ctrl": "control down", "control": "control down"}
    mod_flags = [mods[p] for p in parts if p in mods]
    key = [p for p in parts if p not in mods]
    if len(key) != 1:
        return f"ERROR: bad key spec: {keys}"
    k = key[0]
    using = ""
    if mod_flags:
        using = " using {" + ", ".join(mod_flags) + "}"
    if k in _KEYCODES:
        return _osa(f'tell application "System Events" to key code {_KEYCODES[k]}{using}')
    # printable char
    esc = k.replace("\\", "\\\\").replace('"', '\\"')
    return _osa(f'tell application "System Events" to keystroke "{esc}"{using}')


def click_menu(app: str, path: list) -> str:
    """Click a menu item. path = ['File', 'New Window'] or ['Edit','Find','Find…']."""
    if not path:
        return "ERROR: path required"
    parts = " of ".join(f'menu item "{p}"' if i == 0
                       else (f'menu "{p}"' if i == len(path)-1 else f'menu item "{p}"')
                       for i, p in enumerate(reversed(path)))
    # simpler: use hierarchy: menu bar item 1 → menu 1 → menu item "x" → (optional submenu)
    top = path[0]
    if len(path) == 1:
        script = f'''
        tell application "System Events" to tell process "{app}"
            click menu bar item "{top}" of menu bar 1
        end tell
        '''
        return _osa(script)
    # build nested: click menu item "last" of menu "second-to-last" of menu item "..." ... of menu "top" of menu bar item "top" of menu bar 1
    ref = f'menu item "{path[-1]}"'
    # walk from path[-2] down to path[1], each item is a submenu-holding menu item + its menu
    cur = ref
    for mid in reversed(path[1:-1]):
        cur = f'{cur} of menu "{mid}" of menu item "{mid}"'
    cur = f'{cur} of menu "{path[0]}" of menu bar item "{path[0]}" of menu bar 1'
    script = f'''
    tell application "System Events" to tell process "{app}"
        click {cur}
    end tell
    '''
    return _osa(script)


def click_at(x: int, y: int) -> str:
    return _osa(f'tell application "System Events" to click at {{{int(x)}, {int(y)}}}')


def clipboard_get() -> str:
    r = subprocess.run(["pbpaste"], capture_output=True, text=True, timeout=5)
    return r.stdout[:MAX_TOOL_OUTPUT]


def clipboard_set(text: str) -> str:
    p = subprocess.Popen(["pbcopy"], stdin=subprocess.PIPE)
    p.communicate(text.encode("utf-8"))
    return f"clipboard set ({len(text)} chars)"


def open_url(url: str) -> str:
    r = subprocess.run(["open", url], capture_output=True, text=True)
    return "opened" if r.returncode == 0 else f"ERROR: {r.stderr.strip()}"


def notify(title: str, message: str = "") -> str:
    t = title.replace('"', '\\"')
    m = message.replace('"', '\\"')
    return _osa(f'display notification "{m}" with title "{t}"')


def shortcut_run(name: str, input_text: str = "") -> str:
    """Run an Apple Shortcut by name. Optional text input piped in."""
    cmd = ["shortcuts", "run", name]
    try:
        r = subprocess.run(cmd, input=input_text, capture_output=True, text=True, timeout=120)
        out = r.stdout + (f"\n[stderr]\n{r.stderr}" if r.stderr else "")
        return out[:MAX_TOOL_OUTPUT] or "OK"
    except Exception as e:
        return f"ERROR: {e}"


def mac_control(action: str, value: str = "") -> str:
    """
    System controls. action ∈ {volume, mute, unmute, brightness, battery, wifi_on,
    wifi_off, sleep, lock, dark_mode, light_mode, toggle_dark}. value optional.
    """
    a = action.lower()
    if a == "volume":
        return _osa(f'set volume output volume {int(value) if value else 50}')
    if a == "mute":
        return _osa('set volume with output muted')
    if a == "unmute":
        return _osa('set volume without output muted')
    if a == "battery":
        return run_bash("pmset -g batt | tail -1", 5)
    if a == "wifi_on":
        return run_bash("networksetup -setairportpower en0 on", 5)
    if a == "wifi_off":
        return run_bash("networksetup -setairportpower en0 off", 5)
    if a == "sleep":
        return run_bash("pmset sleepnow", 5)
    if a == "lock":
        return _osa('tell application "System Events" to keystroke "q" using {control down, command down}')
    if a == "dark_mode":
        return _osa('tell application "System Events" to tell appearance preferences to set dark mode to true')
    if a == "light_mode":
        return _osa('tell application "System Events" to tell appearance preferences to set dark mode to false')
    if a == "toggle_dark":
        return _osa('tell application "System Events" to tell appearance preferences to set dark mode to not dark mode')
    if a == "brightness":
        # no native AppleScript for brightness; best-effort via key codes F1/F2
        return "brightness not directly scriptable; use key_press 'f1'/'f2' if function keys mapped"
    return f"ERROR: unknown action {action}"


# ────────────────────────── internet tools ──────────────────────────
def _strip_html(raw: str) -> str:
    """Very lightweight HTML → plain-text: strip tags, decode entities."""
    # remove <script>, <style> blocks entirely
    raw = re.sub(r"<(script|style)[^>]*>.*?</\1>", " ", raw, flags=re.S | re.I)
    # remove all other tags
    raw = re.sub(r"<[^>]+>", " ", raw)
    # collapse whitespace
    raw = re.sub(r"[ \t]+", " ", raw)
    raw = re.sub(r"\n{3,}", "\n\n", raw)
    return html.unescape(raw).strip()


def web_search(query: str, max_results: int = 8) -> str:
    """
    Search the web using DuckDuckGo's free JSON API (no key required).
    Returns a ranked list of results: title, URL, and snippet.
    Also tries the HTML endpoint for extra organic results when the JSON
    Instant Answer doesn't return enough hits.
    """
    results: list[str] = []

    # ── 1. DuckDuckGo Instant Answer API (JSON) ──────────────────────
    try:
        ia_url = (
            "https://api.duckduckgo.com/?q="
            + urllib.parse.quote_plus(query)
            + "&format=json&no_redirect=1&no_html=1&skip_disambig=1"
        )
        req = urllib.request.Request(
            ia_url,
            headers={"User-Agent": "HarnessAgent/1.0 (macOS; python)"},
        )
        with urllib.request.urlopen(req, timeout=10) as r:
            data = json.loads(r.read().decode("utf-8", errors="replace"))

        # Abstract (Wikipedia-style summary)
        abstract = data.get("AbstractText", "").strip()
        abstract_url = data.get("AbstractURL", "").strip()
        if abstract:
            results.append(f"[Abstract]\n{abstract}\n🔗 {abstract_url}")

        # RelatedTopics
        for t in data.get("RelatedTopics", [])[:max_results]:
            if isinstance(t, dict) and t.get("Text") and t.get("FirstURL"):
                results.append(f"• {t['Text']}\n  🔗 {t['FirstURL']}")
            elif isinstance(t, dict) and t.get("Topics"):
                for sub in t["Topics"][:3]:
                    if sub.get("Text") and sub.get("FirstURL"):
                        results.append(f"• {sub['Text']}\n  🔗 {sub['FirstURL']}")

        # Definition
        defn = data.get("Definition", "").strip()
        defn_url = data.get("DefinitionURL", "").strip()
        if defn:
            results.append(f"[Definition]\n{defn}\n🔗 {defn_url}")

        # Answer (e.g. calculator / conversion results)
        answer = data.get("Answer", "").strip()
        if answer:
            results.insert(0, f"[Direct Answer] {answer}")

    except Exception as e:
        results.append(f"[DDG JSON error: {e}]")

    # ── 2. DDG HTML scrape for organic links (fallback / supplement) ──
    if len(results) < 3:
        try:
            html_url = (
                "https://html.duckduckgo.com/html/?q="
                + urllib.parse.quote_plus(query)
            )
            req2 = urllib.request.Request(
                html_url,
                headers={
                    "User-Agent": (
                        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                        "AppleWebKit/537.36 (KHTML, like Gecko) "
                        "Chrome/124.0 Safari/537.36"
                    )
                },
            )
            with urllib.request.urlopen(req2, timeout=12) as r:
                raw_html = r.read().decode("utf-8", errors="replace")

            # Extract result blocks: <a class="result__a" href="...">title</a>
            link_re = re.compile(
                r'<a[^>]+class="result__a"[^>]+href="([^"]+)"[^>]*>(.*?)</a>',
                re.S | re.I,
            )
            snip_re = re.compile(
                r'<a[^>]+class="result__snippet"[^>]*>(.*?)</a>', re.S | re.I
            )
            links = link_re.findall(raw_html)
            snips = [html.unescape(re.sub(r"<[^>]+>", "", s)) for s in snip_re.findall(raw_html)]

            for i, (href, title) in enumerate(links[:max_results]):
                # DDG HTML wraps URLs in redirects; try to decode uddg= param
                try:
                    qs = urllib.parse.parse_qs(urllib.parse.urlparse(href).query)
                    href = qs.get("uddg", [href])[0]
                except Exception:
                    pass
                clean_title = html.unescape(re.sub(r"<[^>]+>", "", title)).strip()
                snip = snips[i].strip() if i < len(snips) else ""
                entry = f"• {clean_title}\n  🔗 {urllib.parse.unquote(href)}"
                if snip:
                    entry += f"\n  {snip}"
                results.append(entry)

        except Exception as e:
            results.append(f"[DDG HTML error: {e}]")

    if not results:
        return f'No results found for "{query}".'

    header = f'🔍 Web search: "{query}" — {len(results)} result(s)\n' + "─" * 60
    return (header + "\n\n" + "\n\n".join(results))[:MAX_TOOL_OUTPUT]


def fetch_url(url: str, raw: bool = False) -> str:
    """
    Fetch a URL and return its content as plain text (HTML stripped by default).
    Set raw=True to get the raw response body (HTML/JSON/etc.).
    Follows redirects automatically. Respects MAX_TOOL_OUTPUT.
    """
    try:
        req = urllib.request.Request(
            url,
            headers={
                "User-Agent": (
                    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/124.0 Safari/537.36"
                ),
                "Accept": "text/html,application/xhtml+xml,application/json,*/*;q=0.9",
                "Accept-Language": "en-US,en;q=0.9",
            },
        )
        with urllib.request.urlopen(req, timeout=15) as r:
            content_type = r.headers.get("Content-Type", "")
            body = r.read().decode("utf-8", errors="replace")
            final_url = r.url

        if raw or "json" in content_type or not (
            "html" in content_type or body.lstrip().startswith("<")
        ):
            result = body
        else:
            result = _strip_html(body)

        header = f"📄 {final_url}\n[{len(result)} chars]\n" + "─" * 60 + "\n"
        return (header + result)[:MAX_TOOL_OUTPUT]

    except urllib.error.HTTPError as e:
        return f"HTTP ERROR {e.code}: {e.reason} — {url}"
    except urllib.error.URLError as e:
        return f"URL ERROR: {e.reason} — {url}"
    except Exception as e:
        return f"ERROR fetching {url}: {type(e).__name__}: {e}"


# ── Credibility tiers: trusted domains score higher ──────────────────
_TRUSTED_DOMAINS: dict[str, int] = {
    # encyclopaedias / reference
    "wikipedia.org": 10, "britannica.com": 10, "scholarpedia.org": 9,
    # science & academia
    "nature.com": 10, "science.org": 10, "pubmed.ncbi.nlm.nih.gov": 10,
    "ncbi.nlm.nih.gov": 10, "scholar.google.com": 9, "arxiv.org": 9,
    "researchgate.net": 8, "jstor.org": 9, "ieee.org": 9, "acm.org": 9,
    # government / official
    "gov": 9, "edu": 9, "who.int": 10, "cdc.gov": 10, "nih.gov": 10,
    "fda.gov": 10, "europa.eu": 9,
    # reputable news
    "bbc.com": 9, "bbc.co.uk": 9, "reuters.com": 9, "apnews.com": 9,
    "theguardian.com": 8, "nytimes.com": 8, "washingtonpost.com": 8,
    "economist.com": 8, "ft.com": 8, "bloomberg.com": 8, "wsj.com": 8,
    "npr.org": 8, "pbs.org": 8, "theatlantic.com": 7,
    # tech / official docs
    "docs.python.org": 10, "developer.mozilla.org": 10, "mdn.io": 10,
    "stackoverflow.com": 8, "github.com": 7, "developer.apple.com": 9,
    "docs.microsoft.com": 9, "learn.microsoft.com": 9,
    "cloud.google.com": 9, "aws.amazon.com": 9,
}
_UNTRUSTED_PATTERNS: list[str] = [
    "quora.com", "reddit.com", "yahoo.com/answers", "answers.com",
    "buzzfeed.com", "dailymail.co.uk", "thesun.co.uk", "nypost.com",
]

_BROWSER_UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/124.0 Safari/537.36"
)


def _domain_score(url: str) -> tuple[int, str]:
    """Return (trust_score 1-10, label) for a URL."""
    try:
        host = urllib.parse.urlparse(url).netloc.lower().lstrip("www.")
    except Exception:
        return 5, "unknown"
    for pat, score in _TRUSTED_DOMAINS.items():
        if host == pat or host.endswith("." + pat) or host.endswith(pat):
            return score, pat
    for bad in _UNTRUSTED_PATTERNS:
        if bad in host:
            return 3, f"low-trust ({bad})"
    tld = host.rsplit(".", 1)[-1] if "." in host else ""
    if tld in ("gov", "edu", "ac"):
        return 9, f"{tld} domain"
    return 5, "general"


def _fetch_snippet(url: str, max_chars: int = 3000) -> str:
    """Fetch a URL and return a short plain-text snippet, silently on error."""
    try:
        req = urllib.request.Request(
            url,
            headers={"User-Agent": _BROWSER_UA,
                     "Accept": "text/html,application/xhtml+xml,*/*;q=0.9",
                     "Accept-Language": "en-US,en;q=0.9"},
        )
        with urllib.request.urlopen(req, timeout=10) as r:
            raw = r.read(60_000).decode("utf-8", errors="replace")
        return _strip_html(raw)[:max_chars]
    except Exception as e:
        return f"[fetch error: {e}]"


def _ddg_organic_urls(query: str, want: int = 12) -> list[tuple[str, str, str]]:
    """
    Return up to `want` (url, title, snippet) tuples from DDG HTML scrape.
    """
    results: list[tuple[str, str, str]] = []
    try:
        html_url = (
            "https://html.duckduckgo.com/html/?q="
            + urllib.parse.quote_plus(query)
        )
        req = urllib.request.Request(
            html_url,
            headers={"User-Agent": _BROWSER_UA},
        )
        with urllib.request.urlopen(req, timeout=12) as r:
            raw_html = r.read().decode("utf-8", errors="replace")

        link_re = re.compile(
            r'<a[^>]+class="result__a"[^>]+href="([^"]+)"[^>]*>(.*?)</a>',
            re.S | re.I,
        )
        snip_re = re.compile(
            r'<a[^>]+class="result__snippet"[^>]*>(.*?)</a>', re.S | re.I
        )
        links = link_re.findall(raw_html)
        snips = [
            html.unescape(re.sub(r"<[^>]+>", "", s)).strip()
            for s in snip_re.findall(raw_html)
        ]
        for i, (href, title) in enumerate(links[:want]):
            try:
                qs = urllib.parse.parse_qs(urllib.parse.urlparse(href).query)
                href = qs.get("uddg", [href])[0]
            except Exception:
                pass
            href = urllib.parse.unquote(href)
            clean_title = html.unescape(re.sub(r"<[^>]+>", "", title)).strip()
            snip = snips[i] if i < len(snips) else ""
            results.append((href, clean_title, snip))
    except Exception:
        pass
    return results


def _extract_key_claims(text: str, max_sentences: int = 6) -> list[str]:
    """Pull the first N sentences from a text block as 'key claims'."""
    # split on sentence-ending punctuation
    sentences = re.split(r"(?<=[.!?])\s+", text.strip())
    out = []
    for s in sentences:
        s = s.strip()
        if len(s) > 30:
            out.append(s)
        if len(out) >= max_sentences:
            break
    return out


def _agreement_score(claim: str, corpus: list[str]) -> float:
    """
    Simple keyword-overlap agreement: what fraction of sources contain
    the key nouns/numbers from `claim`.  Returns 0.0–1.0.
    """
    if not corpus:
        return 0.0
    # extract words ≥4 chars that look like content words (not stopwords)
    stopwords = {
        "that", "this", "with", "from", "have", "will", "been", "they",
        "their", "there", "were", "also", "some", "when", "which", "what",
    }
    words = [
        w.lower()
        for w in re.findall(r"\b[a-zA-Z0-9]{4,}\b", claim)
        if w.lower() not in stopwords
    ]
    if not words:
        return 0.5
    hits = sum(
        1 for doc in corpus
        if sum(1 for w in words if w in doc.lower()) >= max(1, len(words) // 3)
    )
    return hits / len(corpus)


def verified_search(query: str, min_sources: int = 5, max_sources: int = 10) -> str:
    """
    Multi-source verified web search.

    Steps:
      1. Collect 12+ candidate URLs from DuckDuckGo (JSON + HTML).
      2. Deduplicate by domain so no single site dominates.
      3. Fetch page content from min_sources..max_sources URLs in parallel.
      4. Score each source by domain credibility (1-10).
      5. Extract key claims from the highest-trust sources.
      6. Cross-check each claim against ALL other sources (agreement score).
      7. Return a structured report: verified facts, contested points,
         source list with trust scores, and a confidence summary.
    """
    console.print(f"[dim cyan]🔍 verified_search: collecting sources for \"{query}\"…[/]")

    # ── Step 1: gather candidate URLs ────────────────────────────────
    candidates: list[tuple[str, str, str]] = []   # (url, title, snippet)

    # DDG JSON Instant Answer
    try:
        ia_url = (
            "https://api.duckduckgo.com/?q="
            + urllib.parse.quote_plus(query)
            + "&format=json&no_redirect=1&no_html=1&skip_disambig=1"
        )
        req = urllib.request.Request(
            ia_url, headers={"User-Agent": "HarnessAgent/1.0 (macOS; python)"}
        )
        with urllib.request.urlopen(req, timeout=10) as r:
            data = json.loads(r.read().decode("utf-8", errors="replace"))

        if data.get("AbstractURL") and data.get("AbstractText"):
            candidates.append((
                data["AbstractURL"],
                data.get("AbstractSource", "Abstract"),
                data["AbstractText"][:300],
            ))
        for t in data.get("RelatedTopics", [])[:8]:
            if isinstance(t, dict) and t.get("FirstURL") and t.get("Text"):
                candidates.append((t["FirstURL"], t.get("Text", "")[:80], t.get("Text", "")[:200]))
    except Exception:
        pass

    # DDG HTML organic results
    organic = _ddg_organic_urls(query, want=14)
    candidates.extend(organic)

    # ── Step 2: deduplicate by domain (max 2 per domain) ─────────────
    seen_domains: dict[str, int] = {}
    deduped: list[tuple[str, str, str]] = []
    for url, title, snip in candidates:
        try:
            domain = urllib.parse.urlparse(url).netloc.lower().lstrip("www.")
        except Exception:
            domain = url
        if seen_domains.get(domain, 0) < 2:
            deduped.append((url, title, snip))
            seen_domains[domain] = seen_domains.get(domain, 0) + 1

    # sort: higher-trust domains first
    deduped.sort(key=lambda x: _domain_score(x[0])[0], reverse=True)
    to_fetch = deduped[:max_sources]

    if not to_fetch:
        return f'❌ verified_search: could not find any sources for "{query}".'

    console.print(
        f"[dim]  → fetching content from {len(to_fetch)} sources in parallel…[/]"
    )

    # ── Step 3: fetch page content in parallel ────────────────────────
    source_data: list[dict] = []   # {url, title, snippet, content, trust, label}

    def _fetch_one(item: tuple[str, str, str]) -> dict:
        url, title, snip = item
        trust, label = _domain_score(url)
        content = _fetch_snippet(url, max_chars=2500)
        return {
            "url": url, "title": title, "snippet": snip,
            "content": content, "trust": trust, "label": label,
        }

    with ThreadPoolExecutor(max_workers=min(8, len(to_fetch))) as pool:
        futures = {pool.submit(_fetch_one, item): item for item in to_fetch}
        for fut in as_completed(futures):
            try:
                source_data.append(fut.result())
            except Exception:
                pass

    # filter out fetch errors entirely
    good_sources = [
        s for s in source_data
        if not s["content"].startswith("[fetch error")
        and len(s["content"]) > 100
    ]
    error_sources = [
        s for s in source_data
        if s["content"].startswith("[fetch error") or len(s["content"]) <= 100
    ]

    if not good_sources:
        return (
            f'⚠️  verified_search: found {len(to_fetch)} URLs but could not '
            f'fetch readable content from any of them for "{query}".\n'
            + "\n".join(f"  • {s['url']}" for s in to_fetch)
        )

    # sort good sources by trust descending
    good_sources.sort(key=lambda s: s["trust"], reverse=True)
    all_contents = [s["content"] for s in good_sources]

    # ── Step 4 & 5: extract key claims from top-trust sources ─────────
    top_sources = good_sources[:3]
    raw_claims: list[str] = []
    for src in top_sources:
        raw_claims.extend(_extract_key_claims(src["content"], max_sentences=5))
    # deduplicate very similar claims (length-based heuristic)
    seen_claim_words: set[str] = set()
    unique_claims: list[str] = []
    for c in raw_claims:
        sig = frozenset(c.lower().split())
        overlap = len(sig & seen_claim_words) / max(len(sig), 1)
        if overlap < 0.6:
            unique_claims.append(c)
            seen_claim_words |= sig
        if len(unique_claims) >= 10:
            break

    # ── Step 6: cross-check each claim ───────────────────────────────
    verified: list[tuple[str, float]] = []    # (claim, agreement_ratio)
    contested: list[tuple[str, float]] = []

    for claim in unique_claims:
        ratio = _agreement_score(claim, all_contents)
        if ratio >= 0.5:
            verified.append((claim, ratio))
        else:
            contested.append((claim, ratio))

    # overall confidence: weighted avg trust of good sources
    avg_trust = sum(s["trust"] for s in good_sources) / len(good_sources)
    verified_ratio = len(verified) / max(len(unique_claims), 1)
    confidence = "🟢 HIGH" if avg_trust >= 7 and verified_ratio >= 0.6 else \
                 "🟡 MEDIUM" if avg_trust >= 5 else "🔴 LOW"

    # ── Step 7: build the report ──────────────────────────────────────
    lines: list[str] = []
    lines.append(f'╔══ 🔍 VERIFIED SEARCH REPORT ══════════════════════════════╗')
    lines.append(f'  Query    : "{query}"')
    lines.append(f'  Sources  : {len(good_sources)} fetched / {len(to_fetch)} found'
                 + (f' ({len(error_sources)} unreachable)' if error_sources else ''))
    lines.append(f'  Avg trust: {avg_trust:.1f}/10   Confidence: {confidence}')
    lines.append(f'╚═══════════════════════════════════════════════════════════╝')
    lines.append("")

    if verified:
        lines.append("✅ VERIFIED FACTS  (agreed by ≥50% of sources)")
        lines.append("─" * 60)
        for claim, ratio in sorted(verified, key=lambda x: -x[1]):
            pct = int(ratio * 100)
            lines.append(f"  [{pct:3d}% agreement]  {claim}")
        lines.append("")

    if contested:
        lines.append("⚠️  CONTESTED / UNCERTAIN  (found in <50% of sources)")
        lines.append("─" * 60)
        for claim, ratio in sorted(contested, key=lambda x: -x[1]):
            pct = int(ratio * 100)
            lines.append(f"  [{pct:3d}% agreement]  {claim}")
        lines.append("")

    lines.append("📚 SOURCES  (sorted by trust score)")
    lines.append("─" * 60)
    for i, s in enumerate(good_sources, 1):
        bar = "█" * s["trust"] + "░" * (10 - s["trust"])
        lines.append(f"  {i:2d}. [{bar}] {s['trust']}/10  {s['label']}")
        lines.append(f"      {s['title'][:70]}")
        lines.append(f"      🔗 {s['url']}")
        if s["snippet"]:
            lines.append(f"      ↳ {s['snippet'][:120]}")
        lines.append("")

    if error_sources:
        lines.append("❌ UNREACHABLE SOURCES")
        lines.append("─" * 60)
        for s in error_sources:
            lines.append(f"  • {s['url']}")
        lines.append("")

    lines.append("─" * 60)
    lines.append(
        f"ℹ️  This answer was cross-verified across {len(good_sources)} independent "
        f"websites. Claims marked ✅ appeared in ≥50% of sources. "
        f"Always check primary sources for critical decisions."
    )

    report = "\n".join(lines)
    return report[:MAX_TOOL_OUTPUT]


TOOLS = [
    {"name":"read_file","description":"Read a file. Optional offset/limit for line ranges.",
     "input_schema":{"type":"object","properties":{
        "path":{"type":"string"},
        "offset":{"type":"integer","description":"0-indexed starting line"},
        "limit":{"type":"integer","description":"number of lines; 0 = all"}},
        "required":["path"]}},
    {"name":"write_file","description":"Create or overwrite a file",
     "input_schema":{"type":"object","properties":{
        "path":{"type":"string"},"content":{"type":"string"}},"required":["path","content"]}},
    {"name":"edit_file","description":"Replace old_str with new_str. old_str must be unique unless replace_all=true.",
     "input_schema":{"type":"object","properties":{
        "path":{"type":"string"},"old_str":{"type":"string"},
        "new_str":{"type":"string"},"replace_all":{"type":"boolean"}},
        "required":["path","old_str","new_str"]}},
    {"name":"list_dir","description":"List directory entries",
     "input_schema":{"type":"object","properties":{"path":{"type":"string"}}}},
    {"name":"run_bash","description":"Execute a shell command in the working directory",
     "input_schema":{"type":"object","properties":{
        "cmd":{"type":"string"},"timeout":{"type":"integer"}},"required":["cmd"]}},
    {"name":"search_code","description":"Regex search with ripgrep (or grep fallback)",
     "input_schema":{"type":"object","properties":{
        "pattern":{"type":"string"},"path":{"type":"string"}},"required":["pattern"]}},
    {"name":"glob_files","description":"Find files by glob pattern (e.g. '**/*.py')",
     "input_schema":{"type":"object","properties":{"pattern":{"type":"string"}},"required":["pattern"]}},
    {"name":"git_status","description":"git status","input_schema":{"type":"object","properties":{}}},
    {"name":"git_diff","description":"git diff","input_schema":{"type":"object","properties":{"path":{"type":"string"}}}},
    {"name":"git_log","description":"git log","input_schema":{"type":"object","properties":{"n":{"type":"integer"}}}},

    # ── macOS control ──
    {"name":"launch_app","description":"Launch a Mac app by name (e.g. 'WhatsApp', 'Safari').",
     "input_schema":{"type":"object","properties":{"name":{"type":"string"}},"required":["name"]}},
    {"name":"focus_app","description":"Bring a running app to front / activate it.",
     "input_schema":{"type":"object","properties":{"name":{"type":"string"}},"required":["name"]}},
    {"name":"quit_app","description":"Quit a Mac app.",
     "input_schema":{"type":"object","properties":{"name":{"type":"string"}},"required":["name"]}},
    {"name":"list_apps","description":"List visible running apps.",
     "input_schema":{"type":"object","properties":{}}},
    {"name":"frontmost_app","description":"Get the name of the frontmost app.",
     "input_schema":{"type":"object","properties":{}}},
    {"name":"applescript","description":"Run arbitrary AppleScript. Highest-leverage Mac automation. Use for Messages, Mail, Safari, Finder, Music, Notes, Reminders, Calendar, System Events.",
     "input_schema":{"type":"object","properties":{
        "code":{"type":"string"},"timeout":{"type":"integer"}},"required":["code"]}},
    {"name":"read_ui","description":"Read the accessibility UI tree of an app as text (no screenshot, no OCR). Hierarchical dump of every visible element: role, name, value, description, and center coordinates. Use this to SEE the screen before deciding what to click or type.",
     "input_schema":{"type":"object","properties":{
        "app":{"type":"string","description":"app name; blank = frontmost"},
        "max_depth":{"type":"integer"},
        "max_lines":{"type":"integer"},
        "max_chars":{"type":"integer"}}}},
    {"name":"click_element","description":"Find a UI element by text (matches name/value/description, case-insensitive) and click it. Much more reliable than click_at. Optional role filter ('button','row','textfield','link',…).",
     "input_schema":{"type":"object","properties":{
        "app":{"type":"string"},"query":{"type":"string"},
        "role":{"type":"string"},"nth":{"type":"integer"}},
        "required":["app","query"]}},
    {"name":"wait","description":"Sleep N seconds to let the UI settle after a click/keystroke before reading it again.",
     "input_schema":{"type":"object","properties":{"seconds":{"type":"number"}}}},
    {"name":"check_permissions","description":"Verify macOS Accessibility permission is granted to the terminal. Call this first if UI tools are failing.",
     "input_schema":{"type":"object","properties":{}}},
    {"name":"type_text","description":"Type a string into the frontmost app via keystroke.",
     "input_schema":{"type":"object","properties":{"text":{"type":"string"}},"required":["text"]}},
    {"name":"key_press","description":"Press a key or chord, e.g. 'return', 'cmd+f', 'cmd+shift+t', 'down'.",
     "input_schema":{"type":"object","properties":{"keys":{"type":"string"}},"required":["keys"]}},
    {"name":"click_menu","description":"Click a menu item by path, e.g. app='Safari', path=['File','New Window'].",
     "input_schema":{"type":"object","properties":{
        "app":{"type":"string"},
        "path":{"type":"array","items":{"type":"string"}}},"required":["app","path"]}},
    {"name":"click_at","description":"Click at absolute screen coordinates (last resort).",
     "input_schema":{"type":"object","properties":{
        "x":{"type":"integer"},"y":{"type":"integer"}},"required":["x","y"]}},
    {"name":"clipboard_get","description":"Return current clipboard text.",
     "input_schema":{"type":"object","properties":{}}},
    {"name":"clipboard_set","description":"Set clipboard text.",
     "input_schema":{"type":"object","properties":{"text":{"type":"string"}},"required":["text"]}},
    {"name":"open_url","description":"Open a URL or file path in the default handler (e.g. 'https://…', 'whatsapp://send?phone=…').",
     "input_schema":{"type":"object","properties":{"url":{"type":"string"}},"required":["url"]}},
    {"name":"notify","description":"Show a macOS notification banner.",
     "input_schema":{"type":"object","properties":{
        "title":{"type":"string"},"message":{"type":"string"}},"required":["title"]}},
    {"name":"shortcut_run","description":"Run an Apple Shortcut by name, optionally with text input.",
     "input_schema":{"type":"object","properties":{
        "name":{"type":"string"},"input_text":{"type":"string"}},"required":["name"]}},
    {"name":"mac_control","description":"System controls. action ∈ {volume, mute, unmute, battery, wifi_on, wifi_off, sleep, lock, dark_mode, light_mode, toggle_dark}.",
     "input_schema":{"type":"object","properties":{
        "action":{"type":"string"},"value":{"type":"string"}},"required":["action"]}},

    # ── internet ──
    {"name":"web_search","description":"Search the web using DuckDuckGo (no browser opened, no API key needed). Returns titles, URLs, and snippets for the top results. Use this to look up current information, news, docs, prices, weather, etc.",
     "input_schema":{"type":"object","properties":{
        "query":{"type":"string","description":"Search query string"},
        "max_results":{"type":"integer","description":"Max number of results to return (default 8)"}},"required":["query"]}},
    {"name":"fetch_url","description":"Fetch a URL and return its content as plain text (HTML is stripped). Use this to read web pages, docs, JSON APIs, etc. without opening any browser.",
     "input_schema":{"type":"object","properties":{
        "url":{"type":"string","description":"Full URL to fetch (http/https)"},
        "raw":{"type":"boolean","description":"If true, return raw response body (HTML/JSON) instead of stripped text"}},"required":["url"]}},
    {"name":"verified_search","description":(
        "Multi-source VERIFIED web search. Searches 5-10 independent websites, "
        "fetches their content in parallel, scores each by domain credibility (1-10), "
        "extracts key claims, cross-checks every claim across ALL sources, and returns "
        "a structured report with: ✅ verified facts (≥50% source agreement), "
        "⚠️ contested points, 📚 source list with trust scores, and an overall confidence "
        "level. Use this instead of web_search whenever accuracy matters — news, health, "
        "science, facts, prices, current events. Never trust a single source."
    ),
     "input_schema":{"type":"object","properties":{
        "query":{"type":"string","description":"What to research and verify"},
        "min_sources":{"type":"integer","description":"Minimum sources to fetch (default 5)"},
        "max_sources":{"type":"integer","description":"Maximum sources to fetch (default 10)"}},"required":["query"]}},
]
FUNC = {
    "read_file": read_file, "write_file": write_file, "edit_file": edit_file,
    "list_dir": list_dir, "run_bash": run_bash, "search_code": search_code,
    "glob_files": glob_files, "git_status": git_status, "git_diff": git_diff,
    "git_log": git_log,
    # mac
    "launch_app": launch_app, "focus_app": focus_app, "quit_app": quit_app,
    "list_apps": list_apps, "frontmost_app": frontmost_app,
    "applescript": applescript, "read_ui": read_ui,
    "click_element": click_element, "wait": wait,
    "check_permissions": check_permissions,
    "type_text": type_text, "key_press": key_press,
    "click_menu": click_menu, "click_at": click_at,
    "clipboard_get": clipboard_get, "clipboard_set": clipboard_set,
    "open_url": open_url, "notify": notify,
    "shortcut_run": shortcut_run, "mac_control": mac_control,
    # internet
    "web_search": web_search, "fetch_url": fetch_url,
    "verified_search": verified_search,
}


# ────────────────────────── API call loop ──────────────────────────
def call_claude_stream():
    global total_in, total_out, client
    kwargs: Dict[str, Any] = dict(
        model=MODEL, max_tokens=8192, system=build_system(),
        messages=messages, tools=TOOLS,
    )
    if think_mode:
        kwargs["thinking"] = {"type": "enabled", "budget_tokens": 4000}

    delays = [1, 3, 6]
    oauth_refreshed = False
    for attempt in range(len(delays) + 1):
        try:
            with client.messages.stream(**kwargs) as stream:
                final = stream.get_final_message()
            total_in += final.usage.input_tokens
            total_out += final.usage.output_tokens
            return final
        except RateLimitError:
            if attempt == len(delays): raise
            console.print(f"[yellow]rate-limited, retry in {delays[attempt]}s[/]")
            time.sleep(delays[attempt])
        except APIStatusError as e:
            if e.status_code == 401:
                if auth_mode == "oauth" and not oauth_refreshed:
                    tokens = load_oauth_tokens()
                    refreshed = oauth_refresh(tokens) if tokens else None
                    if refreshed:
                        console.print("[dim]OAuth token refreshed, retrying…[/]")
                        client = _build_client_from_mode("oauth")
                        oauth_refreshed = True
                        continue
                    console.print("[red]OAuth session expired. Run /login to re-authenticate.[/]")
                else:
                    console.print("[red]Auth failed mid-session. Run /key reset (or /login) and restart.[/]")
                raise SystemExit(1)
            if e.status_code >= 500 and attempt < len(delays):
                console.print(f"[yellow]server {e.status_code}, retry...[/]")
                time.sleep(delays[attempt]); continue
            raise


# ── Hallucination scrubber ───────────────────────────────────────────────────
# Patterns that strongly indicate fabricated facts stated as truth.
# These are checked sentence-by-sentence; flagged sentences are replaced with
# a warning so the user always knows when something wasn't actually verified.
_HALLUCINATION_PATTERNS: list[re.Pattern] = [
    # fake version numbers  e.g. "v2.1.118", "version 3.4.2", "v1.0"
    re.compile(r'\bv\d+\.\d+[\.\d]*\b', re.I),
    # "the official changelog / docs / release notes states / says / shows"
    re.compile(r'\b(official|changelog|release notes?|docs?|documentation)\b.{0,40}\b(states?|says?|shows?|confirms?|reads?|lists?)\b', re.I),
    # "according to <site>.com / <site>.org"
    re.compile(r'\baccording to\b.{0,60}\.(com|org|io|net|gov|edu)\b', re.I),
    # "the correct (date|version|release) is …"
    re.compile(r'\bthe correct\b.{0,40}\bis\b', re.I),
    # "I should have verified" / "I verified" / "I checked the source"
    re.compile(r'\bI (should have |have )?(verified|checked|confirmed|looked up)\b', re.I),
    # explicit fake self-correction: "not 2025" / "not 2024" etc.
    re.compile(r'\bnot 20\d\d\b', re.I),
]

_HALLUCINATION_WARNING = (
    "\n\n> ⚠️ **[Hallucination guard]** "
    "A sentence was removed because it contained unverified facts "
    "(version number, date, citation, or source) that were not fetched this session. "
    "Use `verified_search` to get real data.\n"
)


def _scrub_hallucinations(text: str) -> tuple[str, bool]:
    """
    Scan text line-by-line. Flag any line that matches a hallucination pattern.
    Flagged lines are dropped in-place; surrounding whitespace/structure is preserved.
    Returns (cleaned_text, was_flagged).
    """
    lines = text.splitlines(keepends=True)   # preserve \n so structure stays intact
    clean: list[str] = []
    flagged = False
    for line in lines:
        hit = any(p.search(line) for p in _HALLUCINATION_PATTERNS)
        if hit:
            flagged = True
            # replace with empty line to keep paragraph spacing intact
            clean.append("\n")
        else:
            clean.append(line)
    result = "".join(clean).strip()
    if flagged:
        result += _HALLUCINATION_WARNING
    return result, flagged


def render_assistant(resp) -> bool:
    """Print assistant content, execute any tool calls, return True if more turns needed."""
    global last_assistant_text, tool_calls_count

    # Build a friendly short model label: "Sonnet", "Opus", "Haiku" etc.
    _m = MODEL.lower()
    if "opus"   in _m: _model_label = "Opus"
    elif "sonnet" in _m: _model_label = "Sonnet"
    elif "haiku"  in _m: _model_label = "Haiku"
    else:                _model_label = MODEL

    tool_results = []
    for b in resp.content:
        # ── text reply ──────────────────────────────────────────────
        if b.type == "text":
            raw = b.text or ""
            # Must contain at least one non-whitespace character to be worth showing.
            # .strip() alone isn't enough — Markdown('\n\n') still renders a blank Panel.
            if not re.search(r"\S", raw):
                continue
            text, was_flagged = _scrub_hallucinations(raw.strip())
            if was_flagged:
                console.print("[dim red]⚠ hallucination guard triggered — sentence(s) removed[/]")
            if not re.search(r"\S", text):
                continue   # entire response was hallucinated — show nothing
            last_assistant_text = text
            console.print(Panel(
                Markdown(text),
                title=f"Jarvis [{_model_label}]",
                border_style="magenta",
                padding=(0, 1),
            ))

        # ── thinking block ───────────────────────────────────────────
        elif b.type == "thinking":
            thinking = b.thinking or ""
            if re.search(r"\S", thinking):
                console.print(Panel(
                    thinking.strip(),
                    title="thinking",
                    border_style="dim",
                    padding=(0, 1),
                ))

        # ── tool call ────────────────────────────────────────────────
        elif b.type == "tool_use":
            tool_calls_count += 1
            icon = TOOL_ICONS.get(b.name, "🔧")
            args_preview = json.dumps(b.input, ensure_ascii=False)[:120]
            console.print(f"{icon} [yellow]{b.name}[/] [dim]{args_preview}[/]")
            try:
                out = FUNC[b.name](**b.input)
            except Exception as e:
                out = f"ERROR: {type(e).__name__}: {e}"
            out_str = str(out)
            # only show preview panel if there is real non-whitespace content
            if re.search(r"\S", out_str):
                short = out_str.strip()[:400] + ("…" if len(out_str.strip()) > 400 else "")
                console.print(Panel(short, border_style="dim", padding=(0, 1)))
            tool_results.append({
                "type": "tool_result",
                "tool_use_id": b.id,
                "content": out_str[:MAX_TOOL_OUTPUT],
            })

    if tool_results:
        messages.append({"role": "user", "content": tool_results})
        return True
    return False


# ────────────────────────── commands ──────────────────────────
WELCOME_ART = r"""
  ██╗  ██╗ █████╗ ██████╗ ███╗   ██╗███████╗███████╗███████╗    █████╗  ██████╗ ███████╗███╗   ██╗████████╗
  ██║  ██║██╔══██╗██╔══██╗████╗  ██║██╔════╝██╔════╝██╔════╝   ██╔══██╗██╔════╝ ██╔════╝████╗  ██║╚══██╔══╝
  ███████║███████║██████╔╝██╔██╗ ██║█████╗  ███████╗███████╗   ███████║██║  ███╗█████╗  ██╔██╗ ██║   ██║
  ██╔══██║██╔══██║██╔══██╗██║╚██╗██║██╔══╝  ╚════██║╚════██║   ██╔══██║██║   ██║██╔══╝  ██║╚██╗██║   ██║
  ██║  ██║██║  ██║██║  ██║██║ ╚████║███████╗███████║███████║   ██║  ██║╚██████╔╝███████╗██║ ╚████║   ██║
  ╚═╝  ╚═╝╚═╝  ╚═╝╚═╝  ╚═╝╚═╝  ╚═══╝╚══════╝╚══════╝╚══════╝   ╚═╝  ╚═╝ ╚═════╝ ╚══════╝╚═╝  ╚═══╝   ╚═╝
"""


def welcome_banner():
    console.print(f"[bold magenta]{WELCOME_ART}[/]")
    console.print(Panel(
        "[bold]Jarvis-style macOS agent[/] — chat, code, and control your Mac.\n"
        "[dim]Type [cyan]/help[/] for commands • [cyan]/new[/] for a fresh chat • [cyan]/exit[/] to quit[/]",
        border_style="magenta", padding=(0, 2),
    ))


def cmd_help():
    t = Table(show_header=True, header_style="bold cyan", box=None, padding=(0, 2))
    t.add_column("command"); t.add_column("description")
    sections = [
        ("Session", [
            ("/help", "this menu"),
            ("/new", "start a fresh conversation (keeps pinned context)"),
            ("/reset", "clear conversation"),
            ("/retry", "re-send last user message"),
            ("/history", "show message summary"),
            ("/search <q>", "search conversation for a phrase"),
            ("/export <file>", "export conversation as markdown"),
            ("/save <file>", "save session JSON"),
            ("/load <file>", "load session JSON"),
            ("/session", "list persisted sessions & resume"),
            ("/session resume <id>", "resume a session by id"),
            ("/session new", "start a new persisted session"),
            ("/session delete <id>", "delete a stored session"),
            ("/clear", "clear the terminal screen"),
            ("/exit", "quit"),
        ]),
        ("Context", [
            ("/pin <text>", "pin context injected into every system prompt"),
            ("/unpin", "clear pinned context"),
            ("/note <text>", "append a note to your notes file"),
            ("/notes", "show your notes file"),
            ("/alias <n>=<cmd>", "create a shortcut alias (e.g. /alias gs=/git)"),
            ("/aliases", "list aliases"),
        ]),
        ("Files & Shell", [
            ("/ls [path]", "list directory"),
            ("/cd <dir>", "change working dir"),
            ("/pwd", "print working dir"),
            ("/find <pat>", "glob find files (e.g. **/*.py)"),
            ("/run <cmd>", "run a shell command (auto-approved)"),
            ("/undo", "restore last file edit"),
            ("/git", "git status"),
            ("/diff [path]", "git diff"),
        ]),
        ("Clipboard", [
            ("/copy", "copy last assistant response to clipboard"),
            ("/paste", "send clipboard contents as next message"),
        ]),
        ("Control", [
            ("/think", "toggle extended thinking"),
            ("/auto", "toggle auto-approve bash"),
            ("/multi", "enter a multiline message (end with ';;' line)"),
            ("/model <name>", "switch model"),
            ("/tokens", "usage so far"),
            ("/cost", "estimated USD cost of session"),
            ("/stats", "session stats (time, msgs, tools, tokens)"),
            ("/key reset", "delete stored API key"),
            ("/login", "log in with Anthropic (Pro/Max subscription)"),
            ("/logout", "clear OAuth tokens (fall back to API key)"),
            ("/auth", "show current auth mode + token info"),
        ]),
    ]
    for section, rows in sections:
        t.add_row(f"[bold yellow]── {section} ──[/]", "")
        for c, d in rows:
            t.add_row(f"[cyan]{c}[/]", d)
    console.print(Panel(t, title="📖 commands", border_style="blue"))


def header_panel():
    pinned_flag = "[yellow]pinned[/]" if pinned_context.strip() else "[dim]no pin[/]"
    flags = " • ".join([
        f"[bold magenta]{MODEL}[/]",
        f"🧠 {'[green]on[/]' if think_mode else '[dim]off[/]'}",
        f"⚡ {'[green]auto[/]' if auto_approve else '[dim]ask[/]'}",
        f"📌 {pinned_flag}",
        f"💬 {len(messages)}",
        f"📂 [dim]{CWD.name}[/]",
        f"🔐 [dim]{auth_mode}[/]",
    ])
    console.print(Panel(flags, border_style="green", padding=(0, 1)))


def fmt_duration(sec: float) -> str:
    sec = int(sec)
    h, rem = divmod(sec, 3600)
    m, s = divmod(rem, 60)
    return f"{h}h{m:02d}m{s:02d}s" if h else f"{m}m{s:02d}s"


def estimated_cost() -> float:
    p = PRICING.get(MODEL, (3.0, 15.0))
    return (total_in * p[0] + total_out * p[1]) / 1_000_000


def save_pin():
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    PIN_FILE.write_text(pinned_context)


def save_aliases():
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    ALIAS_FILE.write_text(json.dumps(aliases, indent=2))


def export_markdown(path: str) -> str:
    """Dump conversation as a human-readable markdown file."""
    lines = [f"# Claude session — {time.strftime('%Y-%m-%d %H:%M')}", ""]
    for m in messages:
        role = m["role"]
        c = m["content"]
        lines.append(f"## {role}")
        if isinstance(c, str):
            lines.append(c)
        else:
            for b in c:
                bd = b.model_dump() if hasattr(b, "model_dump") else b
                t = bd.get("type")
                if t == "text":
                    lines.append(bd.get("text", ""))
                elif t == "thinking":
                    lines.append(f"> *thinking:* {bd.get('thinking','')}")
                elif t == "tool_use":
                    lines.append(f"**🔧 tool:** `{bd.get('name')}` — `{json.dumps(bd.get('input',{}))[:400]}`")
                elif t == "tool_result":
                    body = bd.get("content", "")
                    if isinstance(body, list):
                        body = "".join(x.get("text", "") for x in body if isinstance(x, dict))
                    lines.append(f"```\n{str(body)[:2000]}\n```")
        lines.append("")
    pathlib.Path(path).write_text("\n".join(lines))
    return path


# ────────────────────────── Persistent sessions (SQLite) ──────────────────────────
current_session_id: Optional[int] = None


def db_conn() -> sqlite3.Connection:
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    c = sqlite3.connect(str(SESSIONS_DB))
    c.row_factory = sqlite3.Row
    return c


def db_init():
    with db_conn() as c:
        c.executescript("""
        CREATE TABLE IF NOT EXISTS sessions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            title TEXT,
            model TEXT,
            created_at REAL,
            updated_at REAL
        );
        CREATE TABLE IF NOT EXISTS messages (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            session_id INTEGER,
            idx INTEGER,
            role TEXT,
            content_json TEXT,
            created_at REAL,
            FOREIGN KEY (session_id) REFERENCES sessions(id) ON DELETE CASCADE
        );
        CREATE INDEX IF NOT EXISTS idx_msg_session ON messages(session_id, idx);
        """)


def db_create_session(model: str) -> int:
    now = time.time()
    with db_conn() as c:
        cur = c.execute(
            "INSERT INTO sessions (title, model, created_at, updated_at) VALUES (?,?,?,?)",
            (None, model, now, now))
        return cur.lastrowid


def db_append_message(session_id: int, idx: int, msg: Dict):
    serialized = _msg_to_json(msg)
    content = serialized["content"]
    content_json = json.dumps(content) if not isinstance(content, str) else json.dumps(content)
    with db_conn() as c:
        c.execute(
            "INSERT INTO messages (session_id, idx, role, content_json, created_at) VALUES (?,?,?,?,?)",
            (session_id, idx, msg["role"], content_json, time.time()))
        c.execute("UPDATE sessions SET updated_at=? WHERE id=?", (time.time(), session_id))


def db_replace_session_messages(session_id: int, msgs: List[Dict]):
    """Rewrite all messages for a session (used after /retry etc.)."""
    with db_conn() as c:
        c.execute("DELETE FROM messages WHERE session_id=?", (session_id,))
        now = time.time()
        for i, m in enumerate(msgs):
            s = _msg_to_json(m)
            content_json = json.dumps(s["content"])
            c.execute(
                "INSERT INTO messages (session_id, idx, role, content_json, created_at) VALUES (?,?,?,?,?)",
                (session_id, i, m["role"], content_json, now))
        c.execute("UPDATE sessions SET updated_at=? WHERE id=?", (now, session_id))


def db_set_title_if_empty(session_id: int, title: str):
    title = (title or "").strip().replace("\n", " ")[:80]
    if not title:
        return
    with db_conn() as c:
        row = c.execute("SELECT title FROM sessions WHERE id=?", (session_id,)).fetchone()
        if row and not row["title"]:
            c.execute("UPDATE sessions SET title=? WHERE id=?", (title, session_id))


def db_list_sessions(limit: int = 50) -> List[sqlite3.Row]:
    with db_conn() as c:
        return c.execute("""
            SELECT s.id, s.title, s.model, s.created_at, s.updated_at,
                   (SELECT COUNT(*) FROM messages m WHERE m.session_id=s.id) AS msg_count
            FROM sessions s
            WHERE EXISTS (SELECT 1 FROM messages m WHERE m.session_id=s.id)
            ORDER BY s.updated_at DESC LIMIT ?""", (limit,)).fetchall()


def db_load_session(session_id: int) -> Optional[List[Dict]]:
    with db_conn() as c:
        row = c.execute("SELECT id FROM sessions WHERE id=?", (session_id,)).fetchone()
        if not row: return None
        rows = c.execute(
            "SELECT role, content_json FROM messages WHERE session_id=? ORDER BY idx ASC",
            (session_id,)).fetchall()
    out = []
    for r in rows:
        content = json.loads(r["content_json"])
        out.append({"role": r["role"], "content": content})
    return out


def db_delete_session(session_id: int) -> bool:
    with db_conn() as c:
        cur = c.execute("DELETE FROM sessions WHERE id=?", (session_id,))
        c.execute("DELETE FROM messages WHERE session_id=?", (session_id,))
        return cur.rowcount > 0


def _fmt_ts(ts: float) -> str:
    try: return datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M")
    except Exception: return "-"


def cmd_session(arg: str):
    """Handle /session subcommands. Returns new session_id if switched, else None."""
    global messages, current_session_id, tool_calls_count
    parts = arg.split(maxsplit=1)
    sub = parts[0] if parts else "list"
    rest = parts[1] if len(parts) > 1 else ""

    if sub in ("list", "ls", ""):
        rows = db_list_sessions()
        if not rows:
            console.print("[dim]no saved sessions yet[/]"); return None
        t = Table(show_header=True, header_style="bold cyan", box=None, padding=(0, 2))
        t.add_column("#", style="dim"); t.add_column("id", style="cyan")
        t.add_column("title"); t.add_column("msgs", justify="right")
        t.add_column("model", style="magenta"); t.add_column("updated", style="dim")
        for i, r in enumerate(rows, 1):
            marker = " [green]●[/]" if r["id"] == current_session_id else ""
            title = r["title"] or "[dim](untitled)[/]"
            t.add_row(str(i), str(r["id"]) + marker, title,
                      str(r["msg_count"]), r["model"] or "-", _fmt_ts(r["updated_at"]))
        console.print(Panel(t, title="🗂  sessions", border_style="cyan"))
        sel = console.input("[cyan]resume # or id (enter to cancel, 'd <id>' to delete): [/]").strip()
        if not sel: return None
        if sel.startswith("d "):
            sid = sel[2:].strip()
            if sid.isdigit() and db_delete_session(int(sid)):
                console.print(f"[green]deleted session {sid}[/]")
            else:
                console.print("[red]not found[/]")
            return None
        target = None
        if sel.isdigit():
            i = int(sel)
            if 1 <= i <= len(rows): target = rows[i-1]["id"]
            else: target = i  # treat as raw id
        if target is None:
            console.print("[red]invalid selection[/]"); return None
        return _resume_session(target)

    if sub == "resume":
        if not rest.isdigit():
            console.print("usage: /session resume <id>"); return None
        return _resume_session(int(rest))

    if sub == "new":
        sid = db_create_session(MODEL)
        messages = []; tool_calls_count = 0
        current_session_id = sid
        console.print(f"[green]✨ new session #{sid}[/]")
        return sid

    if sub == "delete":
        if not rest.isdigit():
            console.print("usage: /session delete <id>"); return None
        if db_delete_session(int(rest)):
            console.print(f"[green]deleted session {rest}[/]")
        else:
            console.print("[red]not found[/]")
        return None

    if sub == "current":
        console.print(f"[cyan]current session: #{current_session_id}[/]")
        return None

    console.print("[red]unknown subcommand[/] — try: /session [list|resume <id>|new|delete <id>|current]")
    return None


def _resume_session(sid: int) -> Optional[int]:
    global messages, current_session_id, tool_calls_count
    loaded = db_load_session(sid)
    if loaded is None:
        console.print(f"[red]session {sid} not found[/]"); return None
    messages = loaded
    current_session_id = sid
    tool_calls_count = 0
    console.print(f"[green]▶ resumed session #{sid} ({len(messages)} messages)[/]")
    # render a brief tail so the user has context
    tail = messages[-6:]
    for m in tail:
        cn = m["content"]
        if isinstance(cn, str):
            preview = cn[:200]
        else:
            texts = []
            for b in cn:
                if isinstance(b, dict) and b.get("type") == "text":
                    texts.append(b.get("text", ""))
            preview = (" ".join(texts))[:200] or "[tool blocks]"
        console.print(f"  [dim]{m['role']}:[/] {preview}")
    return sid


def main():
    global think_mode, MODEL, messages, auto_approve, CWD, pinned_context, tool_calls_count, client, auth_mode, current_session_id
    welcome_banner()
    header_panel()
    db_init()
    current_session_id = db_create_session(MODEL)
    while True:
        try:
            now_str = datetime.now().strftime("%H:%M")
            inp = console.input(f"\n[dim]{now_str}[/] [bold green]Jarvis[/] [bold blue]›[/] ").strip()
        except (EOFError, KeyboardInterrupt):
            console.print("\n[magenta]bye 👋[/]"); break
        if not inp: continue

        # alias expansion: first token may resolve to a saved alias
        if inp.startswith("/"):
            head = inp.split(maxsplit=1)[0]
            if head[1:] in aliases:
                rest = inp[len(head):]
                inp = aliases[head[1:]] + rest

        if inp.startswith("/"):
            parts = inp.split(maxsplit=1)
            c = parts[0]
            arg = parts[1] if len(parts) > 1 else ""
            if c == "/exit": break
            elif c == "/help": cmd_help()
            elif c == "/ls": console.print(list_dir(arg or "."))
            elif c == "/cd":
                try:
                    os.chdir(arg or str(pathlib.Path.home()))
                    CWD = pathlib.Path.cwd()
                    console.print(f"[green]cwd → {CWD}[/]")
                except Exception as e: console.print(f"[red]{e}[/]")
            elif c == "/pwd": console.print(str(CWD))
            elif c in ("/reset", "/new"):
                messages = []; tool_calls_count = 0
                current_session_id = db_create_session(MODEL)
                if c == "/new":
                    console.clear(); welcome_banner(); header_panel()
                console.print(f"[green]✨ fresh conversation (session #{current_session_id})[/]")
            elif c == "/session" or c == "/sessions":
                cmd_session(arg)
            elif c == "/retry":
                # drop trailing assistant/tool-result blocks, re-send last user text
                last_user = None
                for i in range(len(messages) - 1, -1, -1):
                    m = messages[i]
                    if m["role"] == "user" and isinstance(m["content"], str):
                        last_user = m["content"]; messages = messages[:i]; break
                if last_user is None:
                    console.print("[red]no prior user message[/]"); continue
                inp = last_user
                if current_session_id:
                    db_replace_session_messages(current_session_id, messages)
                # fall through to send
            elif c == "/history":
                if not messages: console.print("[dim]empty[/]"); continue
                t = Table(show_header=True, header_style="bold cyan", box=None)
                t.add_column("#"); t.add_column("role"); t.add_column("preview")
                for i, m in enumerate(messages):
                    cn = m["content"]
                    if isinstance(cn, str):
                        preview = cn[:80]
                    else:
                        kinds = [getattr(b, "type", b.get("type") if isinstance(b, dict) else "?") for b in cn]
                        preview = ",".join(kinds)
                    t.add_row(str(i), m["role"], preview)
                console.print(t)
            elif c == "/search":
                if not arg: console.print("usage: /search <query>"); continue
                q = arg.lower(); hits = 0
                for i, m in enumerate(messages):
                    cn = m["content"]
                    text = cn if isinstance(cn, str) else json.dumps(
                        [b.model_dump() if hasattr(b, "model_dump") else b for b in cn])
                    if q in text.lower():
                        hits += 1
                        idx = text.lower().find(q)
                        snippet = text[max(0, idx-40):idx+80].replace("\n", " ")
                        console.print(f"[cyan]{i}[/] [{m['role']}] …{snippet}…")
                console.print(f"[dim]{hits} hits[/]")
            elif c == "/export":
                if not arg: arg = f"claude-session-{int(time.time())}.md"
                console.print(f"[green]exported → {export_markdown(arg)}[/]")
            elif c == "/clear":
                console.clear(); header_panel()
            elif c == "/undo":
                if not backups: console.print("nothing to undo"); continue
                path, prev = backups.pop()
                pathlib.Path(path).write_text(prev)
                console.print(f"[green]restored {path}[/]")
            elif c == "/save":
                if not arg: console.print("usage: /save <file>"); continue
                pathlib.Path(arg).write_text(json.dumps(
                    [_msg_to_json(m) for m in messages], indent=2))
                console.print(f"[green]saved → {arg}[/]")
            elif c == "/load":
                messages = json.loads(pathlib.Path(arg).read_text())
                current_session_id = db_create_session(MODEL)
                db_replace_session_messages(current_session_id, messages)
                console.print(f"[green]loaded {len(messages)} messages → session #{current_session_id}[/]")
            elif c == "/git": console.print(git_status())
            elif c == "/diff": console.print(git_diff(arg))
            elif c == "/find":
                if not arg: console.print("usage: /find <glob>"); continue
                console.print(glob_files(arg))
            elif c == "/run":
                if not arg: console.print("usage: /run <cmd>"); continue
                prev_auto = auto_approve; auto_approve = True
                console.print(run_bash(arg))
                auto_approve = prev_auto
            elif c == "/pin":
                if not arg: console.print("usage: /pin <text>"); continue
                pinned_context = (pinned_context + "\n" + arg).strip()
                save_pin()
                console.print(f"[green]📌 pinned ({len(pinned_context)} chars)[/]")
            elif c == "/unpin":
                pinned_context = ""; save_pin()
                console.print("[green]📌 cleared[/]")
            elif c == "/note":
                if not arg: console.print("usage: /note <text>"); continue
                CONFIG_DIR.mkdir(parents=True, exist_ok=True)
                with NOTES_FILE.open("a") as f:
                    f.write(f"- [{time.strftime('%Y-%m-%d %H:%M')}] {arg}\n")
                console.print(f"[green]📝 saved to {NOTES_FILE}[/]")
            elif c == "/notes":
                if NOTES_FILE.exists():
                    console.print(Panel(Markdown(NOTES_FILE.read_text()),
                                        title="📝 notes", border_style="yellow"))
                else:
                    console.print("[dim]no notes yet[/]")
            elif c == "/alias":
                if "=" not in arg:
                    console.print("usage: /alias <name>=<command>  e.g. /alias gs=/git"); continue
                k, v = arg.split("=", 1)
                aliases[k.strip().lstrip("/")] = v.strip()
                save_aliases()
                console.print(f"[green]alias {k.strip()} → {v.strip()}[/]")
            elif c == "/aliases":
                if not aliases: console.print("[dim]none[/]"); continue
                for k, v in aliases.items():
                    console.print(f"  [cyan]/{k}[/] → {v}")
            elif c == "/copy":
                if not last_assistant_text:
                    console.print("[dim]nothing to copy[/]"); continue
                clipboard_set(last_assistant_text)
                console.print(f"[green]copied {len(last_assistant_text)} chars[/]")
            elif c == "/paste":
                pasted = clipboard_get()
                if not pasted.strip():
                    console.print("[dim]clipboard empty[/]"); continue
                console.print(Panel(pasted[:400] + ("…" if len(pasted) > 400 else ""),
                                    title="📋 pasted", border_style="dim"))
                inp = pasted  # fall through to send as user message
            elif c == "/multi":
                console.print("[dim]enter multiline message, end with a single ';;' line:[/]")
                buf = []
                while True:
                    try: line = input()
                    except EOFError: break
                    if line.strip() == ";;": break
                    buf.append(line)
                inp = "\n".join(buf)
                if not inp.strip():
                    console.print("[dim]empty[/]"); continue
            elif c == "/think":
                think_mode = not think_mode; header_panel()
            elif c == "/auto":
                auto_approve = not auto_approve; header_panel()
            elif c == "/tokens":
                console.print(f"in:{total_in}  out:{total_out}  total:{total_in+total_out}")
            elif c == "/cost":
                console.print(f"[green]≈ ${estimated_cost():.4f}[/] "
                              f"[dim]({total_in} in + {total_out} out @ {MODEL})[/]")
            elif c == "/stats":
                t = Table(show_header=False, box=None, padding=(0, 2))
                t.add_row("⏱  elapsed", fmt_duration(time.time() - session_start))
                t.add_row("💬 messages", str(len(messages)))
                t.add_row("🔧 tool calls", str(tool_calls_count))
                t.add_row("⇅ tokens in/out", f"{total_in} / {total_out}")
                t.add_row("💰 est. cost", f"${estimated_cost():.4f}")
                t.add_row("🤖 model", MODEL)
                t.add_row("📂 cwd", str(CWD))
                console.print(Panel(t, title="📊 session stats", border_style="cyan"))
            elif c == "/model":
                if arg:
                    # allow numeric selection or explicit name
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
                        MODEL = chosen
                        console.print(f"[green]✓ model switched to[/] [cyan]{MODEL}[/]")
                        header_panel()
                    else:
                        console.print(f"[red]unknown model: {arg}[/]")
                else:
                    t = Table(show_header=True, box=None, pad_edge=False)
                    t.add_column("#", style="dim"); t.add_column("model", style="cyan"); t.add_column("description"); t.add_column("")
                    for i, (m, desc) in enumerate(AVAILABLE_MODELS, 1):
                        marker = "[green]● current[/]" if m == MODEL else ""
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
                            MODEL = chosen
                            console.print(f"[green]✓ model switched to[/] [cyan]{MODEL}[/]")
                            header_panel()
                        else:
                            console.print(f"[red]invalid selection: {sel}[/]")
            elif c == "/key" and arg == "reset":
                KEY_FILE.unlink(missing_ok=True)
                console.print("[green]key deleted — restart the agent[/]")
            elif c == "/login":
                clear_oauth_tokens()
                oauth_login()
                _secure_write(AUTH_MODE_FILE, "oauth")
                auth_mode = "oauth"
                try:
                    client = _build_client_from_mode("oauth")
                    client.models.list(limit=1)
                    console.print("[green]✓ OAuth client active[/]")
                except Exception as e:
                    console.print(f"[red]login validation failed: {e}[/]")
            elif c == "/logout":
                clear_oauth_tokens()
                if KEY_FILE.exists() or os.getenv("ANTHROPIC_API_KEY"):
                    _secure_write(AUTH_MODE_FILE, "api_key")
                    auth_mode = "api_key"
                    try:
                        client = _build_client_from_mode("api_key")
                        console.print("[green]logged out — falling back to API key[/]")
                    except Exception as e:
                        console.print(f"[red]{e}[/]")
                else:
                    console.print("[yellow]logged out — no API key configured, restart to set one[/]")
            elif c == "/auth":
                lines = [f"mode: [bold]{auth_mode}[/]"]
                if auth_mode == "oauth":
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
            else:
                console.print(f"[red]unknown: {c}[/]  (/help)")
                continue
            # commands that set `inp` for sending (retry/paste/multi) fall through below
            if c not in ("/retry", "/paste", "/multi"):
                continue

        user_msg = {"role": "user", "content": inp}
        messages.append(user_msg)
        if current_session_id:
            db_append_message(current_session_id, len(messages) - 1, user_msg)
            db_set_title_if_empty(current_session_id, inp)
        try:
            while True:
                with console.status("[dim]thinking…[/]", spinner="dots"):
                    resp = call_claude_stream()
                asst_msg = {"role": "assistant", "content": resp.content}
                messages.append(asst_msg)
                if current_session_id:
                    db_append_message(current_session_id, len(messages) - 1, asst_msg)
                more = render_assistant(resp)
                if resp.stop_reason == "end_turn" or not more:
                    break
                # tool results get appended inside render_assistant via messages — persist the latest
                if current_session_id and messages and messages[-1] is not asst_msg:
                    db_append_message(current_session_id, len(messages) - 1, messages[-1])
        except KeyboardInterrupt:
            console.print("\n[yellow]interrupted[/]")
        except Exception as e:
            console.print(f"[red]error: {type(e).__name__}: {e}[/]")


def _msg_to_json(m):
    """Make assistant content blocks JSON-serializable for /save."""
    c = m["content"]
    if isinstance(c, str): return m
    out = []
    for b in c:
        if hasattr(b, "model_dump"): out.append(b.model_dump())
        else: out.append(b)
    return {"role": m["role"], "content": out}


if __name__ == "__main__":
    main()
