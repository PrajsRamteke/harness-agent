/** Message composer: send / stop / queue, slash-command menu, prompt history */
import { $, escapeHtml, showToast, storageGet, storageSet } from './utils.js';
import { icon } from './icons.js';
import { store, subscribe } from './store.js';
import { sendPrompt, cancelTurn } from './api.js';
import { CATALOG, LOCAL_PICKERS, LAPTOP_COMMANDS, matchItem, rankItems } from './catalog.js';
import { scrollToBottom } from './chat.js';

const HISTORY_KEY = 'jarvis-prompt-history';
const HISTORY_MAX = 50;

let sending = false;
let history = [];
let historyIdx = -1;
let draft = '';
let slashItems = [];
let slashCursor = 0;
let runCatalogItem = null;
let openPicker = null;

const prompt = () => $('prompt');

export function autoResizePrompt() {
  const el = prompt();
  if (!el) return;
  el.style.height = 'auto';
  el.style.height = `${Math.min(el.scrollHeight, Math.round(window.innerHeight * 0.4))}px`;
}

function loadHistory() {
  try {
    const raw = JSON.parse(storageGet(HISTORY_KEY, '[]'));
    history = Array.isArray(raw) ? raw.filter((x) => typeof x === 'string') : [];
  } catch {
    history = [];
  }
}

function remember(text) {
  history = history.filter((h) => h !== text);
  history.push(text);
  if (history.length > HISTORY_MAX) history = history.slice(-HISTORY_MAX);
  storageSet(HISTORY_KEY, JSON.stringify(history));
  historyIdx = -1;
}

// ─── Send / stop ──────────────────────────────────────────────────────────

export async function submitPrompt(text) {
  const el = prompt();
  const value = String(text ?? el?.value ?? '').trim();
  if (!value || sending) return;

  const pickerKind = LOCAL_PICKERS[value.toLowerCase()];
  if (pickerKind && openPicker) {
    if (text === undefined && el) setPromptValue('');
    openPicker(pickerKind);
    return;
  }

  const wasBusy = store.busy;
  sending = true;
  syncSendButton();
  if (text === undefined && el) setPromptValue('');
  closeSlash();
  try {
    await sendPrompt(value);
    remember(value);
    if (LAPTOP_COMMANDS.has(value)) showToast('Opened in the terminal on your computer');
    else if (wasBusy) showToast('Queued — sends when Jarvis is free');
    scrollToBottom(true);
  } catch (err) {
    if (text === undefined && el && !el.value) setPromptValue(value);
    showToast(err?.status === 503 ? 'Jarvis is not ready yet — try again' : 'Message not sent — check the connection', true);
  } finally {
    sending = false;
    syncSendButton();
  }
}

export async function stopTurn() {
  try {
    await cancelTurn();
  } catch {
    showToast('Could not stop — check the connection', true);
  }
}

function syncSendButton() {
  const btn = $('send');
  const el = prompt();
  if (!btn || !el) return;
  const hasText = !!el.value.trim();
  const stop = store.busy && !hasText;
  btn.classList.toggle('is-stop', stop);
  btn.disabled = sending || !store.connected || (!hasText && !store.busy);
  const label = stop ? 'Stop' : store.busy ? 'Queue message' : 'Send';
  btn.setAttribute('aria-label', label);
  btn.title = stop ? 'Stop (Esc)' : label;
  el.placeholder = store.busy
    ? 'Jarvis is working. Type to queue a follow-up'
    : 'Message Jarvis, or type / for commands';
}

function onSendClick(e) {
  e.preventDefault();
  const btn = $('send');
  if (btn?.classList.contains('is-stop')) stopTurn();
  else submitPrompt();
}

export function setPromptValue(text) {
  const el = prompt();
  if (!el) return;
  el.value = text;
  autoResizePrompt();
  syncSendButton();
}

export function fillPrompt(text) {
  const el = prompt();
  if (!el) return;
  setPromptValue(text);
  el.focus();
  el.setSelectionRange(el.value.length, el.value.length);
  updateSlash();
}

// ─── Slash menu ───────────────────────────────────────────────────────────

function slashQuery() {
  const v = (prompt()?.value || '').toLowerCase();
  if (!v.startsWith('/') || v.includes('\n')) return null;
  // Past the first word only while it still completes a longer command
  // ("/agent i" → "/agent init"); otherwise the user is typing arguments.
  if (/\s/.test(v) && !CATALOG.some((it) => it.cmd?.toLowerCase().startsWith(v) && it.cmd.trim().toLowerCase() !== v.trim())) {
    return null;
  }
  return v.slice(1);
}

