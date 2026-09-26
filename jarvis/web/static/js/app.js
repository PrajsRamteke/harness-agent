/** Application bootstrap */
import { $, escapeHtml, showToast, copyText } from './utils.js';
import { icon, hydrateIcons } from './icons.js';
import { store, loadSnapshot, loadUiPrefs } from './store.js';
import { connectEvents, fetchState, hasToken, transportMode } from './api.js';
import { handleEvent } from './events.js';
import { initChat, renderSnapshot, invalidateSnapshot } from './chat.js';
import { initComposer, fillPrompt, submitPrompt } from './composer.js';
import { initStatus, setBusy, setConnected, setQueue } from './status.js';
import { initSidebar } from './sidebar.js';
import { initModals } from './modal.js';
import { initPalette, runItem } from './palette.js';
import { initPickers, openPickerByKind } from './pickers.js';
import { initPrompts } from './prompts.js';
import { initTheme, toggleTheme } from './theme.js';
import { initQuickbar } from './quickbar.js';
import { initShortcuts } from './shortcuts.js';
import { newChat } from './actions.js';

const STARTERS = [
  { icon: 'git-branch', title: 'Review my changes', text: 'Review the uncommitted changes in this repo and point out bugs.' },
  { icon: 'flask-conical', title: 'Run the tests', text: 'Run the test suite and fix anything that fails.' },
  { icon: 'map', title: 'Explain this project', text: 'Give me a short tour of this codebase: what it does and where things live.' },
  { icon: 'history', title: 'Resume a session', picker: 'session' },
];

function renderStarters() {
  const box = $('starters');
  if (!box) return;
  box.innerHTML = STARTERS.map((s, i) => `
    <button type="button" class="starter" data-i="${i}">
      ${icon(s.icon)}
      <span><strong>${escapeHtml(s.title)}</strong><span>${escapeHtml(s.text || 'Pick up where you left off')}</span></span>
    </button>`).join('');
  box.querySelectorAll('.starter').forEach((btn) => {
    btn.addEventListener('click', () => {
      const s = STARTERS[Number(btn.dataset.i)];
      if (s.picker) openPickerByKind(s.picker);
      else fillPrompt(s.text);
    });
  });
}

function showGate(title, text) {
  const gate = $('gate');
  if (!gate) return;
  if (title) $('gate-title').textContent = title;
  if (text) $('gate-text').innerHTML = text;
  gate.hidden = false;
}

/**
 * Jarvis restarted (new token) — this tab's key is dead. The server hands the
 * current key to a bare "/" visit, so ask for it and reload in place instead
 * of leaving a page that silently receives nothing. One attempt per minute;
 * after that, explain what to do.
 */
async function recoverToken() {
  const KEY = 'jarvis-token-recover';
  let last = 0;
  try { last = Number(sessionStorage.getItem(KEY) || 0); } catch { /* private mode */ }
  if (Date.now() - last > 60_000) {
    try {
      sessionStorage.setItem(KEY, String(Date.now()));
    } catch { /* ignore */ }
    try {
      const res = await fetch('/', { redirect: 'follow', cache: 'no-store' });
      const fresh = new URL(res.url).searchParams.get('token');
      if (res.ok && fresh && fresh !== new URLSearchParams(location.search).get('token')) {
        location.replace(`/?token=${encodeURIComponent(fresh)}`);
        return;
      }
    } catch { /* server down — fall through */ }
  }
  showGate(
    'This link has expired',
    'Jarvis was restarted, so this page’s key no longer works. Open the new link printed in your terminal after <code>jarvis --web</code>.',
  );
}

async function loadInitialState() {
  try {
    const data = await fetchState();
    loadSnapshot(data);
    renderSnapshot(data);
    setBusy(!!data.busy);
    setQueue(data.queue || []);
  } catch (err) {
    if (err?.status === 401) {
      recoverToken();
      return;
    }
    showToast('Could not load the session yet', true);
  }
}

function boot() {
  try { sessionStorage.removeItem('jarvis-boot-retry'); } catch { /* private mode */ }
  initTheme();
  hydrateIcons(document);
  $('gate-retry')?.addEventListener('click', () => location.reload());

  if (!hasToken()) {
    showGate();
    return;
  }

  loadUiPrefs();
  initModals();
  initChat({ onReuse: fillPrompt });
  initStatus();
  initComposer({ onCatalogItem: runItem, onOpenPicker: openPickerByKind });
  initSidebar({ onOpenPicker: openPickerByKind });
  initPalette({ onOpenPicker: openPickerByKind });
  initPickers();
  initPrompts();
  initQuickbar({ onOpenPicker: openPickerByKind });
  initShortcuts({ newChat, openPicker: openPickerByKind, toggleTheme });
  renderStarters();

  $('copy-link')?.addEventListener('click', async () => {
    if (await copyText(store.remoteUrl || location.href)) showToast('Link copied — open it on any device on your network');
    else showToast('Copy failed — copy the address bar instead', true);
  });

  loadInitialState();

  connectEvents({
    onMessage: handleEvent,
    onConnect: () => setConnected(true),
    onDisconnect: () => {
      setConnected(false);
      invalidateSnapshot();
    },
    onResync: invalidateSnapshot,
    onUnauthorized: recoverToken,
  });

  // Focus the composer on desktop so typing just works.
  if (window.matchMedia('(min-width: 961px)').matches) $('prompt')?.focus();

  // Type anywhere to start writing.
  document.addEventListener('keydown', (e) => {
    if (e.defaultPrevented || e.metaKey || e.ctrlKey || e.altKey || e.key.length !== 1) return;
    if (document.body.classList.contains('has-modal')) return;
    if (e.target.closest?.('input, textarea, [contenteditable="true"]')) return;
    $('prompt')?.focus();
  });

  window.jarvisRemote = { store, submitPrompt, transport: transportMode };
}

if (document.readyState === 'loading') {
  document.addEventListener('DOMContentLoaded', boot);
} else {
  boot();
}
