/** Transcript rendering: user bubbles and agent turns (text, thinking, tools, diffs).
 *
 * Agent output is grouped into turns: everything between two user messages
 * lands in one `.turn-agent` with a single avatar. Live streams render into
 * a bubble that is finalised in place when the committed message arrives, so
 * nothing flickers or jumps.
 *
 * Sent messages are never edited: every prompt is a new message. Only live
 * entries animate in (`is-new`); a snapshot re-render stays still.
 */
import { $, escapeHtml, copyText, flashDone, truncate, showToast, animateEl, SPRING } from './utils.js';
import { icon } from './icons.js';
import { store } from './store.js';
import { fetchToolOutput } from './api.js';
import { openModal } from './modal.js';
import { renderMarkdown, applyMarkdownLinks } from './markdown.js';
import { openChange } from './changes.js';
import { renderFiles, renderToolImages } from './media.js';

const chat = () => $('chat');
const scroller = () => $('chat-scroll');

/** Long user messages fold behind "Show more" past these. */
const FOLD_LINES = 9;
const FOLD_CHARS = 700;
/** A tool group folds once it has this many rows; the newest 3 stay visible (chat.css). */
const TOOL_FOLD_AT = 6;
const TOOL_FOLD_KEEP = 3;

/** tool id → row element (live + snapshot rows) */
const toolRows = new Map();
/** Active stream: { kind, el, body, buffer, raf } */
let live = null;
let stickToBottom = true;
let lastSnapshotSig = null;
/** Session the transcript on screen belongs to (undefined before the first snapshot). */
let lastSnapshotSession;
/** True while a snapshot is being rendered: nothing animates or counts as new. */
let restoring = false;
/** Items added while scrolled up — shown on the "Latest" button. */
let unseen = 0;

export function normalizeRole(role) {
  const r = String(role || 'assistant').toLowerCase();
  if (r === 'user' || r === 'you') return 'you';
  if (['assistant', 'thinking', 'log', 'system', 'tool', 'diff'].includes(r)) return r;
  return 'assistant';
}

// ─── Scrolling ────────────────────────────────────────────────────────────

export function initChat() {
  const sc = scroller();
  const jump = $('jump-btn');
  sc?.addEventListener('scroll', () => {
    const dist = sc.scrollHeight - sc.scrollTop - sc.clientHeight;
    stickToBottom = dist < 60;
    if (stickToBottom) {
      hideJump();
    } else if (dist > 240) {
      jump.hidden = false;
    }
  }, { passive: true });
  jump?.addEventListener('click', () => scrollToBottom(true, true));
  syncThoughtsVisibility();
  initToolOutputModal();
  // Tool rows are also clickable targets for the Activity tab's "jump here".
  sc?.addEventListener('click', (e) => {
    const btn = e.target.closest?.('[data-full]');
    if (btn?.dataset.full) {
      e.stopPropagation();
      openToolOutput(btn.dataset.full);
    }
  });
}

function hideJump() {
  const jump = $('jump-btn');
  if (!jump) return;
  jump.hidden = true;
  jump.classList.remove('has-new');
  unseen = 0;
  paintUnseen();
}

function paintUnseen() {
  const badge = $('jump-count');
  if (!badge) return;
  const was = badge.textContent;
  badge.hidden = unseen < 1;
  badge.textContent = unseen > 99 ? '99+' : String(unseen);
  $('jump-btn')?.classList.toggle('has-count', unseen > 0);
  if (unseen > 0 && was !== badge.textContent) {
    animateEl(badge, [{ transform: 'scale(0.6)' }, { transform: 'scale(1.18)' }, { transform: 'none' }], { duration: 360, easing: SPRING });
  }
}

/** Something new landed in the transcript (live only). */
function noteUnseen() {
  if (restoring || stickToBottom) return;
  unseen += 1;
  paintUnseen();
}

/**
 * Only live entries animate in. The class is dropped once every entrance
 * (children included) has played, so re-showing the element later — a
 * folded tool group, thoughts toggled back on — doesn't replay it.
 */
function markNew(el) {
  if (restoring || !el) return el;
  el.classList.add('is-new');
  setTimeout(() => el.classList.remove('is-new'), 1200);
  return el;
}

