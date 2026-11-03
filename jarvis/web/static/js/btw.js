/** /btw — a side question in its own small window.
 *
 * `/btw how far along are you?` (or ⌘K → Side question) asks the model about
 * the chat while it works. The answer streams into this floating window, where
 * the conversation can go on; Jarvis's own turn, its queue and the transcript
 * are never touched (jarvis/btw.py, jarvis/web/btw_api.py).
 *
 * Nothing is lost:
 * - closing a window (or starting a new chat) *archives* it — the server keeps
 *   it on disk, so it survives a reload and a restart;
 * - the clock in the header lists every saved side chat; a click opens it again
 *   where it left off;
 * - an unsent question is kept as a draft while the window is open or closed.
 *
 * The window can be dragged by its header and resized from every edge; both
 * its place and its size are remembered.
 */
import { $, escapeHtml, showToast, storageGet, storageSet, haptic } from './utils.js';
import { icon } from './icons.js';
import { api } from './api.js';
import { renderMarkdown, applyMarkdownLinks } from './markdown.js';
import { activeTab, onActiveTab, tabKey, gainedSession } from './tabkey.js';

const POS_KEY = 'jarvis-btw-pos';
const SIZE_KEY = 'jarvis-btw-size';
const DRAFT_KEY = 'jarvis-btw-draft';
/** The unsent question belongs to the chat it was typed in; ``draftTab``
 *  records which chat the visible textarea currently holds so a tab switch
 *  swaps the text synchronously (composer.js keeps the same ledger). */
const draftKey = (t) => `${DRAFT_KEY}:${(t ? tabKey(t) : tabKey(activeTab())) || 'x'}`;
const MARGIN = 12;
const MIN_W = 300;
const MIN_H = 220;

/** The side conversation shown: `{ id, turns: [{ q, a, status, error }] }`. */
let conv = null;
let saved = [];            // closed side chats, newest first (from GET /api/btw)
let asking = false;        // a POST is in flight (the id isn't known yet)
let early = [];            // events that arrived before the POST answered
let histOpen = false;
let histQuery = '';
let armedForget = '';      // the history entry armed for deletion
let renderQueued = false;
let staleServer = false;   // the running Jarvis predates the history API
const selfClosed = new Set();   // ids this page closed itself (its own `closed` event is not a farewell)
let lastHeadButton = 0;         // a click on a header button just happened
let unread = false;             // an answer finished while the window was out of sight
let dragMoved = false;          // the header was dragged (so its click is not "open")
let win = null;
let draftTab = '';   // whose question the textarea holds right now ('' until first shown)

const log = () => $('btw-log');
const input = () => $('btw-input');
const runningTurn = () => !!conv?.turns.some((t) => t.status === 'wait' || t.status === 'live');

// ─── Window ──────────────────────────────────────────────────────────────

