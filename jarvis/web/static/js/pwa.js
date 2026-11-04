/**
 * Installable app + push notifications for the web remote.
 *
 *  - Registers /sw.js (static/sw.js, scope "/"), which makes the remote installable
 *    and shows notifications.
 *  - "Push notifications" (Appearance → This device) subscribes this browser to
 *    Web Push from web/push.py. Alerts then arrive with the page closed: a long turn
 *    finished, or Jarvis waiting on an approval or question.
 *  - Presence: while this page is on screen it tells the Jarvis it shows, every
 *    30 s, and that Jarvis sends no push (you're already looking).
 *  - notifyLocal(): the page's own in-tab alerts (theme.js) go through the service
 *    worker when there is one. Android Chrome only allows notifications that way.
 */
import { api } from './api.js';
import { $, BASE, readToken, showToast } from './utils.js';

const PUSH_PREF = 'jarvis-push';
const PRESENCE_MS = 30_000;
const ICON = '/static/icons/icon-192.png';
const BADGE = '/static/icons/badge-96.png';

const clientId = Math.random().toString(36).slice(2) + Date.now().toString(36);
let registration = null;
let installEvent = null;
let presenceTimer = 0;
let busy = false;
const push = { available: false, subscribed: false, error: '' };

export function isStandalone() {
  return window.matchMedia?.('(display-mode: standalone)').matches || navigator.standalone === true;
}

function isIos() {
  return /iPad|iPhone|iPod/.test(navigator.userAgent)
    || (navigator.platform === 'MacIntel' && navigator.maxTouchPoints > 1);
}

function swSupported() {
  return 'serviceWorker' in navigator && window.isSecureContext;
}

function pushSupported() {
  return swSupported() && 'PushManager' in window && 'Notification' in window;
}

function b64uToBytes(text) {
  const b64 = text.replace(/-/g, '+').replace(/_/g, '/') + '='.repeat((4 - (text.length % 4)) % 4);
  return Uint8Array.from(atob(b64), (c) => c.charCodeAt(0));
}

function sameKey(sub, publicKey) {
  const current = sub?.options?.applicationServerKey;
  if (!current) return true; // the browser doesn't say: assume it's ours
  const a = new Uint8Array(current);
  const b = b64uToBytes(publicKey);
  return a.length === b.length && a.every((v, i) => v === b[i]);
}

function deviceLabel() {
  const ua = navigator.userAgent;
  const os = /iPhone|iPad|iPod/.test(ua) ? 'iOS' : /Android/.test(ua) ? 'Android'
    : /Mac OS X/.test(ua) ? 'macOS' : /Windows/.test(ua) ? 'Windows' : /Linux/.test(ua) ? 'Linux' : '';
  const browser = /Edg\//.test(ua) ? 'Edge' : /Firefox\//.test(ua) ? 'Firefox'
    : /Chrome\//.test(ua) ? 'Chrome' : /Safari\//.test(ua) ? 'Safari' : 'Browser';
  return [browser, os].filter(Boolean).join(' on ') + (isStandalone() ? ' (app)' : '');
}

/** Is Web Push delivering alerts to this device? Then the page skips its own notifications. */
export function pushActive() {
  return push.subscribed && 'Notification' in window && Notification.permission === 'granted';
}

/** A notification from the page itself, through the service worker when there is one. */
export function notifyLocal(title, { body = '', tag = 'jarvis', requireInteraction = false } = {}) {
  if (!('Notification' in window) || !window.isSecureContext || Notification.permission !== 'granted') return;
  const options = { body, tag, requireInteraction, icon: ICON, badge: BADGE, data: { url: location.href } };
  if (registration) {
    registration.showNotification(title, options).catch(() => {});
    return;
  }
  try {
    const n = new Notification(title, options);
    n.onclick = () => {
      window.focus();
      n.close();
    };
  } catch { /* some mobile browsers only allow service-worker notifications */ }
}

// ─── Push subscription ───────────────────────────────────────────────────