export function scrollToBottom(force = false, smooth = false) {
  const sc = scroller();
  if (!sc) return;
  if (!force && !stickToBottom) {
    $('jump-btn')?.classList.add('has-new');
    return;
  }
  stickToBottom = true;
  sc.scrollTo({ top: sc.scrollHeight, behavior: smooth ? 'smooth' : 'auto' });
  hideJump();
}

// ─── Structure helpers ────────────────────────────────────────────────────

function isAgentTurn(el) {
  return el?.classList?.contains('turn-agent');
}

/** The body of the current agent turn, creating a new turn when needed. */
function agentBody() {
  const root = chat();
  // The "working" placeholder turn becomes the real one, so its avatar
  // stays put instead of vanishing and popping in again.
  const pending = document.getElementById('typing-turn');
  if (pending && pending === root.lastElementChild) {
    pending.removeAttribute('id');
    document.getElementById('typing')?.remove();
    return pending.querySelector('.agent-body');
  }
  removeTyping();
  const last = root.lastElementChild;
  if (isAgentTurn(last)) return last.querySelector('.agent-body');
  const turn = document.createElement('div');
  turn.className = 'turn-agent';
  turn.innerHTML = '<span class="mark" aria-hidden="true"></span><div class="agent-body"></div>';
  markNew(turn);
  root.appendChild(turn);
  return turn.querySelector('.agent-body');
}

/** A turn just finished: the avatar gives one soft ring. */
export function markTurnDone() {
  const turns = chat()?.querySelectorAll(':scope > .turn-agent:not(#typing-turn)');
  const mark = turns?.[turns.length - 1]?.querySelector(':scope > .mark');
  if (!mark) return;
  mark.classList.remove('is-done');
  requestAnimationFrame(() => mark.classList.add('is-done'));
  setTimeout(() => mark.classList.remove('is-done'), 1300);
}

function removeTyping() {
  document.getElementById('typing-turn')?.remove();
  document.getElementById('typing')?.remove();
}

/** Show the "working" dots while busy and nothing else is visibly moving. */
export function syncTyping() {
  const root = chat();
  if (!root) return;
  const running = [...toolRows.values()].some((r) => r.isConnected && r.dataset.status === 'running');
  const want = store.busy && store.connected && !live && !running && !store.activePrompt;
  if (!want) {
    removeTyping();
    return;
  }
  const dots = document.getElementById('typing');
  if (dots && !dots.nextElementSibling && dots.closest('.turn-agent') === root.lastElementChild) return;

  removeTyping();
  const el = document.createElement('div');
  el.id = 'typing';
  el.className = 'typing';
  el.setAttribute('role', 'status');
  el.setAttribute('aria-label', 'Jarvis is working');
  el.innerHTML = '<i></i><i></i><i></i>';
  const last = root.lastElementChild;
  if (isAgentTurn(last)) {
    last.querySelector('.agent-body').appendChild(el);
  } else {
    const turn = document.createElement('div');
    turn.className = 'turn-agent';
    turn.id = 'typing-turn';
    turn.innerHTML = '<span class="mark" aria-hidden="true"></span><div class="agent-body"></div>';
    turn.querySelector('.agent-body').appendChild(el);
    root.appendChild(turn);
  }
  scrollToBottom();
}

function afterAppend({ scroll = true } = {}) {
  syncTyping();
  if (scroll) scrollToBottom();
}

// ─── Entries ──────────────────────────────────────────────────────────────

function actionButton(iconName, label, onClick) {
  const btn = document.createElement('button');
  btn.type = 'button';
  btn.className = 'act-btn';
  btn.innerHTML = `${icon(iconName)}<span>${escapeHtml(label)}</span>`;
  btn.addEventListener('click', (e) => {
    e.stopPropagation();
    onClick(btn);
  });
  return btn;
}

function copyAction(getText) {
  return actionButton('copy', 'Copy', async (btn) => {
    if (await copyText(getText())) flashDone(btn);
  });
}