function build() {
  if (win) return win;
  win = document.createElement('section');
  win.id = 'btw';
  win.className = 'btw glass';
  win.setAttribute('role', 'dialog');
  win.setAttribute('aria-label', 'Side question');
  win.hidden = true;
  win.innerHTML = `
    <header class="btw-head" id="btw-head">
      <span class="btw-title">${icon('message-circle-question')}<strong>/btw</strong><span class="btw-sub">on the side</span></span>
      <span class="btw-actions">
        <button type="button" class="icon-btn btw-hist-btn" id="btw-history" title="Saved side chats" aria-label="Saved side chats" aria-expanded="false">${icon('history')}<span class="btw-badge" id="btw-badge" hidden>0</span></button>
        <button type="button" class="icon-btn" id="btw-new" title="New side chat (the current one is saved)" aria-label="New side chat">${icon('plus')}</button>
        <button type="button" class="icon-btn" id="btw-min" title="Minimize" aria-label="Minimize">${icon('minus')}</button>
        <button type="button" class="icon-btn" id="btw-close" title="Close (the chat is saved)" aria-label="Close">${icon('x')}</button>
      </span>
    </header>
    <div class="btw-body" id="btw-log" aria-live="polite"></div>
    <section class="btw-hist" id="btw-hist" hidden aria-label="Saved side chats">
      <div class="btw-hist-top">
        <span class="btw-hist-title">${icon('history')} Saved side chats</span>
        <button type="button" class="icon-btn" id="btw-hist-close" aria-label="Back to the chat">${icon('x')}</button>
      </div>
      <input type="search" id="btw-hist-q" class="btw-hist-q" placeholder="Search side chats…" aria-label="Search side chats" autocomplete="off" spellcheck="false">
      <div class="btw-hist-list" id="btw-hist-list"></div>
      <div class="btw-hist-foot" id="btw-hist-foot"></div>
    </section>
    <form class="btw-form" id="btw-form">
      <textarea id="btw-input" rows="1" placeholder="Ask on the side…" aria-label="Side question" spellcheck="true"></textarea>
      <button type="submit" class="btw-send" id="btw-send" aria-label="Send">${icon('arrow-up')}</button>
    </form>
    <div class="btw-note">Doesn’t interrupt Jarvis · read-only · saved on this computer</div>
    <div class="btw-rz" data-dir="n"></div><div class="btw-rz" data-dir="s"></div>
    <div class="btw-rz" data-dir="e"></div><div class="btw-rz" data-dir="w"></div>
    <div class="btw-rz" data-dir="ne"></div><div class="btw-rz" data-dir="nw"></div>
    <div class="btw-rz" data-dir="se"></div><div class="btw-rz" data-dir="sw"></div>`;
  document.body.appendChild(win);

  $('btw-close').addEventListener('click', closeBtw);
  $('btw-min').addEventListener('click', () => setMin(!isMin()));
  $('btw-new').addEventListener('click', newSide);
  $('btw-history').addEventListener('click', toggleHistory);
  $('btw-hist-close').addEventListener('click', () => setHistory(false));
  $('btw-hist-q').addEventListener('input', (e) => { histQuery = e.target.value; renderHistory(); });
  $('btw-head').addEventListener('pointerdown', (e) => {
    if (e.target.closest('button')) lastHeadButton = Date.now();
  });
  $('btw-head').addEventListener('dblclick', (e) => {
    // A quick second tap on + / ⌕ / × must not read as a double-click on the bar.
    if (e.target.closest('button') || Date.now() - lastHeadButton < 500) return;
    setMin(!isMin());
  });
  // The minimized bar is a button: one click on it opens the window again.
  $('btw-head').addEventListener('click', (e) => {
    if (isMin() && !e.target.closest('button') && !dragMoved) setMin(false);
  });
  $('btw-form').addEventListener('submit', (e) => { e.preventDefault(); sendFromInput(); });
  $('btw-send').addEventListener('click', (e) => {
    if (runningTurn()) { e.preventDefault(); stopAnswer(); }
  });
  const el = input();
  el.addEventListener('input', () => {
    grow();
    syncSend();
    storageSet(draftTab || draftKey(), el.value);
  });
  el.addEventListener('keydown', (e) => {
    if (e.key === 'Enter' && !e.shiftKey && !e.isComposing && e.keyCode !== 229) {
      e.preventDefault();
      sendFromInput();
    } else if (e.key === 'Escape') {
      e.preventDefault();
      e.stopPropagation();
      if (runningTurn()) stopAnswer();
      else if (histOpen) setHistory(false);
      else setMin(true);
    }
  });
  log().addEventListener('click', (e) => {
    if (e.target.closest('[data-btw-retry]')) retryLast();
    const resume = e.target.closest('[data-btw-resume]');
    if (resume) resumeSaved(resume.getAttribute('data-btw-resume'));
  });
  $('btw-hist-list').addEventListener('click', onHistoryClick);
  for (const handle of win.querySelectorAll('.btw-rz')) resizeify(handle, handle.dataset.dir);
  dragify($('btw-head'));
  restorePos();
  restoreSize();
  window.addEventListener('resize', keepOnScreen);
  return win;
}

const isMin = () => !!win?.classList.contains('is-min');

/** Collapse to a header-only bar, or open back up. */
function setMin(min) {
  if (!win) return;
  win.classList.toggle('is-min', min);
  const btn = $('btw-min');
  if (btn) {
    btn.innerHTML = icon(min ? 'maximize-2' : 'minus');
    btn.title = min ? 'Open' : 'Minimize';
    btn.setAttribute('aria-label', btn.title);
  }
  if (!min) {
    unread = false;
    requestAnimationFrame(() => { keepOnScreen(); log().scrollTop = log().scrollHeight; });
  }
  syncChrome();
}

function show({ focus = true } = {}) {
  build();
  win.hidden = false;
  setMin(false);
  swapDraft();      // owner check: never shows the other chat's question
  render();
  if (focus) input()?.focus();
}

/** Put the other chat's question away and bring this chat's back. Called at
 *  tab-switch time (before any network await) and on every show. */
function swapDraft({ carry = false } = {}) {
  const el = input();
  const key = draftKey();
  if (!el || draftTab === key) return;
  if (carry && draftTab) {
    // The same chat, now with a session: the question typed so far goes along.
    storageSet(key, el.value);
    storageSet(draftTab, '');
  } else {
    if (draftTab) storageSet(draftTab, el.value);   // never file it under the new chat
    el.value = storageGet(key, '');
  }
  draftTab = key;
  requestAnimationFrame(() => { grow(); syncSend(); });
}

