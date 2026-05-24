/** Application bootstrap */
import { $, icons, showToast } from './utils.js';
import { initComposer, fillPrompt } from './composer.js';
import { initChatScroll, bindQuickCommands } from './chat.js';
import { openPickerByKind } from './pickers.js';
import { bindQueueBanner, setConnected, applySessionData } from './stream.js';
import { bindOverlayDismiss } from './modals.js';
import { initDrawer } from './drawer.js';
import { initCommandHub } from './commands.js';
import { initPickers } from './pickers.js';
import { initToolbar, loadUiPrefs } from './toolbar.js';
import { handleEvent } from './events.js';
import { connectEvents, fetchState, hasToken } from './api.js';
import { loadSnapshot } from './store.js';

async function loadInitialState() {
  if (!hasToken()) return;
  try {
    const data = await fetchState();
    loadSnapshot(data);
    applySessionData(data);
  } catch {
    showToast('Could not load session', true);
  }
}

function boot() {
  if (!hasToken()) {
    $('reconnect-banner')?.classList.add('show');
    const banner = $('reconnect-banner');
    if (banner) {
      banner.querySelector('.banner-inner').textContent =
        'Missing token — open the full URL from your terminal (includes ?token=...)';
    }
    icons();
    return;
  }

  loadUiPrefs();
  icons();

  initChatScroll();
  initComposer();
  initDrawer();
  initCommandHub();
  initPickers();
  initToolbar();
  bindQueueBanner();
  bindOverlayDismiss();
  bindQuickCommands((cmd) => {
    const pickers = { '/session': 'session', '/model': 'model', '/agent': 'agent' };
    if (pickers[cmd]) openPickerByKind(pickers[cmd]);
    else fillPrompt(cmd);
  });

  loadInitialState();

  connectEvents(
    handleEvent,
    () => setConnected(true),
    () => setConnected(false),
  );
}

if (document.readyState === 'loading') {
  document.addEventListener('DOMContentLoaded', boot);
} else {
  boot();
}