function appendUser(text, files = []) {
  removeTyping();
  const row = document.createElement('div');
  row.className = 'turn-you';
  if (files.length) {
    // Photos and files sit above the text, like a message with attachments.
    row.classList.add('has-files');
    row.appendChild(renderFiles(files));
    if (!text) {
      markNew(row);
      chat().appendChild(row);
      return;
    }
  }
  const bubble = document.createElement('div');
  const isCommand = /^[/!]\S/.test(text) && !text.includes('\n');
  bubble.className = `bubble-you${isCommand ? ' is-command' : ''}`;
  const body = document.createElement('div');
  body.className = 'you-body md';
  if (isCommand) body.textContent = text;
  else {
    body.innerHTML = renderMarkdown(text);
    applyMarkdownLinks(body);
  }
  bubble.appendChild(body);
  const actions = document.createElement('div');
  actions.className = 'msg-actions';
  actions.append(copyAction(() => text));
  row.append(bubble, actions);
  markNew(row);
  chat().appendChild(row);
  if (!isCommand && (text.split('\n').length > FOLD_LINES || text.length > FOLD_CHARS)) foldUser(bubble, body);
}

/** Long pasted prompts fold to a few lines; the toggle sits inside the bubble. */
function foldUser(bubble, body) {
  bubble.classList.add('is-long', 'is-folded');
  const more = document.createElement('button');
  more.type = 'button';
  more.className = 'you-more';
  more.setAttribute('aria-expanded', 'false');
  more.innerHTML = `<span>Show more</span>${icon('chevron-down')}`;
  more.addEventListener('click', (e) => {
    e.stopPropagation();
    const folded = bubble.classList.toggle('is-folded');
    more.setAttribute('aria-expanded', String(!folded));
    more.querySelector('span').textContent = folded ? 'Show more' : 'Show less';
  });
  bubble.appendChild(more);
  // Length is only a hint: a long single line can still fit a wide screen.
  requestAnimationFrame(() => {
    if (body.isConnected && body.scrollHeight <= body.clientHeight + 2) {
      bubble.classList.remove('is-long', 'is-folded');
      more.remove();
    }
  });
}

function makeAgentText(text, { title = '', liveStream = false } = {}) {
  const wrap = document.createElement('div');
  wrap.className = 'agent-text';
  const bubble = document.createElement('div');
  bubble.className = `bubble-agent${liveStream ? ' is-live' : ''}`;
  const plan = /proposed plan/i.test(title || '');
  bubble.innerHTML = `${plan ? `<div class="plan-title">${icon('map')}<span>Proposed plan</span></div>` : ''}<div class="md"></div>`;
  const body = bubble.querySelector('.md');
  if (text) {
    body.innerHTML = renderMarkdown(text, { streaming: liveStream });
    applyMarkdownLinks(body);
  }
  wrap.dataset.raw = text || '';
  const actions = document.createElement('div');
  actions.className = 'msg-actions';
  actions.appendChild(copyAction(() => wrap.dataset.raw || ''));
  if (liveStream) actions.hidden = true;
  wrap.append(bubble, actions);
  return { wrap, bubble, body, actions };
}

function thinkingPreview(text) {
  const line = String(text || '').replace(/\s+/g, ' ').trim();
  return truncate(line, 120);
}

function makeThinking(text, { liveStream = false } = {}) {
  const el = document.createElement('div');
  el.className = `think${liveStream ? ' is-live is-open' : ''}`;
  el.innerHTML = `
    <button type="button" class="think-head" aria-expanded="${liveStream}">
      ${icon('brain')}
      <strong>${liveStream ? 'Thinking' : 'Thought'}</strong>
      <span class="think-preview"></span>
      <span class="think-chev">${icon('chevron-right')}</span>
    </button>
    <div class="fold"><div class="fold-inner"><div class="think-body"></div></div></div>`;
  markNew(el);
  const body = el.querySelector('.think-body');
  body.textContent = text || '';
  el.querySelector('.think-preview').textContent = liveStream ? '' : thinkingPreview(text);
  el.querySelector('.think-head').addEventListener('click', () => {
    const open = el.classList.toggle('is-open');
    el.querySelector('.think-head').setAttribute('aria-expanded', String(open));
  });
  return { el, body };
}

function appendAssistant(text, title) {
  const { wrap } = makeAgentText(text, { title });
  agentBody().appendChild(markNew(wrap));
}

function appendThinking(text) {
  const body = agentBody();
  // The same thought can arrive twice (stream finalize + reply commit).
  const seen = [...body.querySelectorAll(':scope > .think .think-body')].some((el) => el.textContent.trim() === text);
  if (seen) return;
  body.appendChild(makeThinking(text).el);
}

