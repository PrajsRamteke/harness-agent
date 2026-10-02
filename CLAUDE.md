# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Commands

```bash
# Set up development environment (run from repo root, one step at a time)
python3 -m venv .venv
source .venv/bin/activate    # Windows: .venv\Scripts\activate
pip install -e .             # installs deps + registers `jarvis`
jarvis --help                # verify CLI

# Run the TUI (default)
jarvis
# or:
python agent.py

# Run legacy Rich REPL
python agent.py --legacy

# Run unit tests
pip install pytest
python -m pytest tests/ -q
```

`tests/conftest.py` points `HOME` at a throwaway dir before anything imports `jarvis`, so a test run (even `!pytest` inside Jarvis) never touches the real `~/.config/harness-agent` — it used to rewrite `settings.json` / `provider` / `auth_mode`. Tests that only *read* local data use `JARVIS_REAL_HOME`; `JARVIS_TESTS_REAL_HOME=1` opts out.

`pip install -r requirements.txt` installs libraries only (no `jarvis` entry point). Prefer `pip install -e .` for local development. See `requirements.txt` header for the full verified sequence.

## Environment Variables

- `ANTHROPIC_API_KEY` — bypass auth prompt (pins provider to Anthropic)
- `OPENROUTER_API_KEY` — OpenRouter key (pins provider to OpenRouter if no Anthropic state exists)
- `OPENCODE_API_KEY` — OpenCode Go key
- `OPENCODE_ZEN_API_KEY` — OpenCode Zen key
- `HARNESS_PROVIDER` — pin provider explicitly: `anthropic`, `openrouter`, `opencode`, or `opencode_zen`
- `CLAUDE_MODEL` — override default model (default: `sonnet-4-6`)
- `HARNESS_MODEL_CATALOG_TTL` — seconds a fetched free-model catalog stays fresh (default: 21600 / 6h)
- `HARNESS_OPENROUTER_HIDE_NO_TOOLS` — set to `1` to drop free OpenRouter models that can't call tools (listed by default, labelled `no tool use`)
- `HARNESS_MODELS_DEV` — set to `0` to turn off the models.dev catalog (no fetch, no extra providers or models)
- `<PROVIDER>_API_KEY` as named by models.dev (`DEEPSEEK_API_KEY`, `GEMINI_API_KEY`, `GROQ_API_KEY` …) — connects that models.dev provider; `HARNESS_PROVIDER=md:<id>` pins one
- `HARNESS_MAX_PARALLEL_TOOLS` — max concurrent tool workers (default/cap: 64)
- `HARNESS_BUNDLE_MAX_CHARS` — max chars in resolve_context/read_bundle output (default: 120000)
- `HARNESS_BUNDLE_PER_FILE_MAX` — per-file cap inside a bundle (default: 20000)
- `HARNESS_BUNDLE_MODE` — default for resolve_context: `full` | `skeleton` | `manifest` (default: skeleton)
- `HARNESS_BUNDLE_MODE_READ` — default for read_bundle (default: full)
- `HARNESS_HTTP_READ_TIMEOUT` — streaming response timeout in seconds (default: 240 OpenRouter, 600 direct)
- `HARNESS_HTTP_CONNECT_TIMEOUT` — connection timeout (default: 30)
- `HARNESS_STREAM_REPLY` — set to `0` to disable live streaming of assistant text
- `HARNESS_MOUSE` — set to `0` to run the TUI without mouse capture (native terminal selection)
- `HARNESS_UPLOAD_MAX_MB` — largest file the web remote accepts as an attachment (default: 50)
- `HARNESS_UPLOAD_KEEP_DAYS` — days sent web attachments are kept under `~/.config/harness-agent/uploads/` (default: 30; never-sent ones go after a day)
- `HARNESS_CHROME` — Chrome/Chromium binary for `screenshot(url=…)`. Auto-detected otherwise, preferring `chrome-headless-shell` (PATH, Playwright or Puppeteer cache) over full Chrome: on macOS every full-Chrome headless run leaves a Chrome icon in the Dock's recent apps

## Architecture

`agent.py` is a thin entrypoint that routes to either `jarvis/tui/app.py` (Textual TUI, default) or `jarvis/main.py` (Rich REPL, `--legacy`).

### Package layout (`jarvis/`)

| Subpackage | Role |
|---|---|
| `auth/` | Auth orchestration: API key (`api_key.py`), OAuth PKCE (`oauth_flow.py`, `pkce.py`), OpenRouter (`openrouter.py`), OpenCode (`opencode.py`), unified client factory (`client.py`), models.dev catalog providers (`models_dev.py`, keys in `catalog_keys.py`) |
| `tools/` | All tool implementations + schema routing |
| `tools/router.py` | **Dynamic tool selection** — regex-scans recent messages to include only likely-needed tool groups; core always included, specialized groups (web, mac, ocr, memory, skills, mcp) conditionally added |
| `tools/schemas_core.py` / `schemas_mac.py` | JSON schema definitions for tool groups |
| `tools/mac/` | macOS control: app launch/focus/quit, AppleScript, JXA scripts, UI reading, clicks, keystrokes, clipboard |
| `tools/web/` | Web fetch + DuckDuckGo search with verified-source claim checking (`_claims.py`) |
| `repl/` | Stream handling (`stream.py`), response rendering (`render.py`), hallucination guard (`hallucination.py`), context trimming (`trim.py`) |
| `tui/` | Textual app (`app.py`), widget transcript (`transcript.py`), console shim (`console_shim.py`), markdown renderer (`md_render.py`), tool rows/icons (`tool_format.py`), clickable footer (`footer.py`), sticky prompt (`sticky_prompt.py`), extra key sequences (`terminal_keys.py`: ESC+CR → `alt+enter`, i.e. Shift+Enter from VS Code/Cursor keybindings → newline), sidebar, prompt history + paste chips, pickers/modals (shared chrome in `modal_chrome.py`) |
| `commands/` | Slash command handlers dispatched from `dispatch.py` (`agent.py` activates agents, `skill.py` lists/loads skills) |
| `storage/` | SQLite sessions (`sessions.py`), user memory (`memory.py`), **agents (`agents.py`)**, **skills (`skills.py`)**, **custom commands (`commands.py`)**, unified settings (`settings.py`), prefs (`prefs.py`) |
| `mcp/` | MCP server management: config (`config.py`, scope-aware writers), registry (`registry.py`: stdio + streamable-HTTP + SSE, change listeners, `auth` health), browser sign-in (`auth.py`), credentials store (`secrets.py`), install service (`install.py`), well-known servers (`catalog.py`), `/mcp` command (`manager.py`) |
| `utils/` | Shared helpers: `io.py` (secure file writes), `http.py`, `html_clean.py`, `serialize.py`, `time_fmt.py`, `origins.py` (which tool an agent / skill / MCP server comes from) |
| `pet/` | The TUI pets (UI-free): `model.py` (`Pet` stats/XP/levels/badges/streak/accessories, `Roster` of up to 6 pets, `pet.json`), `sprites.py` (species pixel art, palettes, accessories, growth, props, `pen_stage` scene, `sky`, text pets), `events.py` (tests/git moments from `run_bash` output), `session.py` (`/new` recap) |
| `state.py` | **Module-level mutable globals** shared across the package (client, messages, model, flags, theme, **active_agent**) — mutate via `jarvis.state.<name> = ...` |
| `constants/` | Paths (`~/.config/harness-agent/`, `~/.harness/`), model names, OAuth endpoints, system prompt, provider identifiers, **`default_agents/*.md`** (bundled coding/reverse_eng/setup) |

