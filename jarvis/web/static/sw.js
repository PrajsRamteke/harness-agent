/* Jarvis web remote service worker — served at /sw.js (scope "/") by web/handler.py.
 *
 * Two jobs: make the remote installable (Add to Home Screen / Install app) and
 * show notifications, both the page's own and Web Push from web/push.py, which
 * arrive even when no Jarvis page is open.
 *
 * The fetch handler below does nothing on purpose: it never answers a request,
 * so the browser fetches everything as usual. The remote is useless offline (it
 * drives a live Jarvis) and cached assets would go stale on every upgrade, but
 * Android Chrome only offers "Install app" (beforeinstallprompt) when a service
 * worker has a fetch handler.
 */
const ICON = '/static/icons/icon-192.png';
const BADGE = '/static/icons/badge-96.png';

self.addEventListener('install', () => self.skipWaiting());
self.addEventListener('activate', (event) => event.waitUntil(self.clients.claim()));
self.addEventListener('fetch', () => {}); // pass-through: see the note at the top

self.addEventListener('push', (event) => {
  let msg = {};
  if (event.data) {
    try {
      msg = event.data.json();
    } catch {
      msg = { body: event.data.text() };
    }
  }
  // Every push must show a notification (userVisibleOnly); browsers revoke
  // subscriptions that stay silent.
  event.waitUntil(self.registration.showNotification(msg.title || 'Jarvis', {
    body: msg.body || 'Open Jarvis to see what happened.',
    tag: msg.tag || 'jarvis',
    renotify: true,
    requireInteraction: !!msg.requireInteraction,
    icon: ICON,
    badge: BADGE,
    data: { url: msg.url || '/', kind: msg.kind || '' },
  }));
});

self.addEventListener('notificationclick', (event) => {
  event.notification.close();
  const target = new URL(event.notification.data?.url || '/', self.location.origin);
  event.waitUntil((async () => {
    const windows = await self.clients.matchAll({ type: 'window', includeUncontrolled: true });
    // A Jarvis page is already open: bring it forward (it keeps its own state).
    const open = windows.find((w) => new URL(w.url).pathname === target.pathname) || windows[0];
    if (open) {
      try {
        await open.focus();
        return;
      } catch { /* not allowed to focus: open a window instead */ }
    }
    await self.clients.openWindow(target.href);
  })());
});

// A new push subscription the browser rotated on its own: tell any open page
// so it re-registers it (pages also re-check on every load).
self.addEventListener('pushsubscriptionchange', (event) => {
  event.waitUntil((async () => {
    const windows = await self.clients.matchAll({ type: 'window', includeUncontrolled: true });
    windows.forEach((w) => w.postMessage({ type: 'jarvis-push-resubscribe' }));
  })());
});