/** Close the window — the chat is archived by the server, never dropped. */
export async function closeBtw() {
  const hadTurns = !!conv?.turns.length;
  const gone = await archiveCurrent();
  conv = null;
  asking = false;
  early = [];
  setHistory(false);
  unread = false;
  if (win) win.hidden = true;
  syncChrome();
  if (hadTurns && gone) {
    showToast('Side chat saved', false, { label: 'Reopen', onClick: () => resumeSaved(gone.id) });
  }
  refreshHistory();
}

async function newSide() {
  await archiveCurrent();
  conv = null;
  asking = false;
  early = [];
  // An unsent question stays in the box — starting a new chat must not eat it.
  setHistory(false);
  if (win) {
    win.hidden = false;          // a stray `closed` during the archive must not leave it shut
    setMin(false);
  }
  render();
  input()?.focus();
}

/** Keep the open conversation in the history. Returns its saved view. */
async function archiveCurrent() {
  if (!conv?.id || !conv.turns.length) return null;
  const id = conv.id;
  selfClosed.add(id);          // our own `closed` event must not shut the window
  let res = null;
  try {
    res = await api('/api/btw', 'POST', { op: 'close', id });
  } catch { /* the answer may be mid-flight; the server still holds the thread */ }
  setTimeout(() => selfClosed.delete(id), 1500);
  return res?.saved || null;
}

// ─── History ─────────────────────────────────────────────────────────────

/** The chat on screen, as the server knows it: a side chat belongs to the
 * session it was asked in, so every request says which one this page shows. */
function scope() {
  const tab = activeTab();
  return tab ? { tab: tab.id, session: tab.session_id || undefined } : {};
}

function scopeQuery() {
  const s = scope();
  const q = new URLSearchParams();
  if (s.tab) q.set('tab', s.tab);
  if (s.session) q.set('session', s.session);
  const str = q.toString();
  return str ? `?${str}` : '';
}

/** Does a `btw` event concern the chat this page shows? */
function inScope(evt) {
  const tab = activeTab();
  if (!tab) return true;                      // no tab list yet: the server's view is ours
  if (evt.session) return tab.session_id === evt.session;
  return evt.tab == null || evt.tab === tab.id;
}

async function fetchScoped() {
  try {
    return await api(`/api/btw${scopeQuery()}`);
  } catch {
    return null;
  }
}

async function refreshHistory() {
  const res = await fetchScoped();
  if (!res) return;
  // A Jarvis started before the history API answers without it — say so
  // instead of claiming nothing was ever saved.
  staleServer = !Array.isArray(res?.history);
  saved = Array.isArray(res?.history) ? res.history : [];
  paintBadge();
  if (histOpen) renderHistory();
  if (!conv?.turns.length && win && !win.hidden) renderEmpty();
}

function paintBadge() {
  const badge = $('btw-badge');
  if (badge) {
    badge.textContent = String(saved.length);
    badge.hidden = !saved.length;
  }
  syncChrome();
}

/** Everything that tells you /btw is in use: the minimized bar's status line
 * and the launcher beside the composer (shown once a side question exists). */
function syncChrome() {
  const busy = runningTurn();
  const ready = unread && !busy;
  if (win) {
    win.classList.toggle('is-busy', busy);
    win.classList.toggle('is-ready', ready);
    const sub = win.querySelector('.btw-sub');
    if (sub) sub.textContent = busy ? 'answering…' : ready ? 'answer ready' : 'on the side';
  }
  const chip = $('qc-btw');
  if (!chip) return;
  const used = saved.length > 0 || !!conv?.turns.length;
  chip.hidden = !used;
  chip.classList.toggle('is-live', busy);
  chip.classList.toggle('has-new', ready);
  const count = $('qc-btw-count');
  if (count) count.textContent = saved.length ? String(saved.length) : '';
  const shown = !!win && !win.hidden && !isMin();
  chip.setAttribute('aria-pressed', String(shown && histOpen));
  chip.title = ready ? 'Side question answered — open /btw' : 'Side questions (/btw) — open your history';
}

/** The launcher: open the window on the saved-chats list (or toggle it when already open). */
export function openBtwHistory() {
  if (win && !win.hidden && !isMin()) {
    toggleHistory();
    return;
  }
  show({ focus: false });
  refreshHistory().then(() => setHistory(true));
}

