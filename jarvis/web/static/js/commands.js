/** Custom commands — the web /command: your own slash commands.
 *
 * A command is a saved prompt: `/review src/app.py` sends its template with
 * `$ARGUMENTS` filled in. The list feeds the composer's slash menu and the ⌘K
 * palette (`catalog.setCustomItems`); the dialog runs, creates, edits, copies
 * and deletes them, with a live preview of what Jarvis receives. Same files
 * as the terminal's /command (jarvis/web/commands_api.py → storage/commands.py).
 */
import { $, escapeHtml, showToast, haptic, debounce, originBadge, copyText, isMac } from './utils.js';
import { icon } from './icons.js';
import { loadSnapshot } from './store.js';
import { openModal, closeModal, isModalOpen } from './modal.js';
import { fetchCommands, extPost, pickerAction } from './api.js';
import { setCustomItems, setCustomRefresher } from './catalog.js';
import { fillPrompt, submitPrompt } from './composer.js';
import { rowBtn, moreBtn, seg, patch, msg, spin, plural, tildify, openMenu, closeMenu } from './extui.js';
import { setView, setHomeSub, toolbar, section as dlgSection, footer, empty, arrowRows } from './dialog.js';

const SCOPES = [
  { value: 'project', label: 'This project', title: 'Only in this folder (.harness/commands)' },
  { value: 'global', label: 'Global', title: 'Every project on this computer (~/.harness/commands)' },
];
const NAME_RE = /^[a-z0-9]+(?:[_-][a-z0-9]+)*$/;
const TOKEN_RE = /\$(ARGUMENTS|[1-9])/g;
const STALE_MS = 20_000;

/** Examples to start from: the empty state and a new command's editor. */
const STARTERS = [
  {
    name: 'review',
    description: 'Review code for bugs and risky changes',
    argument_hint: '[file or folder]',
    body: 'Review $ARGUMENTS for bugs, edge cases and risky changes. If I didn’t name anything, review the current git diff.\n\nList problems by severity with file:line, and suggest a fix for each.',
  },
  {
    name: 'pr-description',
    description: 'Write a pull-request description from the diff',
    argument_hint: '[extra notes]',
    body: 'Look at the current git diff and recent commits, then write a pull-request description: a one-line summary, what changed and why, and how it was tested.',
  },
  {
    name: 'explain',
    description: 'Explain how a piece of code works',
    argument_hint: '<file or function>',
    body: 'Explain how $ARGUMENTS works: what it is for, the main flow step by step, and anything surprising. Keep it short and point to file:line.',
  },
  {
    name: 'write-tests',
    description: 'Write tests for a file or function',
    argument_hint: '<file or function>',
    body: 'Write tests for $ARGUMENTS in the style of the project’s existing tests. Cover the normal path, edge cases and errors, then run them.',
  },
];

let data = null; // last /api/commands
let loadedAt = 0;
let loadError = false;
let loading = null;
let query = '';
let built = ''; // which view's skeleton is in the body: 'list' | 'edit' | ''
let view = null; // the editor: { draft, original, path, scope, errors, saving, tryArgs, armed }
let stash = null; // an unsaved editor the dialog was closed on
let flash = ''; // the row to highlight after a save
const busy = {};

// ─── Data ─────────────────────────────────────────────────────────────────

function toItems(list) {
  return (list?.commands || [])
    .filter((c) => c.active && !c.builtin)
    .map((c) => ({
      group: 'Your commands',
      custom: true,
      name: c.name,
      cmd: c.takes_args ? `/${c.name} ` : `/${c.name}`,
      fill: c.takes_args,
      hint: c.argument_hint || (c.takes_args ? '[args]' : ''),
      label: c.name,
      desc: c.description || 'Your command',
      icon: 'terminal',
      scope: c.scope,
      keys: `custom mine my command template ${c.scope} ${c.tool_label || ''} ${c.argument_hint || ''}`,
    }));
}

function setData(list) {
  if (!list || !Array.isArray(list.commands)) return;
  data = list;
  loadedAt = Date.now();
  loadError = false;
  setCustomItems(toItems(list));
}

export async function refreshCommands() {
  if (loading) return loading;
  loading = (async () => {
    try {
      setData(await fetchCommands());
    } catch {
      if (!data) loadError = true;
    } finally {
      loading = null;
    }
    if (isModalOpen('commands')) render();
  })();
  return loading;
}
const refreshSoon = debounce(refreshCommands, 150);

/** The slash menu is opening: commands made in the terminal show up too. */
function refreshIfStale() {
  if (Date.now() - loadedAt > STALE_MS) refreshCommands();
}

const find = (name) => data?.commands?.find((c) => c.name === name);
const reserved = () => new Set(data?.reserved || []);

// ─── Template expansion (mirrors storage/commands.py:expand_template) ─────

