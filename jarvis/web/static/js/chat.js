/** Transcript rendering: user bubbles and agent turns (text, thinking, tools, diffs).
 *
 * Agent output is grouped into turns: everything between two user messages
 * lands in one `.turn-agent` with a single avatar. Live streams render into
 * a bubble that is finalised in place when the committed message arrives, so
 * nothing flickers or jumps.
 */
import { $, escapeHtml, copyText, flashDone, truncate } from './utils.js';
import { icon } from './icons.js';
import { store } from './store.js';
import { renderMarkdown, applyMarkdownLinks } from './markdown.js';

const chat = () => $('chat');
const scroller = () => $('chat-scroll');

/** tool id → row element (live + snapshot rows) */
const toolRows = new Map();
/** Active stream: { kind, el, body, buffer, raf } */
let live = null;
let stickToBottom = true;
let lastSnapshotSig = null;
let fillPromptFn = null;

export function normalizeRole(role) {
  const r = String(role || 'assistant').toLowerCase();
  if (r === 'user' || r === 'you') return 'you';
  if (['assistant', 'thinking', 'log', 'system', 'tool', 'diff'].includes(r)) return r;
  return 'assistant';
}

// ─── Scrolling ────────────────────────────────────────────────────────────

export function initChat({ onReuse } = {}) {
  fillPromptFn = onReuse;
  const sc = scroller();
  const jump = $('jump-btn');
  sc?.addEventListener('scroll', () => {
    const dist = sc.scrollHeight - sc.scrollTop - sc.clientHeight;
    stickToBottom = dist < 60;
    if (stickToBottom) {
      jump.hidden = true;
      jump.classList.remove('has-new');
    } else if (dist > 240) {
      jump.hidden = false;
    }
  }, { passive: true });
  jump?.addEventListener('click', () => scrollToBottom(true, true));
  syncThoughtsVisibility();
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
  const jump = $('jump-btn');
  if (jump) {
    jump.hidden = true;
    jump.classList.remove('has-new');
  }
}

// ─── Structure helpers ────────────────────────────────────────────────────

function isAgentTurn(el) {
  return el?.classList?.contains('turn-agent');
}

/** The body of the current agent turn, creating a new turn when needed. */
function agentBody() {
  removeTyping();
  const root = chat();
  const last = root.lastElementChild;
  if (isAgentTurn(last)) return last.querySelector('.agent-body');
  const turn = document.createElement('div');
  turn.className = 'turn-agent';
  turn.innerHTML = '<span class="mark" aria-hidden="true"></span><div class="agent-body"></div>';
  root.appendChild(turn);
  return turn.querySelector('.agent-body');
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

function appendUser(text) {
  removeTyping();
  const row = document.createElement('div');
  row.className = 'turn-you';
  const bubble = document.createElement('div');
  const isCommand = /^[/!]\S/.test(text) && !text.includes('\n');
  bubble.className = `bubble-you md${isCommand ? ' is-command' : ''}`;
  if (isCommand) bubble.textContent = text;
  else {
    bubble.innerHTML = renderMarkdown(text);
    applyMarkdownLinks(bubble);
  }
  const actions = document.createElement('div');
  actions.className = 'msg-actions';
  actions.append(
    actionButton('pencil', 'Edit', () => fillPromptFn?.(text)),
    copyAction(() => text),
  );
  row.append(bubble, actions);
  chat().appendChild(row);
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
    <div class="think-body"></div>`;
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
  agentBody().appendChild(wrap);
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
  chat().appendChild(el);
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
  const status = data.status || row.dataset.status || 'running';
  row.dataset.status = status;
  const summary = data.summary ?? row.dataset.summary ?? '';
  row.dataset.summary = summary;
  const title = data.title || row.dataset.title || data.name || 'Tool';
  row.dataset.title = title;
  const args = data.args ?? row.dataset.args ?? '';
  row.dataset.args = args;

  const stateHtml = status === 'running' ? '<span class="spinner"></span>' : icon(STATE_ICON[status] || 'check');
  const hasOut = !!summary && status !== 'running';
  const stateLabel = { running: 'Running', done: 'Done', error: 'Failed', pending: 'Not finished' }[status] || '';
  row.innerHTML = `
    <button type="button" class="tool-head" ${hasOut ? `aria-expanded="${row.classList.contains('is-open')}"` : 'disabled'}>
      <span class="tool-kind">${icon(toolKindIcon(row.dataset.name || data.name))}</span>
      <span class="tool-title">${escapeHtml(title)}</span>
      <span class="tool-args">${escapeHtml(args)}</span>
      <span class="tool-state" title="${stateLabel}" aria-label="${stateLabel}">${stateHtml}</span>
      <span class="tool-chev">${hasOut ? icon('chevron-right') : ''}</span>
    </button>
    ${hasOut ? `<div class="tool-out">${escapeHtml(summary)}</div>` : ''}`;
  row.title = args ? `${title} ${args}` : title;
  if (hasOut) {
    row.querySelector('.tool-head').addEventListener('click', () => {
      const open = row.classList.toggle('is-open');
      row.querySelector('.tool-head').setAttribute('aria-expanded', String(open));
    });
  }
}

function createToolRow(data) {
  const row = document.createElement('div');
  row.className = 'tool';
  if (data.id) row.dataset.id = data.id;
  row.dataset.name = data.name || '';
  if (data.status === 'error' && data.summary) row.classList.add('is-open');
  paintTool(row, data);
  toolsContainer().appendChild(row);
  if (data.id) toolRows.set(String(data.id), row);
  return row;
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
  }
  afterAppend();
}

export function toolDone(data) {
  const id = String(data.id || '');
  const status = data.error ? 'error' : 'done';
  const row = id && toolRows.get(id);
  if (row?.isConnected) {
    paintTool(row, { ...data, status });
    if (status === 'error' && row.dataset.summary) {
      row.classList.add('is-open');
      row.querySelector('.tool-head')?.setAttribute('aria-expanded', 'true');
    }
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
    </div>
    <pre class="diff-body">${body}${extra}</pre>`;
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
  agentBody().appendChild(el);
  afterAppend();
}

// ─── Public append API (live events + snapshots) ──────────────────────────

function appendEntry(entry) {
  const role = normalizeRole(entry.role);
  const text = String(entry.text ?? '').trim();
  switch (role) {
    case 'you':
      if (text) appendUser(text);
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

/** A committed message from the session (live). */
export function appendMessage(role, text, title) {
  const r = normalizeRole(role);
  const value = String(text ?? '').trim();
  if (!value) return;

  if (live && ((r === 'assistant' && live.kind === 'assistant') || (r === 'thinking' && live.kind === 'thinking'))) {
    finalizeLive(value);
    afterAppend();
    return;
  }
  if (r === 'you') finalizeLive();
  appendEntry({ role: r, text: value, title });
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
    body.appendChild(parts.wrap);
    live = { kind, el: parts.wrap, bubble: parts.bubble, body: parts.body, actions: parts.actions, buffer: '', raf: 0 };
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

  const keep = live;
  if (keep) keep.el.remove();
  const root = chat();
  root.innerHTML = '';
  toolRows.clear();
  live = null;
  messages.forEach(appendEntry);
  if (keep) {
    const body = agentBody();
    body.appendChild(keep.el);
    live = keep;
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
