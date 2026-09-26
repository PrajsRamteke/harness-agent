/** Appearance: light / dark / system mode, accent colour, device preferences */
import { $, escapeHtml, storageGet, storageSet } from './utils.js';
import { icon } from './icons.js';
import { patchStore } from './store.js';
import { openModal } from './modal.js';

export const MODES = [
  { id: 'system', label: 'System', icon: 'monitor' },
  { id: 'light', label: 'Light', icon: 'sun' },
  { id: 'dim', label: 'Soft dark', icon: 'cloud-moon' },
  { id: 'dark', label: 'Dark', icon: 'moon' },
];

const THEME_COLOR = { light: '#f6f6f5', dim: '#1c1d21', dark: '#000000' };
export const THEME_LABEL = { light: 'Light', dim: 'Soft dark', dark: 'Dark' };

export const ACCENTS = [
  { id: 'green', label: 'Green', dark: '#4ade80', light: '#16a34a' },
  { id: 'blue', label: 'Blue', dark: '#60a5fa', light: '#2563eb' },
  { id: 'teal', label: 'Teal', dark: '#2dd4bf', light: '#0d9488' },
  { id: 'orange', label: 'Orange', dark: '#fb923c', light: '#ea580c' },
  { id: 'rose', label: 'Rose', dark: '#fb7185', light: '#e11d48' },
  { id: 'mono', label: 'Mono', dark: '#f5f5f5', light: '#171717' },
];

const systemLight = window.matchMedia('(prefers-color-scheme: light)');

export const prefs = {
  mode: storageGet('jarvis-theme', 'dark'),
  accent: storageGet('jarvis-accent', 'green'),
  alert: storageGet('jarvis-alert', '1') !== '0',
  compact: storageGet('jarvis-compact', '0') === '1',
  // Which dark the light/dark flip returns to: 'dark' or 'dim'.
  darkVariant: storageGet('jarvis-dark-variant', 'dark') === 'dim' ? 'dim' : 'dark',
};

/** 'light' | 'dim' | 'dark' — what is actually on screen. */
export function resolvedTheme() {
  if (prefs.mode === 'system') return systemLight.matches ? 'light' : 'dark';
  if (prefs.mode === 'light' || prefs.mode === 'dim') return prefs.mode;
  return 'dark';
}

export function isLightTheme() {
  return resolvedTheme() === 'light';
}

function apply() {
  const root = document.documentElement;
  const theme = resolvedTheme();
  root.dataset.theme = theme;
  root.dataset.accent = ACCENTS.some((a) => a.id === prefs.accent) ? prefs.accent : 'green';
  document.body.classList.toggle('is-compact', prefs.compact);
  document.querySelector('meta[name="theme-color"]')?.setAttribute('content', THEME_COLOR[theme]);
  patchStore({ theme, themeMode: prefs.mode, accent: prefs.accent });
}

export function setMode(mode) {
  prefs.mode = MODES.some((m) => m.id === mode) ? mode : 'dark';
  storageSet('jarvis-theme', prefs.mode);
  if (prefs.mode === 'dark' || prefs.mode === 'dim') {
    prefs.darkVariant = prefs.mode;
    storageSet('jarvis-dark-variant', prefs.mode);
  }
  apply();
  paintAppearance();
}

export function setAccent(accent) {
  prefs.accent = accent;
  storageSet('jarvis-accent', accent);
  apply();
  paintAppearance();
}

/** Quick flip between light and dark (sidebar button, palette). */
export function toggleTheme() {
  setMode(isLightTheme() ? prefs.darkVariant : 'light');
}

function notificationsUsable() {
  return 'Notification' in window && window.isSecureContext;
}

async function setAlert(on) {
  prefs.alert = on;
  storageSet('jarvis-alert', on ? '1' : '0');
  if (on && notificationsUsable() && Notification.permission === 'default') {
    try {
      await Notification.requestPermission();
    } catch { /* ignored */ }
  }
  paintAppearance();
}

function setCompact(on) {
  prefs.compact = on;
  storageSet('jarvis-compact', on ? '1' : '0');
  apply();
}

// ─── Reply-ready alert ────────────────────────────────────────────────────

let restoreTitle = null;

/** Called when a turn finishes. Flags the tab (and notifies) if nobody is looking. */
export function replyReady(summary = '') {
  if (!prefs.alert || !document.hidden) return;
  if (restoreTitle === null) restoreTitle = document.title;
  document.title = '✓ Reply ready — Jarvis';
  if (notificationsUsable() && Notification.permission === 'granted') {
    try {
      const n = new Notification('Jarvis finished', {
        body: summary || 'Your reply is ready.',
        tag: 'jarvis-reply',
      });
      n.onclick = () => {
        window.focus();
        n.close();
      };
    } catch { /* some mobile browsers only allow service-worker notifications */ }
  }
  navigator.vibrate?.(60);
}

export function isAlertTitle() {
  return restoreTitle !== null;
}

document.addEventListener('visibilitychange', () => {
  if (!document.hidden && restoreTitle !== null) {
    document.title = restoreTitle;
    restoreTitle = null;
  }
});

// ─── Appearance dialog ────────────────────────────────────────────────────

function paintAppearance() {
  const modes = $('mode-grid');
  if (!modes) return;
  modes.innerHTML = MODES.map((m) => `
    <button type="button" class="mode-card" role="radio" data-mode="${m.id}" aria-checked="${prefs.mode === m.id}">
      <span class="mode-preview mode-${m.id}" aria-hidden="true"><i></i><i></i><i></i></span>
      <span class="mode-label">${icon(m.icon)}<span>${escapeHtml(m.label)}</span></span>
    </button>`).join('');
  modes.querySelectorAll('[data-mode]').forEach((btn) => {
    btn.addEventListener('click', () => setMode(btn.dataset.mode));
  });

  const light = isLightTheme();
  const accents = $('accent-grid');
  accents.innerHTML = ACCENTS.map((a) => `
    <button type="button" class="accent-chip" role="radio" data-accent="${a.id}" aria-checked="${prefs.accent === a.id}" style="--swatch:${light ? a.light : a.dark}">
      <span class="accent-dot">${prefs.accent === a.id ? icon('check') : ''}</span>
      <span>${escapeHtml(a.label)}</span>
    </button>`).join('');
  accents.querySelectorAll('[data-accent]').forEach((btn) => {
    btn.addEventListener('click', () => setAccent(btn.dataset.accent));
  });

  const alert = $('sw-alert');
  if (alert) alert.checked = prefs.alert;
  const sub = $('alert-sub');
  if (sub) {
    sub.textContent = notificationsUsable()
      ? (Notification.permission === 'denied' ? 'Notifications are blocked; the tab title still changes' : 'Notification + tab title while this tab is hidden')
      : 'Changes the tab title (browser notifications need localhost or HTTPS)';
  }
  const compact = $('sw-compact');
  if (compact) compact.checked = prefs.compact;
}

export function openAppearance() {
  paintAppearance();
  openModal('appearance');
}

export function initTheme() {
  apply();
  systemLight.addEventListener?.('change', () => {
    if (prefs.mode === 'system') {
      apply();
      paintAppearance();
    }
  });
  $('sw-alert')?.addEventListener('change', (e) => setAlert(e.target.checked));
  $('sw-compact')?.addEventListener('change', (e) => setCompact(e.target.checked));
}
