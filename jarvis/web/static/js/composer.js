/** Message composer: send / stop / queue, slash-command menu, prompt history,
 * per-device draft, quoting, one-click enhance, attachments (media.js).
 * Every send is a new message — nothing already sent is ever edited. */
import { $, escapeHtml, showToast, storageGet, storageSet, debounce, animateEl, haptic, isMac, EASE, SPRING } from './utils.js';
import { icon } from './icons.js';
import { store, subscribe, patchStore } from './store.js';
import { sendPrompt, cancelTurn, enhancePrompt } from './api.js';
import { CATALOG, LOCAL_PICKERS, LOCAL_PICKERS_WITH_ARG, LAPTOP_COMMANDS, matchItem, rankItems, getCustomItems, wantCustomItems, commandPickerArg } from './catalog.js';
import { scrollToBottom } from './chat.js';
import { quoteLines } from './quote.js';
import { trayStatus, onTrayChange, lockTray, clearTray } from './media.js';
import { editLastQueued } from './queue.js';

const HISTORY_KEY = 'jarvis-prompt-history';
const HISTORY_MAX = 50;
/** Unsent text survives a reload or a phone evicting the tab. */
const DRAFT_KEY = 'jarvis-draft';

let sending = false;
let history = [];
let historyIdx = -1;
let draft = '';
let slashItems = [];
let slashCursor = 0;
let runCatalogItem = null;
let openPicker = null;
let enhancing = false;
let enhanceGen = 0;
/** `{ original, result }` while the enhanced text is still in the box. */
let enhanceUndo = null;

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

function saveDraft() {
  storageSet(DRAFT_KEY, prompt()?.value || '');
}
const saveDraftSoon = debounce(saveDraft, 300);

function restoreDraft() {
  const el = prompt();
  const draft = storageGet(DRAFT_KEY, '');
  if (!el || !draft || el.value) return;
  el.value = draft;
  autoResizePrompt();
}

/** The arrow lifts off and drops back in; a tick of haptics on phones. */
function launchSend() {
  haptic(8);
  const ic = $('send')?.querySelector('.send-ic .ic');
  animateEl(ic, [
    { transform: 'translateY(0)', opacity: 1 },
    { transform: 'translateY(-22px)', opacity: 0, offset: 0.42 },
    { transform: 'translateY(16px)', opacity: 0, offset: 0.43 },
    { transform: 'translateY(0)', opacity: 1 },
  ], { duration: 560, easing: EASE });
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
  const fromBox = text === undefined;
  const value = String(text ?? el?.value ?? '').trim();
  // Attachments ride with what's typed in the box (not with starters / slash picks).
  const tray = fromBox ? trayStatus() : null;
  const withFiles = !!tray?.count;
  if ((!value && !withFiles) || sending) return;
  if (enhancing && fromBox) {
    showToast('Still enhancing — one moment');
    return;
  }
  if (withFiles) {
    if (/^[/!]/.test(value)) {
      showToast('Commands can’t carry attachments. Remove the files, or write a message.', true);
      return;
    }
    if (tray.uploading) {
      showToast(`Still uploading ${tray.uploading === 1 ? 'a file' : `${tray.uploading} files`}. It sends once they finish.`);
      waitForUploadsThenSend();
      return;
    }
    if (tray.failed) {
      showToast(`${tray.failed === 1 ? 'A file' : `${tray.failed} files`} didn’t upload. Retry or remove ${tray.failed === 1 ? 'it' : 'them'} first.`, true);
      return;
    }
  }

  // /command new|edit … opens the web editor instead of a dialog in the terminal.
  const commandArg = withFiles ? null : commandPickerArg(value);
  if (commandArg !== null && openPicker) {
    if (text === undefined && el) setPromptValue('');
    openPicker('command', commandArg);
    return;
  }

  const [head, ...rest] = value.split(/\s+/);
  const withArg = rest.length && LOCAL_PICKERS_WITH_ARG.has(head.toLowerCase());
  const pickerKind = LOCAL_PICKERS[value.toLowerCase()] || (withArg ? LOCAL_PICKERS[head.toLowerCase()] : '');
  if (pickerKind && openPicker && !withFiles) {
    if (text === undefined && el) setPromptValue('');
    openPicker(pickerKind, withArg ? rest.join(' ') : '');
    return;
  }

  // /new while a reply runs: a new chat beside it (as the New chat button),
  // not a queued /new that waits for the reply to end.
  if (store.busy && !withFiles && value.toLowerCase() === '/new') {
    if (text === undefined && el) setPromptValue('');
    const { newChat } = await import('./actions.js');
    newChat();
    return;
  }

  const wasBusy = store.busy;
  const files = withFiles ? tray.ready : [];
  sending = true;
  if (files.length) lockTray(true);
  syncSendButton();
  if (fromBox && el) setPromptValue('');
  closeSlash();
  launchSend();
  try {
    await sendPrompt(value, files.map((f) => f.id));
    if (files.length) clearTray();
    if (value) remember(value);
    if (LAPTOP_COMMANDS.has(value)) showToast('Opened in the terminal on your computer');
    // Queued while busy: it shows up in the queue panel above (queue.js), with
    // Send now / Edit / Remove — no toast needed. (/loop isn't queued at all.)
    else if (wasBusy && !/^\/loop(\s|$)/.test(value)) haptic(8);
    scrollToBottom(true);
  } catch (err) {
    if (files.length) lockTray(false);  // the files stay in the tray for another try
    if (fromBox && el && !el.value) setPromptValue(value);
    const why = err?.status === 503 ? 'Jarvis is not ready yet. Try again.'
      : err?.payload?.error || (err?.status ? `Message not sent (${err.message || `error ${err.status}`})` : 'Message not sent. Check the connection.');
    showToast(why, true);
  } finally {
    sending = false;
    syncSendButton();
  }
}