async function pushConfig(endpoint = '') {
  const q = endpoint ? `?endpoint=${encodeURIComponent(endpoint)}` : '';
  return api(`/api/push/config${q}`);
}

function unsupportedReason() {
  if (isIos() && !isStandalone()) {
    return 'On iPhone and iPad: tap Share → Add to Home Screen, open Jarvis from there, then turn this on.';
  }
  if (!window.isSecureContext) {
    return 'Needs HTTPS (the Anywhere link) or localhost. This address is plain http.';
  }
  return 'This browser can’t receive push notifications.';
}

async function subscribe() {
  if (!pushSupported()) throw new Error(unsupportedReason());
  const permission = await Notification.requestPermission();
  if (permission !== 'granted') {
    throw new Error(permission === 'denied'
      ? 'Notifications are blocked for this site. Allow them in the browser’s site settings.'
      : 'Notifications were not allowed.');
  }
  const cfg = await pushConfig();
  if (!cfg.available || !cfg.public_key) {
    throw new Error(cfg.error || 'This Jarvis can’t send push notifications.');
  }
  const reg = await navigator.serviceWorker.ready;
  let sub = await reg.pushManager.getSubscription();
  if (sub && !sameKey(sub, cfg.public_key)) {
    await sub.unsubscribe().catch(() => {});
    sub = null;
  }
  if (!sub) {
    sub = await reg.pushManager.subscribe({ userVisibleOnly: true, applicationServerKey: b64uToBytes(cfg.public_key) });
  }
  const res = await api('/api/push/subscribe', 'POST', { subscription: sub.toJSON(), label: deviceLabel() });
  if (!res.ok) throw new Error(res.error || 'Jarvis refused the subscription.');
  push.available = true;
  push.subscribed = true;
  push.error = '';
  localStorage.setItem(PUSH_PREF, '1');
}

async function unsubscribe() {
  localStorage.setItem(PUSH_PREF, '0');
  push.subscribed = false;
  const reg = registration || (swSupported() ? await navigator.serviceWorker.getRegistration('/') : null);
  const sub = await reg?.pushManager?.getSubscription();
  if (!sub) return;
  await api('/api/push/unsubscribe', 'POST', { endpoint: sub.endpoint }).catch(() => {});
  await sub.unsubscribe().catch(() => {});
}

/** On load: the browser may still hold a subscription Jarvis forgot (or that uses an old key). */
async function reconcile() {
  if (!readToken()) return;
  try {
    // Asked even where this browser can't subscribe: its presence still keeps
    // other devices quiet while it's on screen.
    const sub = pushSupported() && registration ? await registration.pushManager.getSubscription() : null;
    const cfg = await pushConfig(sub?.endpoint || '');
    push.available = !!cfg.available;
    push.error = cfg.error || '';
    const wanted = localStorage.getItem(PUSH_PREF) === '1';
    if (!pushSupported() || !registration || !wanted || !cfg.available || Notification.permission !== 'granted') {
      push.subscribed = false;
      return;
    }
    if (sub && cfg.subscribed && sameKey(sub, cfg.public_key)) {
      push.subscribed = true;
      return;
    }
    await subscribe(); // re-register silently: permission is already granted
  } catch (err) {
    push.subscribed = false;
    push.error = err?.message || String(err);
  } finally {
    paint();
  }
}

async function setPush(on) {
  if (busy) return;
  busy = true;
  paint();
  try {
    if (on) {
      await subscribe();
      showToast('Push notifications are on for this device');
    } else {
      await unsubscribe();
    }
  } catch (err) {
    push.subscribed = false;
    localStorage.setItem(PUSH_PREF, '0');
    showToast(err?.message || String(err), true);
  } finally {
    busy = false;
    paint();
  }
}

async function sendTest() {
  const sub = await registration?.pushManager?.getSubscription();
  if (!sub) {
    showToast('This device is not subscribed.', true);
    return;
  }
  try {
    const res = await api('/api/push/test', 'POST', { endpoint: sub.endpoint });
    if (res.ok) showToast('Test sent. It should appear in a moment');
    else showToast(res.error || 'The test failed.', true);
  } catch (err) {
    showToast(err?.message || String(err), true);
  }
}