### Live model discovery

Free model line-ups change constantly, so free tiers are **discovered at
runtime**, not hard-coded:

- **Harness Agent (free tier, no key)** has no model list in code:
  `constants/providers.py:harness_agent_models()` is OpenCode Zen's list
  (`_gateway_rows`, below) narrowed to models.dev's $0 models — exactly the
  rows Zen tags Free, image tags included (`model_supports_images` /
  `model_pricing` read `harness_agent` as Zen). `harness_agent_default_model()`
  picks by rule (first tool-capable, non-preview/beta); `HARNESS_AGENT_FALLBACK_MODEL`
  (`big-pickle`) is used only before any catalog exists (import time, cold first
  run) and is the picker's only row then. `auth/zen_catalog.py` (OpenCode's own
  5 MB catalog ∩ served) is the stand-in, fetched only while `HARNESS_MODELS_DEV=0`.
  The free client sends models.dev's `/responses` models there
  (`native_responses_models`, plus `_zen_wire.RESPONSES_API_MODELS` for when models.dev is off).
- `auth/openrouter_catalog.py` — every $0 model in `openrouter.ai/api/v1/models`.
  **Nothing is hidden**: a row missing from `/model` is more confusing than a
  caveated one, so unusable models are labelled and sorted last instead.
  Two flags do that — `no tool use` (the harness always sends tools, so those
  models fail every turn) and `restricted` (answered `403 permission_denied`;
  OpenRouter gates a few free models to its allowlisted apps, and nothing in
  the catalog marks them, so the only way to know is to be refused once —
  entries age out after a week). `FreeModel.usable` is the combined check, and
  only usable models are ever auto-selected as a fallback.
- `auth/codex_catalog.py` — the ChatGPT-OAuth Codex line-up from
  `chatgpt.com/backend-api/codex/models` (needs the user's access token and a
  recent `client_version` — an old one returns only legacy models). Codex
  retires models silently (404 `model_not_found` for a still-listed legacy id,
  400 "not supported" for a dropped one): `repl/stream.py` marks the model
  `unavailable` (sorted last, ages out after a week) and retries on
  `codex_default_model()`, and `normalize_model_for_provider` never keeps a
  refused Codex model. Seeds in `constants/providers.py` are used only while
  the cache is cold and are never appended to a live list.
- `auth/models_dev.py` — **every provider and model on [models.dev](https://models.dev)**
  (MIT licence, (c) models.dev — github.com/sst/models.dev; the same data
  OpenCode uses). There is no per-provider endpoint: `api.json` is the whole
  database (~5 MB, ~500 KB gzipped), fetched on a worker with `If-None-Match`
  (unchanged → 304, no body), trimmed by `trim()` to the fields used here
  (~0.9 MB) and cached as `model_catalog/models_dev.json` (`SCHEMA` bump =
  old cache ignored). Reads are memoised on the file's mtime. Two uses:
  - **Catalog providers** `md:<models.dev id>` (~200): any provider whose
    entry gives a base URL (`api`, or `KNOWN_BASE_URLS` for SDK-only ones
    like OpenAI / Groq / Google / Mistral / xAI) and speaks OpenAI
    chat-completions (→ `OpenCodeClient`) or Anthropic messages (→ Anthropic
    SDK, `api` minus `/v1`). Per model: `wire` (`chat` | `responses`; OpenAI's
    own pro/codex models and gateway models whose override npm is
    `@ai-sdk/openai` go to `/responses`; Anthropic/Google overrides and
    per-model base URLs are dropped), deprecated / non-text models dropped,
    tool-less ones labelled `no tool use` and sorted last (newest usable
    first). `client.py:_build_catalog_client` passes per-model hints to
    `OpenCodeClient(model_hints=…)`: `reasoning_content` is echoed only for
    `interleaved.field == reasoning_content` models (strict APIs — Mistral,
    Groq, OpenAI — reject the field), `reasoning_effort` only for reasoning
    models, `max_tokens` clamped to the model's output limit, OpenAI gets
    `max_completion_tokens`. Built-ins pass no hints, so their requests are
    unchanged. Catalog models **never** enter `MODEL_INFO` / `PRICING` (keyed
    by id alone — another provider may share it): use `model_pricing(model,
    provider)`, `model_supports_images(model, provider)`,
    `model_belongs_to_provider` (strict for catalog providers). Skipped:
    built-ins under another name (`NATIVE_IDS`), `SKIP_IDS` (Copilot
    / GitLab need a token exchange; Kimchi was removed as a provider on purpose), anything needing cloud auth (Bedrock,
    Azure, Vertex …). `${VAR}` in a base URL is filled from the env (missing
    → not connected, the dialogs say which var).
  - **Built-in enrichment**: `_native_extras()` appends models.dev's newer
    models to OpenRouter (all paid models) and the Anthropic API key list,
    registered via `register_dynamic_model()`; Anthropic's curated rows come
    first and are never changed. **OpenCode Go and OpenCode Zen (keyed) have
    no curated rows at all** (`_gateway_rows`): each gateway's public served
    list (`opencode.ai/zen/v1/models`, `…/zen/go/v1/models`, cached by
    `auth/opencode_catalog.py`) says what exists, models.dev (`opencode`,
    `opencode-go`) describes it — a model models.dev marks deprecated is
    dropped even while the gateway still serves it, as OpenCode does
    (`_gateway_retired`; Zen kept serving dead `deepseek-v4-flash-free`, tagged
    free, every request a 400 "Model is unavailable"), and isn't kept as a saved
    model — except the free tier's own models on Zen (`is_harness_agent_model`),
    a served model models.dev doesn't know is listed as "Not on models.dev yet,
    price unknown", and ids models.dev routes to another wire (Claude / Gemini
    on Zen, some MiniMax on Go — `native_skipped()`) never show. Defaults are
    rules, not ids: `opencode_zen_default_model()` = a served free, non-preview
    model; `opencode_go_default_model()` = the newest tool-capable,
    non-preview, non-free one (neither ever retiring / undescribed); with no
    catalog at all they fetch once, and Zen falls back to the free tier.
    `normalize_model_for_provider` swaps a saved model the gateway stopped
    serving. Prices and vision for these come from models.dev *for that
    provider* (`model_pricing` / `model_supports_images` never borrow another
    provider's entry for the same id).
    `native_responses_models()` tells the Go / Zen clients which models go to
    `/responses`.

  Keys: `auth/catalog_keys.py` → `~/.config/harness-agent/provider_keys.json`
  (`{"md:<id>": key}`, created 600). The provider's models.dev env var wins
  (read-only, like built-ins) — **except** a var several catalog providers
  read (`MINIMAX_API_KEY` = 4 MiniMax endpoints) or a generic one
  (`GITHUB_TOKEN`): those never auto-connect; the user saves the one they
  mean, and a saved `$VAR` is a reference, expanded at use. Catalog keys are
  deliberately **not** in `_has_usable_provider_credentials` (an unrelated
  `OPENAI_API_KEY` must not move a first run off the free tier).
  `HARNESS_MODELS_DEV=0` disables it all. UI: `/key` lists built-ins, then
  connected models.dev providers, then the rest (search with `/`;
  `KeyModalScreen(focus=pid, add=True)` opens straight on one); `/model`
  shows connected ones as groups and, while searching, `+ <Provider>` rows
  (`CONNECT_ID`) that open `/key` for it and come back to the picker;
  `/provider <id>` takes a bare models.dev id. Web: catalog providers are
  `Card(catalog=True)` rows (`card_for()`), connected ones in *API keys*, the
  rest in a searchable *More providers* group after the unconnected built-ins
  (filtered in place via `hidden`; popular ones first); keys are checked with `GET {base}/models`.
  Tests: `tests/test_models_dev.py`; **`conftest.py` gives every test an
  empty catalog, a private keys file and no models.dev network** — seed one
  with `models_dev.store(models_dev.trim(raw))`.
- `auth/catalog_cache.py` — stale-while-revalidate disk cache under
  `~/.config/harness-agent/model_catalog/`.

**The picker never fetches on the UI thread.** `ModelPickerScreen.on_mount`
builds rows from the cache (sub-millisecond) and runs `refresh_model_catalogs()`
in a `@work(thread=True)` worker that swaps the rows in when it lands; the TUI
also warms the cache at startup (`_warm_model_catalogs_background`). Anything
calling `model_picker_rows(live=True)` **must** be on a worker thread.
`/model refresh` forces a live refresh and retries previously-refused models.

OpenRouter has **no** `ModelSpec` entries — don't add any (a hard-coded list
rotted: a retired `:free` model kept its Free tag and was the default, and a
price was 5x off). Everything comes from live data: `openrouter_catalog`
fetches `openrouter.ai/api/v1/models` once and caches both the $0 list
(`openrouter_free`) and every served id (`openrouter_ids`, `served_ids()`);
paid models come from models.dev, minus any id OpenRouter no longer serves
and minus models.dev's own "free" rows once OpenRouter's list is known. The
Free tag (`model_is_free`) is OpenRouter's $0 list, never the `:free` suffix
(models.dev prices only before the first fetch). `openrouter_default_model()`
= best usable live free model → a free, tool-capable models.dev one →
`OPENROUTER_DEFAULT_MODEL` (`openrouter/free`, OpenRouter's own free router,
also the only row shown while no catalog has arrived);
`normalize_model_for_provider` swaps a saved model OpenRouter no longer serves
for that default at startup. Models discovered at runtime register their
pricing and vision support via `register_dynamic_model()`.