/** Send pressed while files are still uploading: send the moment they're done
 * (cancelled when one fails, the tray empties, or the text is cleared). */
let pendingSend = null;
function waitForUploadsThenSend() {
  if (pendingSend) return;
  $('composer')?.classList.add('is-waiting-send');
  pendingSend = onTrayChange((st) => {
    if (st.uploading) return;
    stopWaitingToSend();
    if (st.failed || !st.count) return;
    submitPrompt();
  });
  syncSendButton();
}
function stopWaitingToSend() {
  pendingSend?.();
  pendingSend = null;
  $('composer')?.classList.remove('is-waiting-send');
  syncSendButton();
}

export async function stopTurn() {
  patchStore({ stopRequested: true });
  haptic(12);
  try {
    await cancelTurn();
  } catch {
    patchStore({ stopRequested: false });
    showToast('Could not stop — check the connection', true);
  }
}

function syncSendButton() {
  const btn = $('send');
  const el = prompt();
  if (!btn || !el) return;
  const tray = trayStatus();
  const hasText = !!el.value.trim();
  const hasContent = hasText || tray.count > 0;
  const stop = store.busy && !hasContent;
  const waiting = !!pendingSend;
  btn.classList.toggle('is-stop', stop);
  btn.classList.toggle('is-uploading', !stop && tray.uploading > 0);
  btn.disabled = sending || enhancing || !store.connected || tray.locked || (!hasContent && !store.busy);
  const label = stop ? 'Stop'
    : waiting ? 'Sends when the uploads finish'
      : tray.uploading ? `Uploading ${tray.uploading === 1 ? 'a file' : `${tray.uploading} files`}…`
        : store.busy ? 'Queue message' : 'Send';
  btn.setAttribute('aria-label', label);
  btn.title = stop ? 'Stop (Esc)' : label;
  el.placeholder = store.busy
    ? 'Jarvis is working. Type to queue a follow-up'
    : tray.count ? 'Add a message, or just send the files'
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
  syncEnhanceButton();
  saveDraft();
}

export function fillPrompt(text) {
  const el = prompt();
  if (!el) return;
  setPromptValue(text);
  el.focus();
  el.setSelectionRange(el.value.length, el.value.length);
  updateSlash();
}