function setHistory(open) {
  histOpen = open;
  const panel = $('btw-hist');
  if (!panel) return;
  panel.hidden = !open;
  $('btw-history')?.setAttribute('aria-expanded', String(open));
  win.classList.toggle('is-hist', open);
  syncChrome();
  if (open) {
    armedForget = '';
    renderHistory();
    requestAnimationFrame(() => $('btw-hist-q')?.focus());
  }
}

function toggleHistory() {
  if (histOpen) return setHistory(false);
  refreshHistory().then(() => setHistory(true));
}

function when(ts) {
  if (!ts) return '';
  const secs = Math.max(0, (Date.now() / 1000) - ts);
  if (secs < 90) return 'just now';
  if (secs < 3600) return `${Math.round(secs / 60)}m ago`;
  if (secs < 86400) return `${Math.round(secs / 3600)}h ago`;
  const days = Math.round(secs / 86400);
  if (days === 1) return 'yesterday';
  if (days < 7) return `${days}d ago`;
  return new Date(ts * 1000).toLocaleDateString(undefined, { month: 'short', day: 'numeric' });
}

function renderHistory() {
  const list = $('btw-hist-list');
  if (!list) return;
  const q = histQuery.trim().toLowerCase();
  const staled = staleServer && !saved.length;
  const rows = saved.filter((t) => {
    if (!q) return true;
    return `${t.title} ${t.preview} ${(t.pairs || []).map((p) => p.q).join(' ')}`.toLowerCase().includes(q);
  });
  if (!rows.length) {
    list.innerHTML = staled
      ? `<div class="btw-hist-empty">Saved chats are waiting, but this Jarvis started before the history feature existed.<br><strong>Restart Jarvis</strong> and they appear here.</div>`
      : `<div class="btw-hist-empty">${saved.length ? 'No side chats match that.' : 'Nothing saved yet. Close a side chat and it waits here.'}</div>`;
  } else {
    list.innerHTML = rows.map((t) => {
      const live = conv?.id === t.id;
      const armed = armedForget === t.id;
      return `<div class="btw-hist-row${live ? ' is-open' : ''}">
        <button type="button" class="btw-hist-item" data-btw-resume="${t.id}"${live ? ' disabled' : ''}>
          <span class="btw-hist-name">${escapeHtml(t.title || 'Side chat')}</span>
          ${t.preview ? `<span class="btw-hist-preview">${escapeHtml(t.preview)}</span>` : ''}
          <span class="btw-hist-meta">${t.count || 0} ${t.count === 1 ? 'message' : 'messages'} · ${when(t.updated || t.closed_at || t.created)}${live ? ' · showing now' : t.open ? ' · open' : ''}</span>
        </button>
        <button type="button" class="btw-hist-del${armed ? ' is-armed' : ''}" data-btw-forget="${t.id}" title="${armed ? 'Click again to delete for good' : 'Delete this side chat'}" aria-label="${armed ? 'Delete for good' : 'Delete this side chat'}">${icon('trash-2')}${armed ? '<span>Delete?</span>' : ''}</button>
      </div>`;
    }).join('');
    applyMarkdownLinks(list);
  }
  const foot = $('btw-hist-foot');
  if (foot) {
    foot.textContent = saved.length
      ? `${saved.length} side ${saved.length === 1 ? 'chat' : 'chats'} kept on this computer · closing one saves it here`
      : 'Every side chat you have is kept here — nothing is lost';
  }
}

async function onHistoryClick(e) {
  const forget = e.target.closest('[data-btw-forget]');
  if (forget) {
    const id = forget.getAttribute('data-btw-forget');
    if (armedForget !== id) {
      armedForget = id;
      renderHistory();
      return;
    }
    armedForget = '';
    saved = saved.filter((t) => t.id !== id);
    renderHistory();
    paintBadge();
    api('/api/btw', 'POST', { op: 'forget', id }).catch(() => {});
    return;
  }
  const item = e.target.closest('[data-btw-resume]');
  if (item) resumeSaved(item.getAttribute('data-btw-resume'));
}

/** Open a saved side chat again, exactly where it stopped. */
async function resumeSaved(id) {
  if (!id || conv?.id === id) return setHistory(false);
  const listed = saved.find((t) => t.id === id);
  await archiveCurrent();
  let res;
  if (listed?.open) {
    res = { ok: true, thread: listed };      // still an open window on the server: just show it here
  } else {
    try {
      res = await api('/api/btw', 'POST', { op: 'reopen', id, tab: scope().tab });
    } catch {
      res = { ok: false };
    }
  }
  if (!res?.ok) {
    showToast(res?.error || 'That side chat is gone', true);
    refreshHistory();
    return;
  }
  conv = threadToConv(res.thread);
  asking = false;
  early = [];
  setHistory(false);
  show({ focus: true });
  haptic(5);
  refreshHistory();
}