/** shlex.split, close enough for a preview; unbalanced quotes → plain split. */
function splitArgs(s) {
  const out = [];
  let cur = '';
  let quote = '';
  let started = false;
  for (let i = 0; i < s.length; i += 1) {
    const ch = s[i];
    if (quote) {
      if (ch === quote) quote = '';
      else if (ch === '\\' && quote === '"' && i + 1 < s.length) cur += s[(i += 1)];
      else cur += ch;
    } else if (ch === '"' || ch === "'") {
      quote = ch;
      started = true;
    } else if (ch === '\\' && i + 1 < s.length) {
      cur += s[(i += 1)];
      started = true;
    } else if (/\s/.test(ch)) {
      if (started) out.push(cur);
      cur = '';
      started = false;
    } else {
      cur += ch;
      started = true;
    }
  }
  if (quote) return s.split(/\s+/).filter(Boolean);
  if (started) out.push(cur);
  return out;
}

/** Pieces of the expanded prompt: plain text, filled-in values, empty slots. */
function expandParts(body, rawArgs) {
  const args = rawArgs.trim();
  const pos = splitArgs(args);
  const parts = [];
  let last = 0;
  let has = false;
  for (const m of body.matchAll(TOKEN_RE)) {
    has = true;
    if (m.index > last) parts.push({ text: body.slice(last, m.index) });
    const value = m[1] === 'ARGUMENTS' ? args : (pos[Number(m[1]) - 1] ?? '');
    parts.push(value ? { text: value, fill: true } : { text: '', slot: m[0] });
    last = m.index + m[0].length;
  }
  if (last < body.length) parts.push({ text: body.slice(last) });
  // .strip() on the result
  const first = parts.find((p) => p.text);
  if (first && !first.fill) first.text = first.text.replace(/^\s+/, '');
  const end = [...parts].reverse().find((p) => p.text);
  if (end && !end.fill) end.text = end.text.replace(/\s+$/, '');
  if (args && !has) parts.push({ text: '\n\n' }, { text: args, fill: true, appended: true });
  return parts;
}

function previewHtml(body, args) {
  if (!body.trim()) {
    return '<span class="cm-pv-empty">Write the prompt above. This shows exactly what Jarvis receives.</span>';
  }
  return expandParts(body, args).map((p) => {
    if (p.slot) return `<span class="cm-pv-slot" title="Nothing typed for ${p.slot}, so it’s left out">${escapeHtml(p.slot)}</span>`;
    if (p.fill) return `<mark class="cm-pv-fill"${p.appended ? ' title="No placeholder in the prompt, so what you type is added at the end"' : ''}>${escapeHtml(p.text)}</mark>`;
    return escapeHtml(p.text);
  }).join('');
}

// ─── List markup ──────────────────────────────────────────────────────────

function badges(c) {
  const out = [];
  if (c.tool && c.tool !== 'jarvis') out.push(originBadge(c.tool_label, [], tildify(c.source_dir)));
  if (!c.active) out.push('<span class="badge is-warn" title="Global commands are switched off">Hidden</span>');
  return out.join('');
}

function commandRow(c, i) {
  const b = busy[c.name];
  const runnable = c.active && !c.builtin;
  const hint = c.argument_hint ? `<span class="cm-hint">${escapeHtml(c.argument_hint)}</span>` : '';
  return `<div class="ex-skill cm-row${c.active ? '' : ' is-inactive'}${b ? ' is-busy' : ''}${flash === c.name ? ' just-added' : ''}" data-name="${escapeHtml(c.name)}" style="--i:${i}">
    <button type="button" class="ex-skill-main" data-act="edit" data-name="${escapeHtml(c.name)}" aria-label="Edit /${escapeHtml(c.name)}">
      <span class="cm-tile" data-scope="${c.scope}" aria-hidden="true">/</span>
      <span class="pv-text">
        <span class="pv-title"><span class="cm-name">/${escapeHtml(c.name)}</span>${hint}${badges(c)}</span>
        <span class="ex-skill-desc">${escapeHtml(c.description || 'No description')}</span>
        ${c.builtin ? `<span class="ex-sk-warn">${icon('circle-alert')}<span>The built-in /${escapeHtml(c.name)} runs instead. Rename this one to use it.</span></span>` : ''}
      </span>
    </button>
    <div class="pv-side">${b ? `<span class="pv-side-busy">${spin('')}</span>`
      : rowBtn('run', 'Run', { cls: 'is-primary', ic: 'play', data: { name: c.name }, disabled: !runnable, title: runnable ? (c.takes_args ? `Puts /${c.name} in the message box` : `Sends /${c.name} now`) : 'Jarvis can’t run it right now' })}${moreBtn(c.name, `/${c.name}`)}</div>
  </div>`;
}