function appendNotice(text) {
  removeTyping();
  const el = document.createElement('div');
  const multi = text.includes('\n');
  el.className = `notice${multi ? ' is-block' : ''}`;
  el.innerHTML = `${icon(multi ? 'terminal' : 'info')}<div class="notice-body"><div class="notice-text"></div></div>`;
  el.querySelector('.notice-text').textContent = text;
  if (multi && text.split('\n').length > 7) {
    el.classList.add('is-folded');
    const more = actionButton('chevrons-up-down', 'Show all', (btn) => {
      const folded = el.classList.toggle('is-folded');
      btn.querySelector('span').textContent = folded ? 'Show all' : 'Show less';
    });
    more.classList.add('notice-more');
    el.querySelector('.notice-body').appendChild(more);
  }
  chat().appendChild(markNew(el));
}

// ─── Tool rows ────────────────────────────────────────────────────────────

function toolsContainer() {
  const body = agentBody();
  const last = body.lastElementChild;
  if (last?.classList.contains('tools')) return last;
  const box = document.createElement('div');
  box.className = 'tools';
  body.appendChild(box);
  return box;
}

const STATE_ICON = { done: 'check', error: 'x', pending: 'minus' };

/** Icon for the kind of work a tool does (falls back to a wrench). */
function toolKindIcon(name) {
  const n = String(name || '');
  if (n.startsWith('mcp__')) return 'plug';
  if (/^(read_file|read_document|read_bundle|resolve_context)$/.test(n)) return 'file-text';
  if (/^(write_file|edit_file|multi_edit)$/.test(n)) return 'file-pen';
  if (/^(run_bash|run_bg|bg_output|bg_kill)$/.test(n)) return 'terminal';
  if (/^(search_code|glob_files|fast_find|rank_files)$/.test(n)) return 'search';
  if (n === 'list_dir') return 'folder-open';
  if (/^git_/.test(n)) return 'git-branch';
  if (/^(web_search|verified_search|fetch_url|open_url)$/.test(n)) return 'globe';
  if (/^(memory_|lesson_)/.test(n)) return 'database';
  if (n === 'screenshot' || /image_text/.test(n)) return 'camera';
  if (n === 'skill_load') return 'book-open';
  if (n === 'ask_user_question') return 'message-circle-question';
  if (n === 'exit_plan_mode') return 'map';
  if (n === 'schedule_wakeup') return 'timer';
  if (/^(launch_app|focus_app|quit_app|list_apps|frontmost_app|applescript|read_ui|click_|type_text|key_press|mac_control|shortcut_run)/.test(n)) return 'app-window';
  return 'wrench';
}

function paintTool(row, data) {
  if (Array.isArray(data.images) && data.images.length) row._images = data.images;
  const id = String(data.id || row.dataset.id || '');
  if (id) row.dataset.id = id;
  const status = data.status || row.dataset.status || 'running';
  row.dataset.status = status;
  const summary = data.summary ?? row.dataset.summary ?? '';
  row.dataset.summary = summary;
  const title = data.title || row.dataset.title || data.name || 'Tool';
  row.dataset.title = title;
  const args = data.args ?? row.dataset.args ?? '';
  row.dataset.args = args;
  const hasFull = Boolean(data.has_full) || row.dataset.hasFull === '1';
  const fullChars = data.full_chars ?? data.output_chars ?? row.dataset.fullChars ?? 0;
  if (data.has_full) {
    row.dataset.hasFull = '1';
    row.dataset.fullChars = String(fullChars || 0);
    if (data.id) fullById.set(String(data.id), { name: data.name || '', title, args });
  } else if (!data.id && !hasFull) {
    row.dataset.hasFull = '';
  }

  const stateHtml = status === 'running' ? '<span class="spinner"></span>' : icon(STATE_ICON[status] || 'check');
  const hasOut = !!summary && status !== 'running';
  const stateLabel = { running: 'Running', done: 'Done', error: 'Failed', pending: 'Not finished' }[status] || '';
  const fullLabel = hasFull ? `Full output${row.dataset.fullChars ? ` · ${fmtChars(row.dataset.fullChars)}` : ''}` : '';
  row.innerHTML = `
    <button type="button" class="tool-head" ${hasOut ? `aria-expanded="${row.classList.contains('is-open')}"` : 'disabled'}>
      <span class="tool-kind">${icon(toolKindIcon(row.dataset.name || data.name))}</span>
      <span class="tool-title">${escapeHtml(title)}</span>
      <span class="tool-args">${escapeHtml(args)}</span>
      <span class="tool-state" title="${stateLabel}" aria-label="${stateLabel}">${stateHtml}</span>
      <span class="tool-chev">${hasOut ? icon('chevron-right') : ''}</span>
    </button>
    ${hasOut ? `<div class="fold"><div class="fold-inner"><div class="tool-out">${escapeHtml(summary)}</div>${hasFull && row.dataset.id ? `<button type="button" class="tool-full" data-full="${escapeHtml(row.dataset.id)}" title="${escapeHtml(fullLabel)}">${icon('maximize-2')}<span>Full output${row.dataset.fullChars ? ` · ${escapeHtml(fmtChars(row.dataset.fullChars))}` : ''}</span></button>` : ''}</div></div>` : ''}`;
  row.title = args ? `${title} ${args}` : title;
  if (row._images?.length) {
    // Screenshots the tool took: visible without opening the row.
    row.querySelector('.tool-head').after(renderToolImages(row._images));
    row.classList.add('has-media');
  }
  if (hasOut) {
    row.querySelector('.tool-head').addEventListener('click', () => {
      const open = row.classList.toggle('is-open');
      row.querySelector('.tool-head').setAttribute('aria-expanded', String(open));
    });
    row.querySelector('[data-full]')?.addEventListener('click', (e) => {
      e.stopPropagation();
      openToolOutput(row.dataset.id);
    });
  }
}