function threadToConv(thread) {
  const turns = (thread?.turns || (thread?.pairs || []).map((p) => ({ q: p.q, a: p.a, status: 'done' })))
    .map((t) => ({ q: t.q || '', a: t.a || '', status: t.status || 'done', error: '' }));
  return { id: thread?.id || '', tab: thread?.tab, session: thread?.session ?? thread?.session_id ?? null, turns };
}

// ─── Asking ──────────────────────────────────────────────────────────────

/** `/btw <question>` from the composer, or `''` to just open the window. */
export function openBtw(question = '') {
  show();
  const q = String(question || '').trim();
  if (q) ask(q);
}

async function ask(text, { retry = false } = {}) {
  if (asking || runningTurn()) {
    showToast('Still answering — wait for it, or stop it first');
    return;
  }
  conv = conv || { id: '', tab: scope().tab, session: scope().session ?? null, turns: [] };
  const mine = conv;
  if (retry) mine.turns.pop();
  mine.turns.push({ q: text, a: '', status: 'wait', error: '' });
  asking = true;
  early = [];
  storageSet(draftTab || draftKey(), '');
  render();
  haptic(6);
  let res;
  try {
    // `tab` ties a new side chat to the chat it is asked in.
    res = await api('/api/btw', 'POST', { op: 'ask', text, id: mine.id || undefined, tab: scope().tab });
  } catch (err) {
    res = { ok: false, error: err?.payload?.error || (err?.status === 503 ? 'Jarvis is not ready yet' : 'Check the connection') };
  }
  if (conv !== mine) {
    // The page moved to another chat while the answer was on its way: it stays
    // with its own session on the server and shows up when you come back.
    asking = false;
    return;
  }
  asking = false;
  const turn = mine.turns[mine.turns.length - 1];
  if (!turn) return;
  if (!res?.ok) {
    if (/closed|gone/.test(res?.error || '')) mine.id = '';   // the server lost the thread: next ask starts a new one
    fail(turn, res?.error || 'Couldn’t ask');
    return;
  }
  mine.id = res.id;
  const queued = early.filter((evt) => evt.id === res.id);
  early = [];
  queued.forEach((evt) => apply(evt));
}

function sendFromInput() {
  const el = input();
  const text = el.value.trim();
  if (!text) return;
  if (runningTurn()) {
    showToast('Still answering — wait for it, or stop it first');
    return;
  }
  el.value = '';
  storageSet(draftTab || draftKey(), '');
  grow();
  syncSend();
  ask(text);
}

function retryLast() {
  const last = conv?.turns[conv.turns.length - 1];
  if (last && (last.status === 'error' || last.status === 'stopped')) ask(last.q, { retry: true });
}

async function stopAnswer() {
  if (!conv?.id) return;
  const res = await api('/api/btw', 'POST', { op: 'stop', id: conv.id }).catch(() => null);
  if (res?.ok) {
    const turn = conv.turns[conv.turns.length - 1];
    if (turn && (turn.status === 'wait' || turn.status === 'live')) turn.status = 'stopped';
    render();
  }
}

function fail(turn, why) {
  turn.status = 'error';
  turn.error = why;
  render();
}

// ─── Events (`btw` over the page's event stream) ─────────────────────────

export function handleBtwEvent(evt) {
  if (!evt?.id) return;
  // A question is on its way: its first events can beat the POST's answer.
  if (asking && !conv?.id) {
    if (inScope(evt)) early.push(evt);
    return;
  }
  if (evt.op === 'closed' || evt.op === 'reopened') {
    // The event this page's own close/new causes must not be mistaken for a
    // close from another window — that is what made + sometimes shut the panel.
    if (evt.op === 'closed' && !selfClosed.has(evt.id) && conv?.id === evt.id) {
      conv = null;
      if (win) win.hidden = true;
    }
    refreshHistory();
    return;
  }
  // Another page of this Jarvis asked — in the chat this page shows: follow along
  // here too, if nothing else is open. A side chat of another session never appears.
  if (!conv && evt.op === 'start' && inScope(evt)) {
    conv = { id: evt.id, tab: evt.tab, session: evt.session ?? null, turns: [] };
    show({ focus: false });
  }
  if (conv?.id === evt.id) apply(evt);
  else if (evt.op === 'done' && inScope(evt)) refreshHistory();
}