function starterCards() {
  return `<div class="cm-starters">${STARTERS.map((s, i) => `
    <button type="button" class="cm-starter" data-act="starter" data-i="${i}" style="--i:${i}">
      <span class="cm-starter-top"><span class="cm-name">/${escapeHtml(s.name)}</span><span class="cm-hint">${escapeHtml(s.argument_hint)}</span></span>
      <span class="cm-starter-desc">${escapeHtml(s.description)}</span>
    </button>`).join('')}</div>`;
}

function emptyHtml() {
  return `<div class="cm-empty">
    <span class="cm-empty-art" aria-hidden="true">/</span>
    <strong>Make your first command</strong>
    <p>Save a prompt you use often, then run it from the chat by typing <code>/its-name</code>. Whatever you type after the name goes where the prompt says <code>$ARGUMENTS</code>.</p>
    <div class="pv-actions is-center"><button type="button" class="btn btn-primary" data-act="new">${icon('plus')}<span>New command</span></button></div>
    <div class="cm-starters-label">Or start from an example</div>
    ${starterCards()}
  </div>`;
}

function section(title, rows, dir, offset, shown = '') {
  if (!rows.length) return '';
  const path = dir ? `<code class="cm-sec-path" title="${escapeHtml(dir)}">${escapeHtml(shown || tildify(dir))}</code>` : '';
  return `<section class="cm-sec" aria-label="${escapeHtml(title)}">
    ${dlgSection(title, { count: rows.length, right: path })}
    <div class="ex-list">${rows.map((c, i) => commandRow(c, offset + i)).join('')}</div>
  </section>`;
}

function filtered() {
  const words = query.trim().toLowerCase().split(/\s+/).filter(Boolean);
  const list = data?.commands || [];
  if (!words.length) return list;
  return list.filter((c) => {
    const hay = `/${c.name} ${c.description} ${c.argument_hint} ${c.tool_label} ${c.scope}`.toLowerCase();
    return words.every((w) => hay.includes(w));
  });
}

function listHtml() {
  if (!(data?.commands || []).length) return emptyHtml();
  const rows = filtered();
  if (!rows.length) {
    return empty(`No command matches “${query.trim()}”`, 'Try another word — or make it now.', {
      ic: 'search',
      action: `<button type="button" class="btn" data-act="new-from-query">${icon('plus')}<span>Make /${escapeHtml(slugify(query))}</span></button>`,
    });
  }
  const project = rows.filter((c) => c.scope === 'project');
  const global = rows.filter((c) => c.scope !== 'project');
  // One heading per scope; a global command may live in another tool's folder.
  const globalDir = global.every((c) => c.source_dir === data.global_dir) ? data.global_dir : '';
  return section('This project', project, data.project_dir, 0, '.harness/commands') + section('Global', global, globalDir, project.length);
}

function noticeHtml() {
  const out = [];
  if (stash) {
    const name = stash.draft.name ? `/${stash.draft.name}` : 'a new command';
    out.push(`<p class="pv-msg is-warn cm-stash" role="status">${icon('file-pen')}<span>You didn’t save your changes to <strong>${escapeHtml(name)}</strong>.</span>
      <button type="button" class="btn btn-sm" data-act="stash-open">Keep editing</button>
      <button type="button" class="btn btn-sm btn-quiet" data-act="stash-drop">Discard</button></p>`);
  }
  const hidden = data?.hidden_global_count || 0;
  if (hidden && !data.global_commands) {
    out.push(`<p class="pv-msg is-warn" role="status">${icon('circle-alert')}<span>${plural(hidden, 'global command')} ${hidden === 1 ? 'is' : 'are'} switched off, so typing ${hidden === 1 ? 'it' : 'them'} won’t work.</span>
      <button type="button" class="btn btn-sm" data-act="show-global">Turn on</button></p>`);
  }
  return out.join('');
}

function listFootHtml() {
  const any = (data?.commands || []).length;
  return footer({
    scope: any ? seg([
      { value: 'false', label: 'This project', title: 'Only commands saved in this folder' },
      { value: 'true', label: 'Project + global', title: 'Also commands saved for every project' },
    ], String(!!data.global_commands), { label: 'Which commands Jarvis uses', act: 'global' }) : '',
    note: 'Type <kbd>/</kbd> in the chat to run one',
    hints: [['↑ ↓', 'move'], ['↵', 'edit'], ['esc', 'close']],
  });
}

/** Search + "New command" — only on the list (the editor uses the header's back button). */
function renderBar() {
  const bar = $('commands-bar');
  if (!bar) return;
  const onList = !view && !!data;
  bar.hidden = !onList;
  if (onList && !$('cm-search')) {
    bar.innerHTML = toolbar({ id: 'cm-search', placeholder: 'Search your commands', value: query, action: { act: 'new', label: 'New command', ic: 'plus' } });
  }
}