function createToolRow(data) {
  const row = document.createElement('div');
  row.className = 'tool';
  if (data.id) row.dataset.id = data.id;
  row.dataset.name = data.name || '';
  if (data.id && data.has_full) {
    fullById.set(String(data.id), {
      name: data.name || '',
      title: data.title || data.name || 'Tool',
      args: data.args || '',
    });
  }
  if (data.status === 'error' && data.summary) row.classList.add('is-open');
  paintTool(row, data);
  const box = toolsContainer();
  box.appendChild(markNew(row));
  if (data.id) toolRows.set(String(data.id), row);
  syncToolFold(box);
  return row;
}

/**
 * Long tool runs fold: past TOOL_FOLD_AT rows only the newest few stay
 * visible, behind a "N earlier tool calls" toggle (failures are counted in
 * the label so they're never silently hidden).
 */
function syncToolFold(box) {
  if (!box) return;
  const rows = box.querySelectorAll(':scope > .tool');
  let btn = box.querySelector(':scope > .tools-more');
  if (rows.length < TOOL_FOLD_AT) {
    btn?.remove();
    box.classList.remove('is-folded', 'is-expanded');
    return;
  }
  if (!btn) {
    btn = document.createElement('button');
    btn.type = 'button';
    btn.className = 'tools-more';
    btn.addEventListener('click', () => {
      const expanded = box.classList.toggle('is-expanded');
      box.classList.toggle('is-folded', !expanded);
      syncToolFold(box);
    });
    box.prepend(btn);
  }
  const expanded = box.classList.contains('is-expanded');
  box.classList.toggle('is-folded', !expanded);
  const hidden = [...rows].slice(0, rows.length - TOOL_FOLD_KEEP);
  const failed = hidden.filter((r) => r.dataset.status === 'error').length;
  btn.classList.toggle('has-failed', !expanded && failed > 0);
  btn.setAttribute('aria-expanded', String(expanded));
  btn.innerHTML = expanded
    ? `${icon('chevrons-down-up')}<span>Hide earlier tool calls</span>`
    : `${icon('chevrons-up-down')}<span>${hidden.length} earlier tool call${hidden.length === 1 ? '' : 's'}</span>${failed ? `<span class="tm-failed">${failed} failed</span>` : ''}`;
}

/** A running row just finished: its check draws in (or it nudges on error). */
function settleRow(row) {
  row.classList.remove('just-settled');
  row.classList.add('just-settled');
  clearTimeout(row._settleTimer);
  row._settleTimer = setTimeout(() => row.classList.remove('just-settled'), 800);
}