function updateSlash() {
  const menu = $('slash-menu');
  const q = slashQuery();
  if (!menu) return;
  if (q === null) {
    closeSlash();
    return;
  }
  // Commands that start with what was typed win; fall back to a looser match.
  const withCmd = CATALOG.filter((it) => it.cmd);
  const prefix = withCmd.filter((it) => it.cmd.slice(1).toLowerCase().startsWith(q));
  const items = prefix.length ? prefix : withCmd.filter((it) => matchItem(it, q));
  slashItems = rankItems(items, q).slice(0, 9);
  if (!slashItems.length) {
    closeSlash();
    return;
  }
  slashCursor = Math.min(slashCursor, slashItems.length - 1);
  menu.innerHTML = slashItems.map((it, i) => `
    <button type="button" class="slash-item${i === slashCursor ? ' is-cursor' : ''}" role="option" data-i="${i}" aria-selected="${i === slashCursor}">
      ${icon(it.icon)}
      <span class="slash-cmd">${escapeHtml(it.cmd.trim())}</span>
      <span class="slash-desc">${escapeHtml(it.desc)}</span>
      ${it.picker ? '<span class="slash-tag">opens here</span>' : it.laptop ? '<span class="slash-tag">on computer</span>' : ''}
    </button>`).join('');
  menu.hidden = false;
  menu.querySelectorAll('.slash-item').forEach((btn) => {
    btn.addEventListener('mousedown', (e) => e.preventDefault());
    btn.addEventListener('click', () => pickSlash(Number(btn.dataset.i)));
  });
}

function moveSlash(delta) {
  if (!slashItems.length) return;
  slashCursor = (slashCursor + delta + slashItems.length) % slashItems.length;
  const menu = $('slash-menu');
  menu.querySelectorAll('.slash-item').forEach((el, i) => {
    el.classList.toggle('is-cursor', i === slashCursor);
    el.setAttribute('aria-selected', String(i === slashCursor));
  });
  menu.querySelector('.is-cursor')?.scrollIntoView({ block: 'nearest' });
}

function pickSlash(i) {
  const it = slashItems[i];
  if (!it) return;
  closeSlash();
  if (it.fill) {
    fillPrompt(it.cmd);
    return;
  }
  setPromptValue('');
  if (runCatalogItem) runCatalogItem(it);
  else submitPrompt(it.cmd);
}

function closeSlash() {
  const menu = $('slash-menu');
  if (menu) menu.hidden = true;
  slashItems = [];
  slashCursor = 0;
}

// ─── History ──────────────────────────────────────────────────────────────

function recall(delta) {
  if (!history.length) return false;
  const el = prompt();
  if (historyIdx === -1) {
    if (delta > 0) return false;
    draft = el.value;
    historyIdx = history.length;
  }
  const next = historyIdx + delta;
  if (next < 0) return true;
  if (next >= history.length) {
    historyIdx = -1;
    setPromptValue(draft);
    return true;
  }
  historyIdx = next;
  setPromptValue(history[historyIdx]);
  el.setSelectionRange(el.value.length, el.value.length);
  return true;
}

// ─── Wiring ───────────────────────────────────────────────────────────────

function onKeyDown(e) {
  const el = e.currentTarget;
  const slashOpen = !$('slash-menu')?.hidden;

  if (slashOpen) {
    if (e.key === 'ArrowDown') { e.preventDefault(); moveSlash(1); return; }
    if (e.key === 'ArrowUp') { e.preventDefault(); moveSlash(-1); return; }
    if (e.key === 'Tab') {
      e.preventDefault();
      const it = slashItems[slashCursor];
      if (it) fillPrompt(it.fill ? it.cmd : `${it.cmd.trim()}`);
      return;
    }
    if (e.key === 'Enter' && !e.shiftKey && !e.isComposing) {
      e.preventDefault();
      pickSlash(slashCursor);
      return;
    }
    if (e.key === 'Escape') { e.preventDefault(); closeSlash(); return; }
  }

  if (e.key === 'Enter' && !e.shiftKey && !e.isComposing && e.keyCode !== 229) {
    e.preventDefault();
    submitPrompt();
    return;
  }

  if (e.key === 'Escape' && store.busy && !el.value) {
    e.preventDefault();
    stopTurn();
    return;
  }

  const atStart = el.selectionStart === 0 && el.selectionEnd === 0;
  const atEnd = el.selectionStart === el.value.length;
  if (e.key === 'ArrowUp' && (el.value === '' || (historyIdx !== -1 && atStart) || (atStart && !el.value.includes('\n')))) {
    if (recall(-1)) e.preventDefault();
  } else if (e.key === 'ArrowDown' && historyIdx !== -1 && atEnd) {
    if (recall(1)) e.preventDefault();
  }
}

export function initComposer({ onCatalogItem, onOpenPicker } = {}) {
  runCatalogItem = onCatalogItem;
  openPicker = onOpenPicker;
  loadHistory();

  const el = prompt();
  el?.addEventListener('input', () => {
    autoResizePrompt();
    syncSendButton();
    historyIdx = -1;
    updateSlash();
  });
  el?.addEventListener('keydown', onKeyDown);
  el?.addEventListener('blur', () => setTimeout(closeSlash, 120));
  $('send')?.addEventListener('click', onSendClick);

  subscribe(syncSendButton);
  window.addEventListener('resize', autoResizePrompt);
  autoResizePrompt();
  syncSendButton();
}