function buildList(body) {
  body.innerHTML = `
    <div id="cm-notice" class="cm-notice"></div>
    <div id="cm-list" class="cm-list"></div>`;
  built = 'list';
}

function renderList() {
  const body = $('commands-body');
  setView('commands', null);
  renderBar();
  if (built !== 'list' || !$('cm-list')) buildList(body);
  const list = data.commands || [];
  const project = list.filter((c) => c.scope === 'project').length;
  setHomeSub('commands', list.length
    ? `${plural(list.length, 'command')} · ${project} in this project`
    : 'Prompts you reuse, one /name away');
  patch($('cm-notice'), noticeHtml());
  patch($('cm-list'), listHtml());
  patch($('commands-foot'), listFootHtml());
  if (flash) {
    const name = flash;
    flash = '';
    requestAnimationFrame(() => $('cm-list')?.querySelector(`[data-name="${CSS.escape(name)}"]`)?.scrollIntoView({ block: 'nearest', behavior: 'smooth' }));
  }
}

// ─── Editor ───────────────────────────────────────────────────────────────

const blank = () => ({ name: '', description: '', argument_hint: '', body: '' });

function slugify(text) {
  return String(text || '').trim().toLowerCase().replace(/^\/+/, '').replace(/[^a-z0-9_-]+/g, '-').replace(/-{2,}/g, '-').replace(/^[-_]+|[-_]+$/g, '').slice(0, 64);
}

function startEditor({ command = null, draft = null, scope = '' } = {}) {
  const original = command
    ? { name: command.name, description: command.description || '', argument_hint: command.argument_hint || '', body: command.body || '' }
    : blank();
  view = {
    draft: { ...original, ...(draft || {}) },
    original,
    path: command?.path || '',
    scope: command?.scope || scope || 'project',
    command,
    errors: {},
    saving: false,
    tryArgs: '',
    armed: '',
  };
  built = '';
  stash = null;
  render();
  requestAnimationFrame(() => {
    const target = !view?.draft.name ? $('cm-name') : $('cm-body');
    target?.focus({ preventScroll: true });
    if (target === $('cm-body')) target.setSelectionRange(target.value.length, target.value.length);
  });
}

const dirty = () => !!view && JSON.stringify(view.draft) !== JSON.stringify(view.original);

function nameProblem(name) {
  if (!name) return '';
  if (name.length > 64) return 'Keep it under 64 characters.';
  if (!NAME_RE.test(name)) return 'Use lowercase letters, numbers and dashes, starting and ending with a letter or number.';
  if (reserved().has(name)) return `/${name} is a built-in command. Pick another name.`;
  const clash = (data?.commands || []).find((c) => c.name === name && c.path !== view.path);
  if (clash) return `/${name} already exists (${clash.scope === 'project' ? 'this project' : 'global'}). Pick another name, or edit that one.`;
  return '';
}

function canSave() {
  const d = view.draft;
  return !view.saving && !!d.name && !nameProblem(d.name) && !!d.body.trim() && (dirty() || !view.path);
}

function nameHelp() {
  const d = view.draft;
  const problem = view.errors.name || nameProblem(d.name);
  if (problem) return { text: problem, error: true };
  if (!d.name) return { text: 'Short and memorable: you’ll type it after a slash.' };
  return { text: `Type /${d.name} in the message box to run it.`, ok: true };
}

function placeholdersUsed(body) {
  return [...new Set([...body.matchAll(TOKEN_RE)].map((m) => m[0]))];
}

function bodyHelp() {
  const d = view.draft;
  if (view.errors.body) return { text: view.errors.body, error: true };
  const used = placeholdersUsed(d.body);
  if (!d.body.trim()) return { text: 'This is the message Jarvis gets. Put $ARGUMENTS where your words should go.' };
  if (!used.length) return { text: 'No placeholder, so anything typed after the name is added at the end.' };
  const all = used.includes('$ARGUMENTS');
  const words = used.filter((t) => t !== '$ARGUMENTS').sort();
  const parts = [];
  if (all) parts.push('$ARGUMENTS is everything typed after the name');
  if (words.length) parts.push(`${words.join(', ')} ${words.length === 1 ? 'is' : 'are'} single words in order ("quote" several words to keep them together)`);
  return { text: `${parts.join('; ')}.`, ok: true };
}