export function toolStart(data) {
  const id = String(data.id || '');
  const existing = id && toolRows.get(id);
  if (existing?.isConnected && existing.dataset.status === 'running') return;
  if (existing?.isConnected && existing.dataset.status === 'pending') {
    paintTool(existing, { ...data, status: 'running' });
  } else {
    finalizeLive();
    createToolRow({ ...data, status: 'running' });
    noteUnseen();
  }
  afterAppend();
}

export function toolDone(data) {
  const id = String(data.id || '');
  const status = data.error ? 'error' : 'done';
  const row = id && toolRows.get(id);
  if (row?.isConnected) {
    const wasRunning = row.dataset.status === 'running';
    paintTool(row, { ...data, status });
    if (wasRunning) settleRow(row);
    if (status === 'error' && row.dataset.summary) {
      row.classList.add('is-open');
      row.querySelector('.tool-head')?.setAttribute('aria-expanded', 'true');
    }
    syncToolFold(row.parentElement);
  } else {
    createToolRow({ ...data, status });
  }
  afterAppend();
}

/** Turn ended or was cancelled: nothing is running any more. */
export function settleTools() {
  for (const row of toolRows.values()) {
    if (row.isConnected && row.dataset.status === 'running') paintTool(row, { status: 'pending' });
  }
}

// ─── Diffs ────────────────────────────────────────────────────────────────

export function appendDiff(data) {
  const lines = data.lines || [];
  if (!lines.length) return;
  finalizeLive();
  const el = document.createElement('div');
  el.className = 'diff';
  const verb = { create: 'Created', write: 'Wrote', edit: 'Edited' }[data.action] || 'Changed';
  const body = lines.map((ln) => {
    const cls = ln.startsWith('+') ? 'add' : ln.startsWith('-') ? 'del' : ln.startsWith('@@') ? 'hunk' : '';
    return `<span class="diff-line ${cls}">${escapeHtml(ln) || ' '}</span>`;
  }).join('');
  const extra = data.hidden ? `<span class="diff-line hunk">… ${data.hidden} more lines</span>` : '';
  el.innerHTML = `
    <div class="diff-head">
      ${icon('file-diff')}
      <span class="diff-path" title="${escapeHtml(data.path || '')}">${escapeHtml(verb)} ${escapeHtml(data.path || '')}</span>
      <span class="diff-add">+${Number(data.added) || 0}</span>
      <span class="diff-del">−${Number(data.removed) || 0}</span>
      ${data.id ? `<button type="button" class="diff-open" title="Open in the changes panel">${icon('panel-right')}<span>Panel</span></button>` : ''}
    </div>
    <pre class="diff-body">${body}${extra}</pre>`;
  el.querySelector('.diff-open')?.addEventListener('click', () => openChange(data.id));
  if (lines.length > 12) {
    el.classList.add('is-collapsed');
    const more = document.createElement('button');
    more.type = 'button';
    more.className = 'diff-more';
    more.textContent = `Show all ${lines.length} lines`;
    more.addEventListener('click', () => {
      const collapsed = el.classList.toggle('is-collapsed');
      more.textContent = collapsed ? `Show all ${lines.length} lines` : 'Show less';
    });
    el.appendChild(more);
  }
  agentBody().appendChild(markNew(el));
  noteUnseen();
  afterAppend();
}

// ─── Full tool output viewer ──────────────────────────────────────────────

/** tool id → { name, title, args } remembered from rows (snapshot rows too). */
const fullById = new Map();

function fmtChars(n) {
  const v = Number(n) || 0;
  if (v < 1000) return `${v} chars`;
  if (v < 1_000_000) return `${(v / 1000).toFixed(v < 10_000 ? 1 : 0).replace(/\.0$/, '')}k chars`;
  return `${(v / 1_000_000).toFixed(1).replace(/\.0$/, '')}M chars`;
}

let fullText = '';