function apply(evt) {
  if (!conv) return;
  let turn = conv.turns[evt.turn];
  switch (evt.op) {
    case 'start':
      if (!turn) {
        conv.turns[evt.turn] = { q: evt.question || '', a: '', status: 'wait', error: '' };
        if (win?.hidden) show({ focus: false });
      }
      break;
    case 'delta':
      if (!turn) turn = conv.turns[evt.turn] = { q: '', a: '', status: 'live', error: '' };
      if (turn.status === 'stopped') return;
      turn.status = 'live';
      turn.a += evt.text || '';
      break;
    case 'done':
      if (!turn) turn = conv.turns[evt.turn] = { q: '', a: '', status: 'done', error: '' };
      turn.a = evt.text || turn.a;
      turn.status = 'done';
      if (!win || win.hidden || isMin()) unread = true;     // tell the launcher an answer is waiting
      refreshHistory();
      break;
    case 'error':
      if (turn) { turn.status = 'error'; turn.error = evt.text || 'Something went wrong'; }
      if (!win || win.hidden || isMin()) unread = true;
      break;
    case 'stopped':
      conv.turns.forEach((t) => { if (t.status === 'wait' || t.status === 'live') t.status = 'stopped'; });
      break;
    default:
      return;
  }
  renderSoon();
}

let scopeSeq = 0;

/** A cross-chat window never survives the switch itself. */
function usesCurrentChat(c) {
  const tab = activeTab();
  if (!c || !tab) return true;
  if (c.session != null) return tab.session_id === c.session;
  if (c.tab != null) return c.tab === tab.id;
  return true;
}

/** Drop everything that belongs to the chat we are leaving (the server keeps it). */
function leaveChat() {
  conv = null;
  asking = false;
  early = [];
  unread = false;
  saved = [];
  histQuery = '';
  armedForget = '';
  setHistory(false);
  if (win) win.hidden = true;
  paintBadge();
}

/** Show the side chats of the chat on screen, and only those.
 *
 * Runs on load and whenever another session is shown (a tab switch, a resumed
 * or new chat): the window and the launcher follow the session. A side chat
 * left open in the chat you come back to returns as the small bar it was; one
 * that is still being answered keeps going on the server meanwhile.
 */
async function syncScope(tab = activeTab(), prev = null) {
  const seq = ++scopeSeq;
  // A new chat's first message gives its tab a session: still the same chat.
  const sameChat = gainedSession(tab, prev);
  // The textarea swap happens first, synchronously — the old chat's pending
  // question is put away and this chat's comes back without waiting for I/O.
  swapDraft({ carry: sameChat });
  if (sameChat) {
    if (conv && conv.session == null) conv.session = tab.session_id;
  } else {
    // Another chat is shown: nothing of the one we left may linger while the
    // fetch is out — not its window (even an empty one), nor its saved list.
    leaveChat();
  }
  const res = await fetchScoped();
  if (!res || seq !== scopeSeq) return;          // a newer switch is already on its way
  staleServer = !Array.isArray(res.history);
  saved = Array.isArray(res.history) ? res.history : [];
  const open = Array.isArray(res.threads) ? res.threads : [];

  if (conv?.id && !open.some((t) => t.id === conv.id)) {
    // That window belongs to the session we just left.
    conv = null;
    asking = false;
    early = [];
    unread = false;
    setHistory(false);
    if (win) win.hidden = true;
  }
  if (!conv && open.length) {
    conv = threadToConv(open[open.length - 1]);
    show({ focus: false });
    setMin(true);
  }
  armedForget = '';
  swapDraft();                                   // the other chat's question never lingers
  paintBadge();
  if (histOpen) renderHistory();
  if (win && !win.hidden) render();
}

// ─── Rendering ───────────────────────────────────────────────────────────

function renderSoon() {
  if (renderQueued) return;
  renderQueued = true;
  requestAnimationFrame(() => { renderQueued = false; if (!paintLive()) render(); });
}

/** A chunk of text only grows the answer being written: repaint just that
 *  answer, so the words follow the model without re-parsing every turn. */
function paintLive() {
  const body = log();
  const turns = (conv?.turns || []).filter(Boolean);
  const last = turns[turns.length - 1];
  const node = body?.lastElementChild?.querySelector('.btw-a.is-live .md');
  if (!node || last?.status !== 'live' || body.children.length !== turns.length) return false;
  const stick = body.scrollHeight - body.scrollTop - body.clientHeight < 40;
  node.innerHTML = renderMarkdown(last.a, { streaming: true });
  applyMarkdownLinks(node);
  if (stick) body.scrollTop = body.scrollHeight;
  return true;
}