function pathText() {
  const d = view.draft;
  const name = d.name && NAME_RE.test(d.name) ? d.name : 'name';
  const shown = (dir) => {
    const root = String(data?.project_dir || '').replace(/\/\.harness\/commands$/, '');
    return root && dir.startsWith(`${root}/`) ? dir.slice(root.length + 1) : tildify(dir);
  };
  if (view.path) {
    const dir = view.path.replace(/\/[^/]*$/, '');
    return `Saved in <code title="${escapeHtml(dir)}">${escapeHtml(shown(dir))}/${escapeHtml(name)}.md</code>`;
  }
  const dir = (view.scope === 'global' ? data?.global_dir : data?.project_dir) || '';
  return `Saves to <code title="${escapeHtml(dir)}">${escapeHtml(dir ? shown(dir) : '.harness/commands')}/${escapeHtml(name)}.md</code>${view.scope === 'global' ? ' · every project' : ' · only this folder'}`;
}

function helpLine(h) {
  return `<span class="cm-help${h.error ? ' is-error' : h.ok ? ' is-ok' : ''}">${h.error ? icon('circle-alert') : h.ok ? icon('circle-check') : ''}<span>${escapeHtml(h.text)}</span></span>`;
}

function actionsHtml() {
  const v = view;
  const saveKey = isMac ? '⌘↵' : 'Ctrl+↵';
  const del = v.path
    ? `<button type="button" class="btn btn-quiet cm-del${v.armed === 'delete' ? ' is-asking' : ''}" data-act="delete-current">${icon('trash-2')}<span>${v.armed === 'delete' ? 'Click again to delete' : 'Delete'}</span></button>`
    : '';
  const cancel = v.armed === 'discard'
    ? `<button type="button" class="btn btn-quiet is-asking" data-act="back">Discard changes?</button>`
    : `<button type="button" class="btn btn-quiet" data-act="back">Cancel</button>`;
  return `${del}<span class="cm-spacer"></span>${cancel}
    <button type="button" class="btn btn-primary" data-act="save" title="Save (${saveKey})"${canSave() ? '' : ' disabled'}>${v.saving ? spin('Saving…') : `${icon('check')}<span>${v.path ? 'Save changes' : 'Save command'}</span>`}</button>`;
}

function insertChips() {
  return ['$ARGUMENTS', '$1', '$2', '$3'].map((t) => `<button type="button" class="cm-token" data-act="insert" data-tok="${t}" title="Insert ${t} at the cursor">${t}</button>`).join('');
}

function buildEditor(body) {
  const v = view;
  const fresh = !v.path;
  setView('commands', {
    title: fresh ? 'New command' : `Edit /${v.original.name}`,
    sub: fresh ? 'A prompt you can run with /name' : 'Changes apply the next time you run it',
    back: () => leaveEditor(),
    backLabel: 'commands',
  });
  renderBar();
  body.innerHTML = `
    ${v.command ? `<div class="cm-ed-head">${badges(v.command)}</div>` : ''}
    <div id="cm-ed-starters"></div>
    <div class="cm-form">
      <div class="cm-fieldset">
        <label class="cm-lbl" for="cm-name">Name</label>
        <div class="pv-field cm-name-field"><span class="cm-slash" aria-hidden="true">/</span>
          <input id="cm-name" class="pv-input" placeholder="my-command" maxlength="64" aria-describedby="cm-name-help"
            autocomplete="off" autocapitalize="off" autocorrect="off" spellcheck="false" data-1p-ignore data-lpignore="true" data-form-type="other" enterkeyhint="next"></div>
        <div id="cm-name-help" class="cm-help-row"></div>
      </div>
      <div class="cm-fieldset">
        <label class="cm-lbl" for="cm-desc">Description <em>shown next to it in the / menu</em></label>
        <div class="pv-field"><input id="cm-desc" class="pv-input is-plain" placeholder="What it does, in a few words" maxlength="300"
          autocomplete="off" data-1p-ignore data-lpignore="true" data-form-type="other" enterkeyhint="next"></div>
      </div>
      <div class="cm-fieldset">
        <div class="cm-lbl-row"><label class="cm-lbl" for="cm-body">Prompt</label><span class="cm-tokens" role="group" aria-label="Insert a placeholder">${insertChips()}</span></div>
        <textarea id="cm-body" class="cm-body" rows="7" spellcheck="true" aria-describedby="cm-body-help"
          placeholder="Review $ARGUMENTS for bugs and risky changes. List problems by severity with file:line."></textarea>
        <div id="cm-body-help" class="cm-help-row"></div>
      </div>
      <div class="cm-fieldset">
        <label class="cm-lbl" for="cm-hint">Argument hint <em>optional · reminds you what to type</em></label>
        <div class="pv-field"><input id="cm-hint" class="pv-input" placeholder="[file] [notes]" maxlength="120"
          autocomplete="off" autocapitalize="off" autocorrect="off" spellcheck="false" data-1p-ignore data-lpignore="true" data-form-type="other" enterkeyhint="done"></div>
      </div>
      <div class="cm-fieldset cm-where">
        ${fresh ? `<span class="cm-lbl">Save for</span><div id="cm-scope"></div>` : ''}
        <p class="ex-path" id="cm-path"></p>
      </div>
    </div>
    <section class="cm-try" aria-label="Preview">
      <div class="cm-try-head"><strong>Preview</strong><span>What Jarvis receives when you run it</span></div>
      <label class="pv-field cm-try-field"><span class="cm-try-cmd" id="cm-try-cmd"></span>
        <input id="cm-try" class="pv-input" placeholder="type example words…" aria-label="Example words after the command"
          autocomplete="off" autocapitalize="off" autocorrect="off" spellcheck="false" data-1p-ignore data-lpignore="true" data-form-type="other"></label>
      <div class="cm-preview" id="cm-preview" aria-live="polite"></div>
    </section>
`;
  $('cm-name').value = v.draft.name;
  $('cm-desc').value = v.draft.description;
  $('cm-body').value = v.draft.body;
  $('cm-hint').value = v.draft.argument_hint;
  $('cm-try').value = v.tryArgs;
  growBody();
  built = 'edit';
}