/** Open the full recorded output for one tool call (fetched on demand). */
export function openToolOutput(id) {
  const key = String(id || '');
  if (!key) return;
  const meta = fullById.get(key) || {};
  openModal('tool-output-modal');
  $('tool-output-title').textContent = meta.title || meta.name || 'Full output';
  $('tool-output-sub').textContent = meta.args || 'Loading…';
  $('tool-output-icon').innerHTML = `<span data-icon="terminal"></span>`;
  const metaEl = $('tool-output-meta');
  metaEl.hidden = true;
  metaEl.innerHTML = '';
  const body = $('tool-output-body');
  body.textContent = 'Loading…';
  $('tool-output-scroll').scrollTop = 0;
  $('tool-output-note').textContent = '';
  fullText = '';
  fetchToolOutput(key).then((res) => {
    fullText = String(res?.output || '');
    const chars = Number(res?.chars) || fullText.length;
    $('tool-output-title').textContent = res?.name ? `${res.name} — full output` : (meta.title || 'Full output');
    $('tool-output-sub').textContent = res?.args || meta.args || `${fmtChars(chars)}${res?.status ? ` · ${res.status}` : ''}`;
    body.textContent = fullText || '(no output)';
    $('tool-output-note').textContent = `${fmtChars(chars)}${res?.truncated ? ' · truncated for viewing' : ''}`;
    const live = toolRows.get(key);
    if (live && live.isConnected && res?.output !== undefined) {
      live.dataset.hasFull = '1';
      live.dataset.fullChars = String(chars);
      paintTool(live, {});
    }
  }).catch((err) => {
    body.textContent = err?.status === 404 ? 'No full output saved for this step.' : 'Could not load the full output.';
    $('tool-output-sub').textContent = meta.args || '';
    if (err?.status !== 404) showToast('Could not load the full output', true);
  });
}

function initToolOutputModal() {
  $('tool-output-copy')?.addEventListener('click', async (btn) => {
    const el = $('tool-output-copy');
    if (!fullText) {
      showToast('Nothing to copy yet', true);
      return;
    }
    if (await copyText(fullText)) flashDone(el, 'Copied');
    else showToast('Copy failed', true);
  });
}

// ─── Public append API (live events + snapshots) ──────────────────────────

function appendEntry(entry) {
  const role = normalizeRole(entry.role);
  const text = String(entry.text ?? '').trim();
  switch (role) {
    case 'you':
      if (text || entry.attachments?.length) appendUser(text, entry.attachments || []);
      break;
    case 'assistant':
      if (text) appendAssistant(text, entry.title);
      break;
    case 'thinking':
      if (text) appendThinking(text);
      break;
    case 'tool':
      createToolRow(entry);
      break;
    case 'diff':
      appendDiff(entry);
      break;
    default:
      if (text) appendNotice(text);
  }
}

/** A committed message from the session (live). `attachments`: files sent with a prompt. */
export function appendMessage(role, text, title, attachments) {
  const r = normalizeRole(role);
  const value = String(text ?? '').trim();
  const files = r === 'you' && Array.isArray(attachments) ? attachments : [];
  if (!value && !files.length) return;

  if (live && ((r === 'assistant' && live.kind === 'assistant') || (r === 'thinking' && live.kind === 'thinking'))) {
    finalizeLive(value);
    afterAppend();
    return;
  }
  if (r === 'you') finalizeLive();
  appendEntry({ role: r, text: value, title, attachments: files });
  if (r !== 'thinking') noteUnseen();
  afterAppend({ scroll: true });
  if (r === 'you') scrollToBottom(true);
}

// ─── Streaming ────────────────────────────────────────────────────────────

function renderLive() {
  if (!live) return;
  live.raf = 0;
  if (live.kind === 'assistant') {
    live.body.innerHTML = renderMarkdown(live.buffer, { streaming: true });
    applyMarkdownLinks(live.body);
  } else {
    live.body.textContent = live.buffer;
    live.body.scrollTop = live.body.scrollHeight;
  }
  scrollToBottom();
}

function startLive(kind, title) {
  finalizeLive();
  removeTyping();
  const body = agentBody();
  if (kind === 'thinking') {
    const { el, body: tb } = makeThinking('', { liveStream: true });
    body.appendChild(el);
    live = { kind, el, body: tb, buffer: '', raf: 0 };
  } else {
    const parts = makeAgentText('', { title, liveStream: true });
    body.appendChild(markNew(parts.wrap));
    live = { kind, el: parts.wrap, bubble: parts.bubble, body: parts.body, actions: parts.actions, buffer: '', raf: 0 };
    noteUnseen();
  }
  scrollToBottom();
}