/** Add a quote of `text` to the message being written (quote.js). */
export function insertQuote(text) {
  const el = prompt();
  if (!el) return;
  const block = `${quoteLines(text)}\n\n`;
  const cur = el.value.replace(/\s+$/, '');
  fillPrompt(cur ? `${cur}\n\n${block}` : block);
  el.scrollTop = el.scrollHeight;
  animateEl($('composer'), [
    { transform: 'scale(1)' },
    { transform: 'scale(1.012)' },
    { transform: 'scale(1)' },
  ], { duration: 420, easing: SPRING });
}

// ─── Enhance: fix spelling & grammar with the current model ──────────────
// The corrected text replaces what's in the box; nothing is sent. The chip
// then offers Undo until the text is edited or sent.

const canEnhance = (value) => !!value.trim() && !/^\s*[/!]/.test(value);
const ENHANCE_MODES = {
  idle: { icon: 'wand-sparkles', text: 'Enhance', title: 'Fix spelling and grammar with the current model' },
  busy: { icon: '', text: 'Enhancing…', title: 'Enhancing your message — Esc cancels' },
  undo: { icon: 'rotate-ccw', text: 'Undo', title: 'Put your original message back' },
};

function syncEnhanceButton() {
  const btn = $('qc-enhance');
  const el = prompt();
  if (!btn || !el) return;
  if (enhanceUndo && el.value !== enhanceUndo.result) enhanceUndo = null;
  const mode = enhancing ? 'busy' : enhanceUndo ? 'undo' : 'idle';
  btn.hidden = mode === 'idle' && !canEnhance(el.value);
  btn.disabled = mode === 'idle' && !store.connected;
  if (btn.dataset.mode === mode) return;
  btn.dataset.mode = mode;
  const m = ENHANCE_MODES[mode];
  btn.querySelector('.en-ic').innerHTML = m.icon ? icon(m.icon) : '<span class="spinner" aria-hidden="true"></span>';
  $('qc-enhance-text').textContent = m.text;
  btn.title = m.title;
  btn.setAttribute('aria-label', m.title);
  btn.setAttribute('aria-busy', String(mode === 'busy'));
}

/** Swap the whole message; on desktop as an edit the browser can undo (⌘Z). */
function replacePromptText(text) {
  const el = prompt();
  let done = false;
  if (!matchMedia('(pointer: coarse)').matches) {
    // Focusing on a phone would pop the keyboard up — they have the Undo chip.
    el.focus();
    el.select();
    try {
      done = document.execCommand('insertText', false, text) && el.value === text;
    } catch {
      done = false;
    }
  }
  if (!done) el.value = text;
  el.setSelectionRange(el.value.length, el.value.length);
  autoResizePrompt();
  syncSendButton();
  saveDraft();
}

function finishEnhance() {
  enhancing = false;
  const el = prompt();
  if (el) el.readOnly = false;
  syncEnhanceButton();
  syncSendButton();
}

function cancelEnhance() {
  if (!enhancing) return false;
  enhanceGen += 1;
  finishEnhance();
  showToast('Enhance cancelled');
  return true;
}