function growBody() {
  const ta = $('cm-body');
  if (!ta) return;
  ta.style.height = 'auto';
  if (ta.scrollHeight) ta.style.height = `${Math.min(Math.max(ta.scrollHeight + 2, 150), 380)}px`;
}

function renderEditor() {
  const body = $('commands-body');
  if (built !== 'edit' || !$('cm-name')) buildEditor(body);
  const v = view;
  const d = v.draft;
  patch($('cm-ed-starters'), !v.path && !d.body.trim()
    ? `<div class="cm-ed-starters"><span>Start from an example</span>${STARTERS.map((s, i) => `<button type="button" class="pv-chip ex-chip" data-act="starter" data-i="${i}" title="${escapeHtml(s.description)}"><span class="cm-name">/${escapeHtml(s.name)}</span></button>`).join('')}</div>`
    : '');
  $('cm-name').closest('.pv-field').classList.toggle('is-invalid', !!(v.errors.name || nameProblem(d.name)));
  patch($('cm-name-help'), helpLine(nameHelp()));
  patch($('cm-body-help'), helpLine(bodyHelp()));
  if ($('cm-scope')) patch($('cm-scope'), seg(SCOPES, v.scope, { label: 'Where to save it', act: 'scope' }));
  patch($('cm-path'), pathText());
  const tryCmd = $('cm-try-cmd');
  const shown = `/${d.name || 'name'}`;
  if (tryCmd.textContent !== shown) tryCmd.textContent = shown;
  $('cm-try').placeholder = d.argument_hint || 'type example words…';
  patch($('cm-preview'), previewHtml(d.body, v.tryArgs));
  // The editor's buttons live in the footer, so they never scroll away.
  patch($('commands-foot'), `<div class="cm-actions">${actionsHtml()}</div>`);
}

function render() {
  const body = $('commands-body');
  if (!body) return;
  if (!data) {
    built = '';
    renderBar();
    patch($('commands-foot'), '');
    body.innerHTML = loadError
      ? empty('Could not load your commands', 'Check that Jarvis is still running, then try again.', {
        ic: 'circle-alert', action: `<button type="button" class="btn" data-act="reload">${icon('refresh-cw')}<span>Try again</span></button>`,
      })
      : `<div class="list-loading">${'<div class="skeleton"></div>'.repeat(4)}</div>`;
    return;
  }
  if (view) renderEditor();
  else renderList();
}

async function save() {
  if (!view || !canSave()) return;
  const v = view;
  v.saving = true;
  v.errors = {};
  renderEditor();
  const d = v.draft;
  const res = await extPost('commands/save', {
    name: d.name,
    description: d.description,
    argument_hint: d.argument_hint,
    body: d.body,
    scope: v.scope,
    path: v.path,
  });
  if (res.list) setData(res.list);
  if (view !== v) return;
  v.saving = false;
  if (!res.ok) {
    v.errors = { [res.field === 'body' ? 'body' : 'name']: res.error || 'Could not save it' };
    renderEditor();
    showToast(res.error || 'Could not save it', true);
    return;
  }
  haptic(10);
  showToast(res.created ? `Saved. Type /${res.name} to run it` : `Saved /${res.name}`);
  flash = res.name;
  view = null;
  built = '';
  render();
}

function leaveEditor({ force = false } = {}) {
  if (!view) return;
  if (!force && dirty() && view.armed !== 'discard') {
    view.armed = 'discard';
    renderEditor();
    showToast('You have unsaved changes — go back again to discard them');
    return;
  }
  view = null;
  built = '';
  render();
  requestAnimationFrame(() => $('cm-search')?.focus({ preventScroll: true }));
}

// ─── Actions ──────────────────────────────────────────────────────────────