function turnHtml(t) {
  const q = `<div class="btw-q">${escapeHtml(t.q)}</div>`;
  let a;
  if (t.status === 'wait') {
    a = '<div class="btw-a is-wait"><span class="btw-dots" aria-label="Thinking"><i></i><i></i><i></i></span></div>';
  } else if (t.status === 'error') {
    a = `<div class="btw-a is-error"><span>${escapeHtml(t.error)}</span> <button type="button" class="link-btn" data-btw-retry>Try again</button></div>`;
  } else if (t.status === 'stopped') {
    const partial = t.a ? `<div class="md">${renderMarkdown(t.a)}</div>` : '';
    a = `<div class="btw-a is-stopped">${partial}<span class="btw-stopped">Stopped</span> <button type="button" class="link-btn" data-btw-retry>Ask again</button></div>`;
  } else {
    a = `<div class="btw-a${t.status === 'live' ? ' is-live' : ''}"><div class="md">${renderMarkdown(t.a, { streaming: t.status === 'live' })}</div></div>`;
  }
  return `<div class="btw-turn">${q}${a}</div>`;
}

function render() {
  if (!win) return;
  const body = log();
  const turns = (conv?.turns || []).filter(Boolean);   // a gap in the list must not blank the window
  const stick = body.scrollHeight - body.scrollTop - body.clientHeight < 40;
  if (!turns.length) {
    renderEmpty();
  } else {
    body.innerHTML = turns.map(turnHtml).join('');
    applyMarkdownLinks(body);
    if (stick) body.scrollTop = body.scrollHeight;
  }
  paintBadge();
  syncSend();
}

function renderEmpty() {
  const body = log();
  if (!body) return;
  const last = saved[0];
  const resume = last
    ? `<button type="button" class="btw-resume" data-btw-resume="${last.id}">
         ${icon('history')}<span><strong>Continue “${escapeHtml((last.title || 'Side chat').slice(0, 60))}”</strong>
         <em>${last.count || 0} messages · ${when(last.closed_at || last.created)}</em></span>
       </button>`
    : '';
  const badge = saved.length ? `<p class="btw-empty-hint">${saved.length} saved side ${saved.length === 1 ? 'chat' : 'chats'} — the clock above opens them.</p>` : '';
  body.innerHTML = `<div class="btw-empty">
      <p><strong>Ask anything on the side.</strong></p>
      <p>Try <em>“What’s the progress, and how long will it take?”</em> Jarvis keeps working while you read the answer.</p>
      ${resume}
      ${badge}
    </div>`;
  syncSend();
}

function syncSend() {
  const btn = $('btw-send');
  const el = input();
  if (!btn || !el) return;
  const running = runningTurn();
  btn.classList.toggle('is-stop', running);
  btn.innerHTML = running ? icon('square') : icon('arrow-up');
  btn.setAttribute('aria-label', running ? 'Stop' : 'Send');
  btn.title = running ? 'Stop (Esc)' : 'Send';
  btn.disabled = !running && !el.value.trim();
  el.placeholder = conv?.turns.length ? 'Ask a follow-up…' : 'Ask on the side…';
}

function grow() {
  const el = input();
  el.style.height = 'auto';
  el.style.height = `${Math.min(el.scrollHeight, 120)}px`;
}

// ─── Size and place ──────────────────────────────────────────────────────

function savePos() {
  if (!win || win.hidden) return;
  const r = win.getBoundingClientRect();
  storageSet(POS_KEY, JSON.stringify({ x: Math.round(r.left), y: Math.round(r.top) }));
}

function saveSize() {
  if (!win) return;
  storageSet(SIZE_KEY, JSON.stringify({ w: Math.round(win.offsetWidth), h: Math.round(win.offsetHeight) }));
}

function place(x, y) {
  const w = win.offsetWidth || 380;
  const h = win.offsetHeight || 200;
  const cx = Math.min(Math.max(MARGIN, x), Math.max(MARGIN, window.innerWidth - w - MARGIN));
  const cy = Math.min(Math.max(MARGIN, y), Math.max(MARGIN, window.innerHeight - h - MARGIN));
  win.style.left = `${cx}px`;
  win.style.top = `${cy}px`;
  win.style.right = 'auto';
  win.style.bottom = 'auto';
}

function restorePos() {
  try {
    const p = JSON.parse(storageGet(POS_KEY, 'null'));
    if (p && Number.isFinite(p.x) && Number.isFinite(p.y)) place(p.x, p.y);
  } catch { /* default corner */ }
}