/** Freeze the live bubble. `finalText` replaces the streamed buffer. */
function finalizeLive(finalText, { stopped = false } = {}) {
  if (!live) return;
  const cur = live;
  live = null;
  if (cur.raf) cancelAnimationFrame(cur.raf);
  const text = (finalText ?? cur.buffer).trim();
  if (!text) {
    cur.el.remove();
    return;
  }
  if (cur.kind === 'assistant') {
    cur.body.innerHTML = renderMarkdown(text);
    applyMarkdownLinks(cur.body);
    cur.bubble.classList.remove('is-live');
    cur.el.dataset.raw = text;
    cur.actions.hidden = false;
    // The finished reply settles with a faint glow sweep.
    if (!stopped) {
      cur.bubble.classList.add('just-done');
      setTimeout(() => cur.bubble.classList.remove('just-done'), 1000);
    }
    if (stopped) {
      cur.bubble.classList.add('is-stopped');
      const note = document.createElement('span');
      note.className = 'stopped-note';
      note.textContent = 'Stopped before the reply finished';
      cur.bubble.appendChild(note);
    }
  } else {
    cur.el.classList.remove('is-live', 'is-open');
    cur.el.querySelector('.think-head strong').textContent = 'Thought';
    cur.el.querySelector('.think-head').setAttribute('aria-expanded', 'false');
    cur.body.textContent = text;
    cur.el.querySelector('.think-preview').textContent = thinkingPreview(text);
  }
}

export function streamStart(kind, title) {
  if (kind === 'thinking' && !shouldShowThoughts()) return;
  startLive(kind === 'thinking' ? 'thinking' : 'assistant', title);
}

export function streamDelta(kind, chunk) {
  if (!chunk) return;
  const k = kind === 'thinking' ? 'thinking' : 'assistant';
  if (k === 'thinking' && !shouldShowThoughts()) return;
  if (!live || live.kind !== k) startLive(k);
  live.buffer += chunk;
  if (!live.raf) live.raf = requestAnimationFrame(renderLive);
}

export function streamEnd(kind, aborted) {
  if (live && (!kind || live.kind === (kind === 'thinking' ? 'thinking' : 'assistant'))) {
    finalizeLive(undefined, { stopped: !!aborted && live.kind === 'assistant' });
  }
  if (aborted) settleTools();
  afterAppend();
}

// ─── Snapshots ────────────────────────────────────────────────────────────

export function clearChat() {
  const root = chat();
  if (root) root.innerHTML = '';
  toolRows.clear();
  live = null;
  lastSnapshotSig = null;
}

/**
 * Connection dropped: force the next snapshot to re-render, and drop the
 * half-streamed bubble — chunks sent while we were away are gone, and the
 * committed reply (or the next stream) replaces it anyway.
 */
export function invalidateSnapshot() {
  lastSnapshotSig = null;
  if (live) {
    if (live.raf) cancelAnimationFrame(live.raf);
    live.el.remove();
    live = null;
  }
}

/**
 * Render the server's transcript. Skipped when nothing changed since the
 * last render; a live stream survives the re-render.
 */
export function renderSnapshot(data) {
  const messages = data.messages || [];
  const sig = `${data.session_id}|${data.message_count}|${data.show_internal}|${messages.length}`;
  if (sig === lastSnapshotSig && chat()?.childElementCount) return false;

  // A half-streamed bubble belongs to its session: carried into another one
  // (New chat, a resumed session) it showed the old "Thinking…" with no prompt.
  const sameSession = lastSnapshotSession === undefined || lastSnapshotSession === data.session_id;
  const keep = sameSession ? live : null;
  if (live) live.el.remove();
  if (!sameSession && live?.raf) cancelAnimationFrame(live.raf);
  lastSnapshotSession = data.session_id;
  const root = chat();
  root.innerHTML = '';
  toolRows.clear();
  live = null;
  restoring = true;
  try {
    messages.forEach(appendEntry);
    if (keep) {
      const body = agentBody();
      body.appendChild(keep.el);
      live = keep;
    }
  } finally {
    restoring = false;
  }
  lastSnapshotSig = sig;
  syncThoughtsVisibility();
  syncTyping();
  scrollToBottom(true);
  return true;
}

// ─── Thinking visibility ──────────────────────────────────────────────────

export function shouldShowThoughts() {
  return store.session.show_internal !== false && store.showThoughts;
}

export function syncThoughtsVisibility() {
  chat()?.classList.toggle('hide-thoughts', !shouldShowThoughts());
  if (!shouldShowThoughts() && live?.kind === 'thinking') {
    live.el.remove();
    live = null;
  }
}