/** Enhance chip / Alt+E: enhance the message, or undo the enhance just made. */
export async function enhanceMessage() {
  const el = prompt();
  if (!el || enhancing) return;
  if (enhanceUndo && el.value === enhanceUndo.result) {
    const { original } = enhanceUndo;
    enhanceUndo = null; // before the swap, so its input event doesn't count as an edit
    replacePromptText(original);
    syncEnhanceButton();
    return;
  }
  const original = el.value;
  if (!canEnhance(original) || !store.connected) return;
  closeSlash();
  enhancing = true;
  const gen = ++enhanceGen;
  el.readOnly = true;
  syncEnhanceButton();
  syncSendButton();
  haptic(8);
  let res;
  try {
    res = await enhancePrompt(original);
  } catch (err) {
    res = { ok: false, error: err?.status === 503 ? 'Jarvis is not ready yet' : 'check the connection' };
  }
  if (gen !== enhanceGen) return; // cancelled
  finishEnhance();
  if (el.value !== original) return;
  if (!res?.ok) {
    showToast(`Couldn't enhance: ${res?.error || 'unknown error'}`, true);
    return;
  }
  if (!res.changed) {
    showToast('Looks good — nothing to fix');
    return;
  }
  enhanceUndo = { original, result: res.text };
  replacePromptText(res.text);
  enhanceUndo.result = el.value; // the textarea may normalise line breaks
  syncEnhanceButton();
  haptic(6);
  animateEl($('composer'), [
    { transform: 'scale(1)' },
    { transform: 'scale(1.012)' },
    { transform: 'scale(1)' },
  ], { duration: 420, easing: SPRING });
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
  if (menu.hidden) wantCustomItems(); // opening: pick up commands made in the terminal
  // The user's own commands come first. Commands that start with what was
  // typed win; fall back to a looser match.
  const builtins = CATALOG.filter((it) => it.cmd);
  const mine = getCustomItems();
  const withCmd = [...mine, ...builtins];
  const prefix = withCmd.filter((it) => it.cmd.slice(1).toLowerCase().startsWith(q));
  const items = q
    ? rankItems(prefix.length ? prefix : withCmd.filter((it) => matchItem(it, q)), q)
    : [...mine.slice(0, 4), ...builtins]; // bare "/": a few of yours, then the built-ins
  slashItems = items.slice(0, 12);
  if (!slashItems.length) {
    closeSlash();
    return;
  }
  slashCursor = Math.min(slashCursor, slashItems.length - 1);
  // Rows cascade in only as the menu opens, not on every keystroke.
  menu.classList.toggle('is-opening', menu.hidden);
  const both = slashItems.some((it) => it.custom) && slashItems.some((it) => !it.custom);
  menu.innerHTML = slashItems.map((it, i) => {
    const head = both && (i === 0 || !!slashItems[i - 1].custom !== !!it.custom)
      ? `<div class="slash-group" role="presentation">${it.custom ? 'Your commands' : 'Built-in'}</div>`
      : '';
    const tag = it.custom
      ? `<span class="slash-tag is-mine">${it.scope === 'project' ? 'project' : 'global'}</span>`
      : it.picker ? '<span class="slash-tag">opens here</span>' : it.laptop ? '<span class="slash-tag">on computer</span>' : '';
    return `${head}
    <button type="button" class="slash-item${it.custom ? ' is-custom' : ''}${i === slashCursor ? ' is-cursor' : ''}" role="option" data-i="${i}" aria-selected="${i === slashCursor}" style="--i:${i}">
      ${icon(it.icon)}
      <span class="slash-cmd">${escapeHtml(it.cmd.trim())}${it.hint ? ` <span class="slash-hint">${escapeHtml(it.hint)}</span>` : ''}</span>
      <span class="slash-desc">${escapeHtml(it.desc)}</span>
      ${tag}
    </button>`;
  }).join('');
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

  if (e.key === 'Escape' && cancelEnhance()) {
    e.preventDefault();
    return;
  }
  if (e.key === 'Escape' && pendingSend) {
    e.preventDefault();
    stopWaitingToSend();
    showToast('Won’t send automatically. Press Enter when the uploads finish.');
    return;
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

  // ↑ in an empty box while Jarvis works: edit the newest queued message (as in the terminal).
  if (e.key === 'ArrowUp' && store.busy && el.value === '' && editLastQueued()) {
    e.preventDefault();
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
  restoreDraft();
  el?.addEventListener('input', () => {
    autoResizePrompt();
    syncSendButton();
    syncEnhanceButton();
    historyIdx = -1;
    updateSlash();
    saveDraftSoon();
  });
  window.addEventListener('pagehide', saveDraft);
  el?.addEventListener('keydown', onKeyDown);
  el?.addEventListener('blur', () => setTimeout(closeSlash, 120));
  $('send')?.addEventListener('click', onSendClick);
  const enhanceBtn = $('qc-enhance');
  enhanceBtn?.addEventListener('mousedown', (e) => e.preventDefault()); // keep the caret
  enhanceBtn?.addEventListener('click', enhanceMessage);
  document.querySelectorAll('.kbd-alt').forEach((k) => { k.textContent = isMac ? '⌥' : 'Alt'; });

  subscribe(syncSendButton);
  subscribe(syncEnhanceButton);
  onTrayChange(syncSendButton);
  window.addEventListener('resize', autoResizePrompt);
  autoResizePrompt();
  syncSendButton();
  syncEnhanceButton();
}