function run(name) {
  const c = find(name);
  if (!c || !c.active || c.builtin) return;
  closeCommands();
  if (c.takes_args) {
    fillPrompt(`/${c.name} `);
    showToast(`Add ${c.argument_hint || 'your words'} and press Enter`);
  } else {
    submitPrompt(`/${c.name}`);
  }
}

async function act(name, kind, path, payload, okText) {
  busy[name] = kind;
  render();
  const res = await extPost(path, payload);
  delete busy[name];
  if (res.list) setData(res.list);
  if (res.ok) {
    haptic(10);
    showToast(typeof okText === 'function' ? okText(res) : okText);
  } else {
    showToast(res.error || 'That did not work', true);
  }
  render();
  return res;
}

async function menuFor(name, anchor) {
  const c = find(name);
  if (!c) return;
  const other = c.scope === 'project' ? 'global' : 'project';
  const items = [
    { key: 'run', label: c.takes_args ? 'Run (fill the message box)' : 'Run now', icon: 'play', disabled: !c.active || c.builtin, reason: c.builtin ? 'A built-in command has this name' : 'Global commands are off' },
    { key: 'edit', label: 'Edit', icon: 'file-pen' },
    { key: 'duplicate', label: 'Duplicate', icon: 'copy' },
    { key: 'copy-to', label: other === 'global' ? 'Copy to global' : 'Copy to this project', icon: 'arrow-right-left' },
    { key: 'copy-name', label: `Copy “/${c.name}”`, icon: 'clipboard-paste' },
    { divider: true },
    { key: 'delete', label: 'Delete', icon: 'trash-2', danger: true, confirm: true },
  ];
  const key = await openMenu(anchor, items);
  if (!key) return;
  if (key === 'run') run(name);
  else if (key === 'edit') startEditor({ command: c });
  else if (key === 'duplicate') {
    const taken = new Set((data?.commands || []).map((x) => x.name));
    let copy = `${c.name}-copy`;
    for (let n = 2; taken.has(copy); n += 1) copy = `${c.name}-copy-${n}`;
    startEditor({ draft: { name: copy, description: c.description, argument_hint: c.argument_hint, body: c.body }, scope: c.scope });
  } else if (key === 'copy-to') {
    act(name, 'copy', 'commands/copy', { name, scope: other }, other === 'global' ? `Copied /${name} to global` : `Copied /${name} into this project`);
  } else if (key === 'copy-name') {
    showToast(await copyText(`/${c.name}`) ? `Copied /${c.name}` : 'Copy failed', false);
  } else if (key === 'delete') {
    act(name, 'delete', 'commands/delete', { name, path: c.path }, `Deleted /${name}`);
  }
}

async function deleteCurrent() {
  const v = view;
  if (!v?.path) return;
  if (v.armed !== 'delete') {
    v.armed = 'delete';
    renderEditor();
    return;
  }
  const name = v.original.name;
  view = null;
  built = '';
  await act(name, 'delete', 'commands/delete', { name, path: v.path }, `Deleted /${name}`);
}

async function setGlobal(on) {
  const res = await pickerAction('commands_scope', { global_commands: on });
  if (res.state) loadSnapshot(res.state);
  if (!res.ok) showToast(res.error || 'Could not change it', true);
  else showToast(on ? 'Global commands are on' : 'Only this project’s commands now');
  refreshCommands();
}

function insertToken(tok) {
  const ta = $('cm-body');
  if (!ta || !view) return;
  ta.focus();
  const { selectionStart: a, selectionEnd: b, value } = ta;
  const before = value.slice(0, a);
  const pad = before && !/\s$/.test(before) ? ' ' : '';
  // execCommand keeps ⌘Z working; fall back to a plain splice.
  if (!document.execCommand?.('insertText', false, pad + tok)) {
    ta.value = before + pad + tok + value.slice(b);
    ta.setSelectionRange(a + pad.length + tok.length, a + pad.length + tok.length);
  }
  onEditorInput(ta);
}

// ─── Events ───────────────────────────────────────────────────────────────

function onEditorInput(el) {
  const v = view;
  if (!v) return;
  if (el.id === 'cm-name') {
    // Lowercase, no leading slash, spaces → dashes, as you type.
    const clean = el.value.toLowerCase().replace(/^\/+/, '').replace(/\s+/g, '-');
    if (clean !== el.value) {
      const at = el.selectionStart - (el.value.length - clean.length);
      el.value = clean;
      el.setSelectionRange(Math.max(0, at), Math.max(0, at));
    }
    v.draft.name = clean;
    delete v.errors.name;
  } else if (el.id === 'cm-desc') v.draft.description = el.value;
  else if (el.id === 'cm-body') {
    v.draft.body = el.value;
    delete v.errors.body;
    growBody();
  } else if (el.id === 'cm-hint') v.draft.argument_hint = el.value;
  else if (el.id === 'cm-try') v.tryArgs = el.value;
  else return;
  if (v.armed) v.armed = '';
  renderEditor();
}