### Key data flows

- **Tool routing**: Each API call goes through `tools/router.py:select_tools()`, which regex-scans the last 4 messages and keeps any tool groups already active in the tool-call loop.
- **Conversation state**: All messages live in `state.messages` (plain dicts). The tool-call loop in `main.py:_send_and_loop()` / `tui/app.py` continues until `stop_reason == "end_turn"`.
- **Tool execution**: Tools in `repl/render.py` run concurrently via `ThreadPoolExecutor` except for tools in `_SERIAL_TOOLS` (shell, file edits, macOS UI control, MCP tools) which run single-threaded.
- **TUI rendering**: every `console.*` call lands in `tui/console_shim.py:TUIConsole`, which mounts one widget per entry in the bottom-anchored `Transcript` (`tui/transcript.py`): `UserBlock`, `AssistantBlock` (streaming markdown), `ThinkingBlock`, `ToolBlock` (one row per tool call from `emit_tool_event` — `repl/tool_events.py` passes `input`/`output`), `DiffBlock` (from `file_diff`, placed under its edit row), `NoticeBlock` (plain prints, coalesced), `TurnFooter`. Blocks render themselves from their own data at paint time as pre-wrapped `Text` (cached per width/theme), so theme switches, ⌃T trace toggles and resizes restyle in place — **never replay history to restyle**. Markdown goes through Rich (`md_render.py`), not Textual's `Markdown` widget (too many widgets per message). Streaming: worker threads only append deltas to a locked buffer; the app's ~24fps pump (`mixins/activity.py:_tick_activity` → `TUIConsole.pump`) drains them, freezing text before the last safe paragraph break and splitting long replies into continuation blocks. Shared code checks `getattr(console, "renders_tool_rows", False)` to skip legacy REPL panels, and optional hooks (`file_diff`, `show_thinking`, `show_reply`, `show_plan`, `show_welcome`) to render natively; `WebMuxConsole` mirrors those hooks to web clients. Cancellation: Esc finalizes the UI immediately and marks the worker thread cancelled (`state.cancel_thread` / `state.turn_cancelled()`); stream deltas are owned by the thread that started the stream, so a stale worker can't touch the next turn. `_cancel_turn` passes the turn's own worker id to `cancel_current_stream(thread_id=…)` (a bare call only interrupts a thread inside `call_claude_stream` right now — thread ids get reused); `_run_turn` re-checks `turn_cancelled()` before writing a finished reply, so a cancelled turn never lands in the next chat. **Changing session while a turn runs stops it first** (`_stop_turn_for_session_change`: web *New chat* / resume via `_handle_web_action`, the `/session` picker via `_resume_session`) and drops prompts queued for the old chat — left running, it kept "thinking" into the new chat and everything queued behind it. `run_bash` polls (`_run_process`): a cancelled turn kills the command's whole process tree and frees `_bash_lock` within ~0.2 s (it used to hold the lock up to the 60 s timeout, so the next command looked stuck). Web: `renderSnapshot` never carries a half-streamed bubble into another session.
- **Prompt enhance** (one-click spelling/grammar fix before sending): `jarvis/prompt_enhance.py:enhance_prompt()` is a side request to the current model (`state.client` / `state.MODEL`), never added to `state.messages`, and the result only replaces the input text, so sending stays up to the user. Code, `@file` refs, URLs and `[image 1]` / paste chips are masked as `⟦n⟧` and restored exactly; a reply that drops one, answers the prompt instead (much longer than the prompt), or keeps calling tools is rejected, and the original stays. Clients that force tools into every request (`gate_tools`, the free Harness Agent tier) get a `corrected_text` answer tool, because those models call a tool no matter what. TUI: `tui/enhance_button.py` (`✦ enhance` → spinner → `↶ undo`, in the composer) + `tui/mixins/enhance.py`; ⌃G toggles; the swap is one TextArea edit, so ⌃Z undoes it; Esc cancels; the prompt is read-only while it runs. Web: `POST /api/enhance` (runs on the HTTP handler thread) + the `#qc-enhance` chip in `composer.js` (Alt/⌥E; `execCommand('insertText')` keeps ⌘Z working on desktop).
- **Sticky prompt**: once the user box of the turn you're reading scrolls out of view, `tui/sticky_prompt.py:StickyPrompt` pins a copy at the top of the transcript — an overlay docked in `#body` (`layers: base overlay`), so nothing shifts. It follows the turn owning the top of the viewport (`Transcript.sticky_prompt()`, from the cached arrangement via `prompt_spans()` — never the compositor's full map). Click = jump back, `↑ ↓` / `alt+↑ alt+↓` = previous/next prompt (`Transcript.step_prompt`), `⎘` copies, hover expands, spinner while that turn runs. Synced from `_refresh_activity_widgets` (scroll + activity tick) and `Transcript.watch_virtual_size`; wired in `tui/mixins/prompt_nav.py`. Setting `ui.sticky_prompt` (default on).
- **Tool images (`screenshot`)**: `tools/screenshot.py` captures the screen / an app window (`screencapture -l`, window ids via JXA) / a region / a web page (headless Chrome) / an image file, scales to ≤1568 px, and returns text plus a `[[jarvis:image <path>]]` marker line. `render.py` turns markers into native `image` blocks inside the tool_result (`utils/tool_images.py:tool_result_content`); OpenAI-style providers (`opencode_client`) and Codex (`codex_client`) move those images into a user message right after the tool messages. `repl/trim.py:prune_tool_images` keeps only the newest 3 in each request (none for non-vision models). Models without vision get OCR text instead. Missing Screen Recording permission surfaces as a clear error.
- **Background jobs (`run_bg` / `bg_output` / `bg_kill`)**: `tools/background.py` — same danger check + approval as `run_bash` (`shell.ask_approval`), detached process group, output to `$TMPDIR/jarvis-bg/<pid>/job-N.log`, a watcher thread records the exit. Running / finished-but-unread jobs are listed in the system prompt (`repl/system.py:_background_jobs_block`); the TUI lists jobs in the sidebar and, on finish, prints a notice and **auto-wakes the agent** (`tui/mixins/bg_jobs.py:BgJobsMixin`): once idle it starts a turn badged `& job #N` carrying the output (`background.take_wake_batch` + `wake_message`) — user-queued prompts go first, jobs finishing together share one turn, no wake right after Esc, killed/already-read jobs never wake. With auto-wake on (`background.set_auto_wake`, TUI only) the tools tell the model not to wait and `bg_output(wait=…)` is capped at `WAKE_MAX_WAIT` (30s), so a turn never hangs on a long job; the legacy REPL keeps the old polling advice. All jobs are killed at exit. Routed via the `vision` / `background` tool groups in `tools/router.py`.
- **/loop (`schedule_wakeup`)**: `jarvis/loop.py` holds the loop state + the `schedule_wakeup` tool (offered only while a loop exists; `loop` tool group) + the `LOOP MODE` system-prompt block. `/loop <task>` is self-paced (the agent must call `schedule_wakeup(delay_seconds 60–3600, prompt, reason, noop)` each run or the loop ends; `stop=true` ends it), `/loop 5m <task>` is a fixed interval, `/loop` shows status, `/loop stop` ends it. `tui/mixins/loop.py:LoopMixin` owns the timer, starts each run as a normal turn badged `⟳ loop #N`, waits for the user's turn if one is running, folds streaks of quiet runs (`noop=true`) into one clickable `LoopQuietBlock`, and shows the countdown on the activity line. Esc during a run stops the loop.
- **Web remote (`jarvis --web`)**: `jarvis/web/` — `server.py` (ThreadingHTTPServer), `handler.py` (API + static), `bridge.py` (SSE fan-out; a stalled client gets `resync` instead of silently dropped events), `console_mux.py:WebMuxConsole` (wraps `TUIConsole`, mirrors streams/tool rows/diffs/prompts to the browser; tool rows get `title`/`args`/`summary` from `tui/tool_format.py`), `sync.py:StateWatcher` (polls `state_api.state_fields` once a second while a browser is connected and pushes `state`, or a full `snapshot` when the session/trace/transcript was swapped — this is how terminal-side `/model`, Tab-cycled agents, `/new` reach the web). Frontend is plain ES modules in `static/js/` (no build step): `chat.js` (turn-grouped transcript, streams finalised in place; sent messages are never edited — every send is a new message; only live entries animate in via `is-new`, snapshot re-renders stay still; long tool runs and long prompts fold), `composer.js` + `quickbar.js` (per-device draft in localStorage), `quote.js` (select transcript text → Quote pill → `>` block in the composer), `sidebar.js`, `palette.js` (⌘K, items from `catalog.js`), `pickers.js`, `prompts.js` (approval/questions/input; dismissing minimises to a chip), `theme.js` (light / soft dark (`dim`) / dark / system + accent presets or a custom colour from the hue bar, nudged to preset-like contrast per theme and set inline on `<html>`; theme/accent changes reveal in a circle via View Transitions; tokens in `css/tokens.css`). Motion helpers (`animateEl`, `countTo`, `haptic`) live in `utils.js` and respect reduced motion. Icons are a local Lucide subset in `static/js/icons.js` (works without internet on the LAN) — to add one, regenerate that file with the new name; `tests/test_web_remote.py` fails on any icon, asset or import that doesn't exist. ask_user answers from the web use `selected_ids` (same as the TUI askbar). Prompts (approval / question / input) go on the bridge even while no page is connected, and the snapshot a connection opens with (SSE hello, first long-poll) carries the pending ones as `prompts`: `prompts.js:syncPrompts` shows one asked while the page was away and closes one answered elsewhere — `prompt_resolved` only reaches connected pages, and phones drop their stream when locked. In the TUI, `/web` / `/web qr` (`tui/web_modal.py:WebConnectScreen`, routed in `app._open_modal_for` → `mixins/web_remote.py:_handle_web_command`) starts the remote mid-session and shows a QR + link dialog; `/web hide|show|copy|stop`. The composer's `🌐 web` button (`tui/web_button.py`, right of the prompt, always shown; the word turns green while the remote runs) and `/web open` start the remote if needed and open this computer's browser on the loopback link (`_web_local_url`, token included) via `action_open_web_browser`; if no browser opens, the link is copied instead. The corner QR (`tui/web_bar.py`) hides on click; the preference is settings `web.qr`. While it runs the footer carries one item (`mixins/web_remote.py:_web_footer_markup`): `🌐 host:port` (never the token) + `· N connected`, shrinking to `🌐 web` when space is short; click → the dialog, ⌃⇧U copies the full link. **Footer vs welcome**: the welcome block is the workspace (version, cwd, branch, project context, key hints), the footer the session (agent, model, provider, think, context/tokens/cost, web remote, help) — never show one thing in both. `/web stop` → `server.stop_web_server` (bridge `close()` ends every SSE stream, port freed, console unwrapped). **Anywhere** (`/web anywhere`, `jarvis --web --tunnel`, `HARNESS_WEB_TUNNEL=1`): `web/tunnel.py` runs `cloudflared` (quick tunnel, preferred — no account) or `ngrok` (setting `web.tunnel` auto|cloudflare|ngrok), reads the public URL from its output (Cloudflare: waits until DoH resolves the host, so phones don't cache NXDOMAIN) and the QR/link then carries `?token=` — `handler._may_hand_out_token` never redirects a tunnel/proxied request to the token. Cloudflare quick tunnels buffer SSE, so the page long-polls there (`/api/poll`, `bridge.poll`; auto-selected on `*.trycloudflare.com` or when SSE stays silent); poll clients count as connected for approvals.
- **Web attachments (media)**: `jarvis/media.py` (UI-free) + `static/js/media.js` + `css/media.css`. The page uploads each file as soon as it's added — 📎 button / ⌘K *Attach files* / drag-and-drop anywhere / paste an image — with `POST /api/upload` (raw body, name in `X-File-Name`; XHR progress ring, 45 s stall timeout, 3 at a time). `save_upload` streams it to `uploads/<16-hex id>/<clean name>` + `.meta.json` (cleaned names never start with ".", so nothing Jarvis adds can collide), refusing empty / over `MAX_FILE_MB` (checked from Content-Length, before reading) / cut-off / unsupported / damaged files with an `UploadError(status, code, message)` the tile shows (images and PDFs are checked by magic bytes; unknown extensions pass only as UTF-8 text). `POST /api/prompt {text, attachments: [ids]}` checks the ids (`resolve` → 410 when gone; 400 on a `/` or `!` command), marks them sent and passes them through `bridge.submit_prompt(text, files)` → `_handle_web_submit` → `_begin_turn(…, attachments=)` / the prompt queue (3-tuple `(text, registry, ids)`; `media.queue_label` names the files) → `_run_turn`, where `media.user_content` builds the message: image *reference* blocks (`source.type == "jarvis_upload"`, never base64 in history / sessions.db) + the text and a note listing every file's path (`NOTE_HEAD`), so tools can open it. `repl/stream.py` calls `materialize_uploads` per request: the newest `KEEP_IMAGES` (8, ≤ 16 MB) become base64 blocks from `model_image()` (upright — EXIF orientation applied — ≤ 1568 px, ≤ 3.5 MB; converted with Pillow if installed, else `sips`, else ImageMagick, cached as `.model.*`), the rest — all of them for a model without vision — a one-line note. Transcripts read the note back (`attachments_in` → `attachments` on `you` entries in snapshots and the live `message` event; pruned files come back `missing`); the TUI shows `📎 a.jpg · b.pdf` instead of paths. `GET /api/media/<id>[?v=thumb|view][&dl=1]` serves by id only, with byte ranges (Safari video), `nosniff`, a sandbox CSP (not for PDFs — Chrome refuses to render them sandboxed) and text as `text/plain`; `thumb` / `view` are browser-friendly JPEG/PNG copies (HEIC, TIFF, big photos). Tool screenshots (`[[jarvis:image …]]` in a tool's raw output) get memory-only `s…` ids via `media.expose` and show under their tool row (`images` on `tool_done` / snapshot tool entries from `tool_output_history`). Web UI: tray in the composer (finished uploads survive a reload via localStorage; Send while uploading sends when they finish; failed tiles say why and offer Retry when it can help), photo grid + document cards (audio plays inline) above the user bubble, and a full-screen viewer (`#viewer`: ← → / swipe, Z zoom, swipe down / Esc closes, download, open in tab). `media.prune` runs when the web server starts. Model list tags (web picker, right side of each row, in this order): **Free** · image mark · **In use**. `/api/models` rows carry `free` (`pickers_api._is_free`: Harness Agent always; OpenRouter = in the cached $0 catalog or a `:free` id; Zen = its free list; models.dev providers only when both prices are listed as 0 — an unknown price is never "free") and `images` (`_sees_images` → `model_supports_images(model, source)`, the same check the request path uses); the image slot is kept empty on rows without it so tags line up; searching "free" or "vision" / "image" lists only those. The terminal `/model` picker shows the same tags (`tui/model_modal.py:model_tags` in a fixed-width `picker_row(tags=…, tags_width=…)` column, legend in the hint line, same "free" / "vision" searches). Web providers are drawers (`pickers.js` `modelSpec.renderRows`, styles `.mdrawer*` in `overlays.css`): one continuous list split by hairlines; a sticky header (monogram tile, name, model count, chevron up = open / down = closed; a closed drawer holding the current model says "In use") and the models hanging off a rail, where a dot marks the model in use (rows use `node: true` instead of an icon tile). Every row is always rendered and the drawer slides via the shared `.fold` grid trick, so toggling is a class flip (`applyOpen`), never a re-render; `listNav` and `reachableRows` skip rows in a closed drawer. Closed drawers are remembered per browser (`localStorage` `jarvis-model-groups-closed`), "Collapse all / Expand all" sits at the end of the search bar (`spec.searchExtra` → `#picker-search-extra`, icon-only on phones), and a search shows every match with plain headers; `state_fields.vision` drives the tray's "can't see images · Pick a model that can" note (opens the picker pre-filtered: `spec.searchArg`). Tests: `tests/test_web_media.py`.
- **Installing skills / MCP servers for the user** (agent tools, `/skill` `/mcp` commands, both UIs share one service layer): tools in `tools/extensions.py` (group `extensions`, routed by `router.EXT_RE` — the words mcp/skill, `add linear`, npx/uvx, GitHub skill links — or while a sign-in waits): `skill_install`, `skill_remove`, `mcp_add`, `mcp_list`, `mcp_connect`, `mcp_remove`. **Scope**: `project` (`.harness/skills`, `.mcp.json`) or `global` (`~/.harness/skills`, Jarvis's `~/.config/harness-agent/mcp.json`), default global; a global install turns the hidden global scope on and says so. **Approval**: a call the *agent* makes asks first (`shell.ask_approval`, skipped by auto-approve) — UI/`/` commands don't, the user did it themselves. `mcp/install.py`: `parse_source` understands a hosted URL, `npx -y pkg`, `claude mcp add …`, JSON (Claude/Cursor/VS Code shapes, `${input:x}` → `${X}`), a GitHub repo link (README is read; several servers → asks which), a catalog name; `add_mcp` writes via `config.write_server` (keeps an existing `.mcp.json` schema, new project file = shared `mcpServers` format), connects, and returns `connected | auth_required | needs_credentials | failed | exists | denied`. `storage/skill_install.py`: GitHub (tarball from codeload — no git needed; tree/blob/raw/`o/r@skill`/skills.sh/`npx skills add`), SKILL.md link, `.zip`/`.skill`/`.tar.gz`, git URL, local folder; archives are unpacked with path/link/size/count checks; a bad header is repaired (name → lowercase-hyphen, description ≤ 1024); several skills in a source → `needs_choice`; provenance in `.jarvis-source.json` (enables `update_skill`); only Jarvis-managed folders are ever removed/moved. **Credentials never enter config files**: `mcp/secrets.py` keeps values in `~/.config/harness-agent/mcp_secrets.json` (600) and the config holds `${NAME}`; `registry.connect` expands them. A missing key is asked with `console.input(password=True)` (TUI modal / web prompt — never through the model). **Hosted servers** (`type: http|sse`, transport auto-fallback both ways) run in ONE asyncio task each (`registry._run_remote`; the SDK's cancel scopes need enter/exit in the same task). A 401 starts the SDK's `OAuthClientProvider` via `mcp/auth.py`: `FileTokenStorage` (tokens + registered client per server, 600, honours real expiry so restarts refresh instead of re-asking), `coordinator` (one pending sign-in per server: authorize URL → UI button; loopback listener `http://localhost:33418/callback` (`HARNESS_MCP_OAUTH_PORT`) or a pasted address from another device; states `pending → working → done|error|cancelled`, 10 min). `registry.connect` returns `AUTH_REQUIRED_MSG` (`needs_auth(err)`) while the task keeps waiting; tools register by themselves when the sign-in finishes. **Startup never starts a sign-in**: `startup_connect()` (auto-connect, global-scope toggle) makes a server that needs one read `auth` until the user clicks. UIs learn of changes through `mcp_registry.add_listener(fn(event, name))` (events: connecting, connected, failed, disconnected, auth, auth_required/working/done/error/cancelled) and `auth.coordinator.add_listener`. Web: `web/extensions_api.py` (route table in its docstring; runs on the handler thread) + bridge events `mcp` / `skills`; the page shows the *Authenticate* button in the MCP dialog and a banner above the composer. Terminal: an attention bar above the composer + sidebar rows + `/mcp` dialog. Tests: `tests/test_mcp_install.py`, `test_mcp_remote.py` (real demo server `tests/mcp_demo_server.py`, OAuth included), `test_skill_install.py`, `test_extension_tools.py`; the `ext_env` fixture in `conftest.py` isolates HOME, config files and the project folder.
- **Providers on the web** (`/provider`, `/login`, `/key` in the browser, the sidebar *Providers* card, *Add a provider* under the model list): `web/providers_api.py` is the web twin of the terminal's `/provider` hub — one dialog (`static/js/providers.js`) lists every provider (Claude Pro/Max + ChatGPT sign-in, Anthropic API / OpenRouter / OpenCode Go / Zen keys + every models.dev provider, free Harness Agent) with its status and next step. Groups (`providers.js` `GROUPS`): *Subscriptions*, *API keys* — **only providers that have a key** (saved or env var, built-in or models.dev) —, *More providers* — every key provider without one, built-ins first (always shown), then models.dev (popular ones until you search; filtered in place via `hidden`) —, *Free*. Adding or removing a key moves the row between the two (`sigOf` marks connected key rows, so the list rebuilds). Keys: pasted text is cleaned (`export X=…`, quotes, `Bearer`), matched by prefix (a key pasted at the top finds its own provider), checked live with the provider where a cheap 401 exists (Anthropic, OpenRouter, models.dev providers via `GET {base}/models` — a refused key is never written), then saved to the same files as `/provider` (`API_KEY_SPECS`); env-var keys are shown but not editable. Sign-in never opens a browser on the computer — the page opens the link on its own device: Claude → paste `code#state`; ChatGPT → a `localhost:1455` listener finishes by itself on this computer, or paste the callback address from a phone (`OAuthFlow`, one per provider, 15 min, `stop` event frees the port). Network work runs on the HTTP handler thread; every session change (`provider_use`, `provider_key_saved`, `provider_key_remove`, `provider_signed_in`, `provider_sign_out`) goes through `bridge.request_action` to the TUI main thread and emits a `providers` event so every open page refreshes. Keys never come back to the browser — rows carry the last 4 characters. Tests: `tests/test_web_providers.py`.
- **File changes + inspector panel (web)**: `jarvis/file_changes.py` (UI-free) is a per-session ledger of what changed on disk. `write_file` / `edit_file` / `multi_edit` report through `repl/file_diffs.py:emit_file_diff` (always, even with `HARNESS_SHOW_DIFFS=0`), and `run_bash` wraps the command in `watch_shell()`, which snapshots the files the command line names (globs, `cd x && …`, redirect targets, directories for `rm -r` / `mv` / `cp -r`) and records only what really changed — so `rm`, `mv`, `sed -i`, `>` show up as deleted / renamed / edited. Each file keeps the text from **before Jarvis first touched it**; the diff is always that baseline vs the file on disk *now* (`_settle` re-stats the file, so later out-of-band edits are seen; ten edits read as one change, an undone edit or created-then-removed file disappears, a deleted file whose text equals an added one folds into one `renamed` row). Caps: 512K chars per file (bigger → counts only), 400 files, 24M chars held per session; ledgers are keyed by `state.current_session_id`, so resuming a session in the same process brings its changes back. Web: `GET /api/changes` (list + totals + server clock), `/api/changes/file?id=` (numbered hunks + edit history; ids come from the ledger, never a path), `/api/changes/patch[?id=]` (a `git apply`-able patch); `server.start_web_server` subscribes a listener that pushes a `change` event (full list + the fresh diff of the file that changed) while a browser is connected; snapshots carry `changes`, and `state_fields` carries `jobs` (background jobs, only stable values so the watcher stays quiet). Frontend: `inspector.js` is the shell (docked third grid column ≥1280px, open by default and remembered; a floating panel 961–1279px; a full-height sheet ≤960px with swipe-to-close; drag-to-resize + a wide toggle; `Alt+D`), `changes.js` the Changes tab (one card per file in first-changed order so an edit never reshuffles what you're reading; **follow** opens the file Jarvis just changed and scrolls to the new rows, which flash; filters, copy path / diff / whole patch, edit history; a "N files changed · Review" chip above the composer while the panel is shut; inline transcript diffs get a *Panel* button), `diffview.js` (pure strings — unified / side-by-side with word-level marks — checked under Node by `tests/test_web_diffview.py`), `activity.js` (live status, background jobs, tool mix, a timeline whose rows jump to the message). Only live changes animate; snapshots just appear. Tests: `tests/test_file_changes.py`, `tests/test_web_changes.py`.
- **Persistence**: Sessions stored in SQLite at `~/.config/harness-agent/sessions.db`. Pinned context from `~/.config/harness-agent/pinned.txt`. Aliases from `~/.config/harness-agent/aliases.json`. Unified preferences in `~/.config/harness-agent/settings.json` (global) merged with `<cwd>/.harness/settings.json` (per-project override).
- **Claude 5 thinking**: on the Anthropic provider (API key or OAuth), Claude 5 models and Opus 4.7/4.8 (`constants/providers.py:claude_uses_adaptive_thinking`) get `thinking: {type: "adaptive", display: "summarized"}` + `output_config.effort` (`claude_thinking_kwargs`; "minimal" → "low"); thinking off sends effort `low` instead of `{type: "disabled"}` (a 400 on Opus 5.5 / Sonnet 5.5 / Fable). `budget_tokens` is a 400 on all of them — every other model keeps the `enabled` + `budget_tokens` form (`repl/stream.py`).
- **Auth**: `auth/client.py:make_client()` checks for `ANTHROPIC_API_KEY`, then stored key/OAuth tokens, then prompts interactively. Sets `state.provider` and `state.auth_mode`.
- **Project context**: On startup, detects `AGENTS.md`, `AGENT.md`, `CLAUDE.md`, or `JARVIS.md` in CWD and stores only the path in `state.project_context_*`; file content is loaded on demand via `read_file()`.
- **Agents**: Markdown files with YAML frontmatter (`storage/agents.py`). Project sources scanned always: `.harness/agents/`, `.claude/agents/`, `.opencode/agents/`, `.agents/`, `.cursor/agents/`, `.gemini/agents/`. Global (opt-in via `agent.global`): `storage/agents.py:global_agent_dirs()` — `~/.harness/agents/`, `~/.claude/agents/`, `~/.cursor/agents/`, `~/.gemini/agents/`, `~/.config/opencode/agent(s)/`. At most one active agent at a time; its body is appended to the system prompt by `repl/system.py:_agent_addon_block()`. Bundled defaults (coding/reverse_eng/setup) seeded into `~/.harness/agents/` on first run.
- **Skills**: SKILL.md packs auto-invoked by the LLM based on `description:` frontmatter. Project sources: `.harness/skills/`, `.skills/`, `.opencode/skills/`, `.claude/skills/`, `.agents/skills/`. Project also reads `.cursor/` `.codex/` `.gemini/` `.kiro/skills/`. Global (opt-in via `skills.global`): `storage/skills.py:global_skill_dirs()` — `~/.harness/skills/`, `~/.config/harness-agent/skills/`, then OpenCode, Claude, `~/.agents/skills` (shared), Codex (`$CODEX_HOME`), Cursor, Gemini, Kiro, Copilot. The picker modal (`tui/skill_modal.py`) browses, installs from a link, removes, moves and updates (see *Installing skills / MCP servers*) — no sticky selection.
- **Where things come from (origin tags)**: every agent, skill and MCP server carries the tool it was read from — `tool` (`jarvis`, `claude`, `cursor`, `codex`, `gemini`, `antigravity`, `opencode`, `agents`, `kiro`, `copilot`, …) and `also` (other tools with one of the same name; first source wins, the rest are shadowed). Agents/skills get it from their folder (`utils/origins.py:tool_for_path` — nearest known folder wins); MCP servers from their config file (`mcp/config.py:_global_sources`, `MCPConfig.get_source` / `get_also`; `mcp/sources.py` labels derive from `origins.TOOLS`). MCP also reads Codex `config.toml` `[mcp_servers.*]` (TOML; `bearer_token_env_var` / `env_http_headers` → `${VAR}` headers), Gemini `settings.json`, Antigravity, Kiro and Copilot configs, and `httpUrl` / `serverUrl` as the URL. Shown as `◆ claude +2` in the TUI dialogs (agent rows, skill rows, MCP rows), the sidebar MCP rows, `/agent` and `/skill` lists, and as badges on the web (`utils.js:originBadge`; API rows carry `tool`, `tool_label`, `also_labels`). With a global scope off, the agent dialog still lists those agents dimmed (↵ turns global agents on and activates), the skill dialog lists them as hidden, and the MCP header says what `g` would add (`global_source_summary`). Skill search matches tool names. Tests: `tests/test_origins.py`.
- **Custom commands**: User-defined prompt templates (`storage/commands.py`) triggered directly as `/<name> [args]` — dispatch falls through to them after all built-ins (`commands/dispatch.py:try_custom_command`). Project sources (recursive): `.harness/commands/`, `.claude/commands/`, `.opencode/command(s)/`, `.agents/commands/`. Global (opt-in via `commands.global`, default **on**): `~/.harness/commands/`, `~/.config/harness-agent/commands/`, `~/.claude/commands/`, `~/.config/opencode/command(s)/`. Body placeholders `$ARGUMENTS` and `$1`…`$9` are filled from the typed args (`expand_template`); args with no placeholder are appended. Managed via `/command` (`commands/command.py`): new/edit/show/delete/refresh/run, `global on|off`, `scope`, `export`/`import`. In the TUI, bare `/command` opens the manager modal (`tui/command_modal.py`): Enter inserts `/<name> ` into the prompt box, `t` inserts the full template for editing, `n`/`e` open an in-app editor sub-modal (name + description inputs + template TextArea; `^s` saves via `storage/commands.py:write_command`, which edits project AND global files in place and renames on name change), `d` delete, `i`/`x` import/export, `g`/`s` scope toggles. Custom commands also appear in the palette via `commands_catalog.filter_commands`; built-ins always shadow same-named custom commands. **Web**: `web/commands_api.py` (`GET /api/commands` — every command with its template, scope, origin tool, `takes_args`, `active`, `builtin`; `POST /api/commands/save|delete|copy`, which only touch files discovery found, never a page-supplied path; the global switch is the `commands_scope` action on the terminal's thread; mutations emit bridge event `commands`) + `static/js/commands.js` (dialog: Project / Global sections, search, *Run* — fills `/name ` when it takes arguments, else sends —, ⋯ menu with edit / duplicate / copy to the other scope / delete, an editor with placeholder chips, starters and a live *Preview* of the expanded prompt mirroring `expand_template`; an unsaved editor closed by Esc comes back as a "Keep editing" notice). The list feeds the composer's slash menu (*Your commands* first, with the argument hint) and the ⌘K palette via `catalog.js:setCustomItems` / `allItems()`; `/command`, `/commands`, `/command new|edit <name>` open the dialog (`commandPickerArg`); sidebar chip `#open-commands` (the theme / appearance buttons live in the sidebar header). `write_command(argument_hint=None)` keeps the file's hint (TUI editor), a string replaces it. Tests: `tests/test_web_commands.py`.
- **Pets**: a roster of pets (cat · dog · bunny · dragon, which starts as an egg and hatches after 10 turns) lives in a pen docked at the bottom of the sidebar (`tui/pet_pen.py:PetPen`, one active pet). It wanders, naps when idle, paces while a turn runs, plays with toys that appear by themselves (box / butterfly / cup on a shelf), chases a laser dot on hover, runs to clicks; click it to pat. Buttons: `pat · feed · play · nap · trick` and `fish` (20 s catch-the-fish game) · `focus` (25 min pomodoro, pet naps on a keyboard) · `pets` (switch / adopt via `pet_modal.PetAdoptScreen`) · `card` (`pet_modal.PetCardScreen`: portrait, stats, badges, wardrobe, roster). Scenery follows the clock (`sprites.sky`: clouds/moon+stars, December snow, October pumpkin). With the sidebar hidden the pet moves into the composer (`tui/pet_widget.py:PetBuddy` + `PetBubble` overlay in the always-blank row above the composer). `tui/mixins/pet.py:PetMixin` wires it up: turn start/finish (`_begin_turn`/`_turn_done`), tool results with input/output (`console_shim._tool_done` → `pet/events.classify`: tests pass/fail/fixed, commit, push, merge conflict), diffs (`console_shim.file_diff` → `_pet_diff`: lines shipped today, "whoa!" at ≥100 lines), typing, and the 2 s `_slow_refresh` tick (focus timer, rate-limited nudges). Level unlocks accessories (glasses Lv3 while working, party hat 5, scarf 7, crown 10) and growth stages (Lv5, Lv10); badges come from counters (`model.BADGES`). Desktop notifications (`utils/notify.py`) when a long turn ends while unfocused and when focus ends. `/pet …` (`commands/pet.py`) runs on the worker thread and reaches the UI through `pet.set_reaction_hook` / `pet.set_action_hook`; `/new` prints the session recap (`commands/history.py`). State: `~/.config/harness-agent/pet.json` (roster; old single-pet files still load); settings `pet.enabled`, `pet.nudges`, `pet.notify`. **Tests** must not touch the real `pet.json` — `tests/conftest.py` redirects `PET_FILE` and resets hooks/session for every test.

### Adding a new tool

1. Implement the handler function in `jarvis/tools/` (or a subdirectory).
2. Add its JSON schema to `schemas_core.py` (always available) or a new group dict.
3. Register the group in `jarvis/tools/__init__.py` (`TOOL_GROUPS`, `TOOL_NAME_TO_GROUP`, `FUNC`).
4. If specialized, add a regex trigger in `tools/router.py:select_tools()`.
5. Wire the tool name → handler by adding it to the `FUNC` dict in `tools/__init__.py` — this is what `repl/render.py` uses to dispatch `tool_use` blocks.
6. Optional: give it a readable transcript row in `tui/tool_format.py` (`_TITLES`, `tool_args`, `tool_summary`).

**`ask_user_question`**: Structured multiple-choice prompts for the LLM. TUI shows options in `#askbar` above the composer (↑/↓ + Enter or 1–9; space toggles when `allow_multiple`). Blocks in `_SERIAL_TOOLS`; uses `TUIConsole.prompt_ask_user_question` from worker threads.

### Themes and agents

- **Themes**: built-in palettes in `jarvis/tui/theme.py:PALETTES` (opencode — the default —, claude, tokyonight, catppuccin, gruvbox, nord, kimchi, red, blue, purple, green, orange, yellow, rose, slate, ocean, cyberpunk, monochrome, forest, dracula, sunset, dark). Each is also registered as a Textual theme (`textual_theme()`), exposing `$jv-*` CSS variables used by transcript widgets; `state.THEMES` derives entries for every palette. Persisted under `theme` in settings.json; `/theme` previews live.
- **Dialogs**: every modal subclasses `tui/modal_chrome.py:TuiModalScreen` (shared frame, `esc` hint on the title, slide-in, compact inputs). Build list rows with `picker_row()` (name · dim detail · right-aligned meta, `●` for current, query matches accented), group them with `section_header()` (disabled rows — the cursor skips them), and write hints with `hint_line()`; see `model_modal.py` / `session_modal.py` for the reference layout.
- **Mouse**: on by default (wheel scroll, select-to-copy). `HARNESS_MOUSE=0` or settings `ui.mouse: false` restores native terminal selection; `tui/mouse_toggle.py` toggles are no-ops while the app owns the mouse.
- **Agents** (replaced the legacy mode system): User-creatable markdown files. The active agent's body is appended to the system prompt as an addon. Status bar shows the active agent (icon + name + scope hint). Tab key cycles through discovered agents. `/agent` opens the picker; `/agent init` scaffolds `.harness/`. Active agent persisted by name as `agent.active`.

### Adding a new agent

1. Drop a markdown file in `.harness/agents/<name>.md` (project) or `~/.harness/agents/<name>.md` (global) — or run `/agent new <name>`.
2. Required frontmatter: `name` (lowercase-kebab, must match filename), `description`. Optional: `icon` (single emoji), `color` (hex/name), `model` (pin a model).
3. Body markdown below the second `---` is appended to the system prompt when active.
4. `/agent refresh` to pick up new files. `/agent <name>` to activate.

### Adding a new skill

1. Create `.harness/skills/<name>/SKILL.md` (project) or `~/.harness/skills/<name>/SKILL.md` (global).
2. Required frontmatter: `name`, `description`. The directory name MUST equal `name`.
3. Body markdown is the skill content. The LLM auto-invokes via `/skill load <name>` when the description matches the task.

### Adding a custom command

1. Run `/command new <name> [description]` — or drop a markdown file at `.harness/commands/<name>.md` (project) / `~/.harness/commands/<name>.md` (global).
2. Frontmatter is optional (`name`, `description`, `argument-hint`); without it the whole file is the template and the first line becomes the description. If `name` is given it must match the filename stem (lowercase-kebab).
3. The body is the prompt template; `$ARGUMENTS` and `$1`…`$9` are substituted from whatever the user types after `/<name>`.
4. Trigger with `/<name> [args]` (or `/command run <name> [args]`). `/command refresh` re-scans; `/command export|import <name>` moves between scopes.
