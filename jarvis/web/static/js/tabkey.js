/** Which chat tab the page shows — for the per-tab parts of the composer.
 *
 * Every chat tab is its own chat: the text you typed but didn't send
 * (composer.js) and the files waiting in the tray (media.js) stay with the tab
 * they were made in. tabs.js calls `announceActiveTab` with each tab list;
 * listeners hear only when the shown tab changes.
 *
 * The key is the tab's session id (it survives a reload and a restart of
 * Jarvis, and is unique across projects — they share one sessions.db), else
 * its tab id. No imports on purpose: tabs.js, composer.js and media.js all use it.
 */

const listeners = new Set();
let current = null;

/** The storage key of one tab: `s<session id>`, else `t<tab id>`. */
export function tabKey(tab) {
  if (!tab) return '';
  if (tab.session_id) return `s${tab.session_id}`;
  return tab.id ? `t${tab.id}` : '';
}

/** The tab on screen now (null before the first tab list arrives). */
export function activeTab() {
  return current;
}

/** `fn(tab, previous)` whenever another tab (or another chat in the tab) is shown. */
export function onActiveTab(fn) {
  listeners.add(fn);
  if (current) fn(current, null);
  return () => listeners.delete(fn);
}

/** From tabs.js: the list as it is now. */
export function announceActiveTab(list) {
  const tab = Array.isArray(list) ? list.find((t) => t.active) : null;
  if (!tab) return;
  const prev = current;
  current = tab;
  if (prev && tabKey(prev) === tabKey(tab)) return;
  for (const fn of listeners) {
    try {
      fn(tab, prev);
    } catch (err) {
      console.error(err);
    }
  }
}