function restoreSize() {
  try {
    const s = JSON.parse(storageGet(SIZE_KEY, 'null'));
    if (!s || !Number.isFinite(s.w) || !Number.isFinite(s.h)) return;
    size(Math.min(Math.max(MIN_W, s.w), window.innerWidth - MARGIN * 2),
         Math.min(Math.max(MIN_H, s.h), window.innerHeight - MARGIN * 2));
  } catch { /* default size */ }
}

function size(w, h) {
  win.style.width = `${w}px`;
  win.style.height = `${h}px`;
  win.style.maxHeight = 'none';                 // an explicit size beats the default cap
  win.style.maxWidth = 'none';
}

function keepOnScreen() {
  if (!win || win.hidden) return;
  const maxW = window.innerWidth - MARGIN * 2;
  const maxH = window.innerHeight - MARGIN * 2;
  if (win.style.width) size(Math.min(win.offsetWidth, maxW), Math.min(win.offsetHeight, maxH));
  if (win.style.left) {
    const r = win.getBoundingClientRect();
    place(r.left, r.top);
  }
}

function dragify(handle) {
  handle.addEventListener('pointerdown', (e) => {
    if (e.button !== 0 || e.target.closest('button') || window.innerWidth < 640) return;
    const r = win.getBoundingClientRect();
    const dx = e.clientX - r.left;
    const dy = e.clientY - r.top;
    handle.setPointerCapture(e.pointerId);
    win.classList.add('is-dragging');
    dragMoved = false;
    const move = (ev) => {
      if (Math.abs(ev.clientX - e.clientX) + Math.abs(ev.clientY - e.clientY) > 4) dragMoved = true;
      place(ev.clientX - dx, ev.clientY - dy);
    };
    const up = () => {
      handle.removeEventListener('pointermove', move);
      handle.removeEventListener('pointerup', up);
      handle.removeEventListener('pointercancel', up);
      win.classList.remove('is-dragging');
      savePos();
    };
    handle.addEventListener('pointermove', move);
    handle.addEventListener('pointerup', up);
    handle.addEventListener('pointercancel', up);
  });
}

/** Every edge and corner resizes the window; the size is remembered. */
function resizeify(handle, dir) {
  handle.addEventListener('pointerdown', (e) => {
    if (e.button !== 0 || window.innerWidth < 640) return;
    e.preventDefault();
    e.stopPropagation();
    const r = win.getBoundingClientRect();
    const start = { x: e.clientX, y: e.clientY, left: r.left, top: r.top, w: r.width, h: r.height };
    handle.setPointerCapture(e.pointerId);
    win.classList.add('is-resizing');
    const move = (ev) => {
      const dx = ev.clientX - start.x;
      const dy = ev.clientY - start.y;
      const maxW = window.innerWidth - MARGIN * 2;
      const maxH = window.innerHeight - MARGIN * 2;
      let w = start.w, h = start.h;
      if (dir.includes('e')) w = start.w + dx;
      if (dir.includes('s')) h = start.h + dy;
      if (dir.includes('w')) w = start.w - dx;
      if (dir.includes('n')) h = start.h - dy;
      w = Math.min(Math.max(MIN_W, w), maxW);
      h = Math.min(Math.max(MIN_H, h), maxH);
      let left = dir.includes('w') ? start.left + start.w - w : start.left;
      let top = dir.includes('n') ? start.top + start.h - h : start.top;
      left = Math.min(Math.max(MARGIN, left), Math.max(MARGIN, window.innerWidth - w - MARGIN));
      top = Math.min(Math.max(MARGIN, top), Math.max(MARGIN, window.innerHeight - h - MARGIN));
      win.style.left = `${left}px`;
      win.style.top = `${top}px`;
      win.style.right = 'auto';
      win.style.bottom = 'auto';
      size(Math.round(w), Math.round(h));
    };
    const up = () => {
      handle.removeEventListener('pointermove', move);
      handle.removeEventListener('pointerup', up);
      handle.removeEventListener('pointercancel', up);
      win.classList.remove('is-resizing');
      saveSize();
      savePos();
      const el = input();
      el.style.height = 'auto';
      el.style.height = `${Math.min(el.scrollHeight, 120)}px`;
    };
    handle.addEventListener('pointermove', move);
    handle.addEventListener('pointerup', up);
    handle.addEventListener('pointercancel', up);
  });
}

export function initBtw() {
  $('qc-btw')?.addEventListener('click', openBtwHistory);
  // The side chats follow the session on screen. `onActiveTab` also fires for the
  // tab shown at start; without a tab list yet the server answers for its own.
  let first = true;
  onActiveTab((tab, prev) => { first = false; syncScope(tab, prev); });
  if (first) syncScope();
}