function handleClick(e) {
  const el = e.target.closest('[data-act]');
  const card = $('commands');
  if (!el || !card?.contains(el)) return;
  const name = el.dataset.name || el.closest('[data-name]')?.dataset.name || '';
  switch (el.dataset.act) {
    case 'new': startEditor(); break;
    case 'new-from-query': startEditor({ draft: { name: slugify(query) } }); break;
    case 'starter': {
      const s = STARTERS[Number(el.dataset.i)];
      if (!s) break;
      const taken = new Set((data?.commands || []).map((x) => x.name));
      const draft = { ...s, name: taken.has(s.name) ? `${s.name}-2` : s.name };
      if (view && !view.path) {
        // Keep a name the user already typed.
        if (view.draft.name) draft.name = view.draft.name;
        Object.assign(view.draft, draft);
        built = '';
        renderEditor();
        $('cm-body')?.focus({ preventScroll: true });
      } else {
        startEditor({ draft });
      }
      break;
    }
    case 'edit': {
      const c = find(name);
      if (c) startEditor({ command: c });
      break;
    }
    case 'run': run(name); break;
    case 'menu': menuFor(name, el); break;
    case 'back': leaveEditor(); break;
    case 'save': save(); break;
    case 'delete-current': deleteCurrent(); break;
    case 'insert': insertToken(el.dataset.tok); break;
    case 'scope':
      if (view) {
        view.scope = el.dataset.val;
        renderEditor();
      }
      break;
    case 'global': {
      const on = el.dataset.val === 'true';
      if (on !== !!data?.global_commands) setGlobal(on);
      break;
    }
    case 'show-global': setGlobal(true); break;
    case 'stash-open': {
      const s = stash;
      stash = null;
      view = s;
      built = '';
      render();
      break;
    }
    case 'stash-drop':
      stash = null;
      render();
      break;
    case 'reload': refreshCommands(); break;
    default:
  }
}

function handleInput(e) {
  const el = e.target;
  if (el.id === 'cm-search') {
    query = el.value;
    patch($('cm-list'), listHtml());
    return;
  }
  onEditorInput(el);
}

function handleKey(e) {
  if (e.isComposing) return;
  const el = e.target;
  if (!view && arrowRows(e, $('cm-search'), [...($('cm-list')?.querySelectorAll('.ex-skill-main') || [])])) return;
  if (view && e.key === 'Enter' && (e.metaKey || e.ctrlKey)) {
    e.preventDefault();
    save();
    return;
  }
  if (e.key !== 'Enter' || e.shiftKey) return;
  if (el.id === 'cm-search') {
    e.preventDefault();
    const first = $('cm-list')?.querySelector('[data-act="edit"], [data-name]');
    const c = first ? find(first.dataset.name || first.closest('[data-name]')?.dataset.name || '') : null;
    if (c && query.trim()) startEditor({ command: c });
    return;
  }
  if (!view || el.tagName !== 'INPUT') return;
  e.preventDefault();
  // Enter walks name → description → prompt; in the last fields it saves.
  if (el.id === 'cm-name') $('cm-desc')?.focus();
  else if (el.id === 'cm-desc') $('cm-body')?.focus();
  else if (canSave()) save();
}

// ─── Open / close ─────────────────────────────────────────────────────────

/** `arg`: '' (the list), 'new [name]', or 'edit|show|open <name>'. */
export async function openCommands(arg = '') {
  const [verb = '', ...rest] = String(arg || '').trim().split(/\s+/);
  const target = slugify(rest.join(' '));
  if (!view) built = '';
  render();
  openModal('commands', {
    focus: undefined,
    onClose: () => {
      closeMenu();
      if (view && dirty()) stash = { ...view, armed: '', saving: false };
      view = null;
      built = '';
      query = '';
    },
  });
  const verbL = verb.toLowerCase();
  if (!data || Date.now() - loadedAt > 2000) await refreshCommands();
  if (!isModalOpen('commands')) return;
  if (['new', 'add', 'create'].includes(verbL)) {
    startEditor({ draft: target ? { name: target } : null });
  } else if (['edit', 'show', 'open'].includes(verbL) && target) {
    const c = find(target);
    if (c) startEditor({ command: c });
    else startEditor({ draft: { name: target } });
  } else {
    render();
    requestAnimationFrame(() => $('cm-search')?.focus({ preventScroll: true }));
  }
}

export function closeCommands() {
  closeModal('commands');
}

export function handleCommandsEvent() {
  refreshSoon();
}

export function initCommands() {
  const card = $('commands');
  card?.addEventListener('click', handleClick);
  card?.addEventListener('input', handleInput);
  card?.addEventListener('keydown', handleKey);
  setCustomRefresher(refreshIfStale);
  refreshCommands();
}