// ─── Presence ────────────────────────────────────────────────────────────

function reportPresence(visible) {
  if (!readToken() || !push.available) return;
  if (!visible && navigator.sendBeacon) {
    // pagehide / hidden: a beacon still goes out while the page is being frozen.
    const url = `${BASE}/api/push/presence?token=${encodeURIComponent(readToken())}`;
    const body = new Blob([JSON.stringify({ client: clientId, visible: false })], { type: 'application/json' });
    if (navigator.sendBeacon(url, body)) return;
  }
  api('/api/push/presence', 'POST', { client: clientId, visible }).catch(() => {});
}

function syncPresence() {
  const visible = document.visibilityState === 'visible';
  reportPresence(visible);
  clearInterval(presenceTimer);
  presenceTimer = visible ? setInterval(() => reportPresence(true), PRESENCE_MS) : 0;
}

// ─── Install ─────────────────────────────────────────────────────────────

export function canInstall() {
  return !!installEvent;
}

export async function promptInstall() {
  const evt = installEvent;
  if (!evt) {
    showToast(isIos()
      ? 'Tap Share → Add to Home Screen to install Jarvis.'
      : window.isSecureContext
        ? 'Open the browser menu (⋮) → Install app, or Add to Home screen.'
        : 'Installing needs HTTPS (the Anywhere link). This address is plain http.');
    return;
  }
  installEvent = null;
  paint();
  try {
    await evt.prompt();
    await evt.userChoice;
  } catch { /* the browser declined to show it */ }
}

// ─── Settings rows (Appearance → This device) ────────────────────────────

function paint() {
  const sw = $('sw-push');
  const sub = $('push-sub');
  const row = sw?.closest('.switch-row');
  const supported = pushSupported();
  if (sw) {
    sw.checked = push.subscribed;
    sw.disabled = busy || (!supported && !push.subscribed);
  }
  row?.classList.toggle('is-disabled', !!sw?.disabled && !busy);
  if (sub) {
    let text;
    if (busy) text = 'Setting up…';
    else if (!supported) text = unsupportedReason();
    else if ('Notification' in window && Notification.permission === 'denied') text = 'Blocked in this browser’s site settings';
    else if (push.subscribed) text = 'On · long replies and approvals, even with Jarvis closed';
    else if (push.error && !push.available) text = push.error;
    else text = 'Alerts when a long reply finishes or Jarvis needs you, even with this page closed';
    sub.textContent = text;
  }
  const test = $('push-test');
  if (test) test.hidden = !push.subscribed;
  const install = $('pwa-install');
  // Always offered outside the installed app: when the browser hasn't handed us its
  // install prompt (it decides when), tapping explains the menu route instead.
  if (install) install.hidden = isStandalone();
  const actions = $('pwa-actions');
  if (actions) actions.hidden = !!test?.hidden && !!install?.hidden;
}

export function initPwa() {
  $('sw-push')?.addEventListener('change', (e) => setPush(e.target.checked));
  $('push-test')?.addEventListener('click', sendTest);
  $('pwa-install')?.addEventListener('click', promptInstall);

  window.addEventListener('beforeinstallprompt', (e) => {
    e.preventDefault(); // offered from Appearance → This device and the ⌘K palette instead
    installEvent = e;
    paint();
  });
  window.addEventListener('appinstalled', () => {
    installEvent = null;
    paint();
  });

  document.addEventListener('visibilitychange', syncPresence);
  window.addEventListener('pagehide', () => reportPresence(false));
  paint();

  if (!swSupported()) {
    reconcile().then(syncPresence);
    return;
  }
  navigator.serviceWorker.register('/sw.js', { scope: '/' })
    .then((reg) => {
      registration = reg;
    })
    .catch((err) => {
      push.error = err?.message || String(err);
    })
    .then(reconcile)
    .then(syncPresence);
  navigator.serviceWorker.addEventListener('message', (e) => {
    if (e.data?.type === 'jarvis-push-resubscribe') reconcile();
  });
}
