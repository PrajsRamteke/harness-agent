/** "Open on your phone" dialog — QR code + link, and the Anywhere switch
 * (a public tunnel, same as `/web anywhere`) so a phone on another network
 * can open this session too. */
import { $, showToast, copyText, readToken, BASE } from './utils.js';
import { store } from './store.js';
import { openModal } from './modal.js';
import { shareLink } from './projects.js';
import { fetchTunnel, setTunnel, fetchTerminalTunnel, setTerminalTunnel } from './api.js';

const isLocalHost = () => ['localhost', '127.0.0.1', '[::1]'].includes(location.hostname);

/** Last known tunnel state: { status: off|starting|live|error|missing|unavailable, link, error, can_toggle, awake } */
let tunnel = { status: 'off', link: '', can_toggle: false };
/** "Terminal on the link": { on, can_toggle } — a tunnel visitor gets a shell only while on. */
let termTunnel = { on: false, can_toggle: false };
let pollTimer = null;
let drawnFor = '';

const isOpen = () => !$('qr')?.hidden;

/** The link the QR carries: the public one while Anywhere is live, else this network's. */
function currentLink() {
  if (tunnel.status === 'live' && tunnel.link) {
    if (!BASE) return tunnel.link;
    try {
      const url = new URL(tunnel.link);
      url.pathname = `${BASE}/`; // the project on screen, through the same tunnel
      return url.toString();
    } catch { /* fall through */ }
  }
  return shareLink();
}

async function drawQr(link) {
  const frame = $('qr-frame');
  if (!frame || link === drawnFor) return;
  drawnFor = link;
  frame.textContent = 'Loading…';
  try {
    const token = readToken();
    const res = await fetch(`${BASE}/api/qr?url=${encodeURIComponent(link)}`, {
      headers: token ? { Authorization: `Bearer ${token}` } : {},
    });
    if (!res.ok) throw new Error(String(res.status));
    if (drawnFor !== link) return; // a newer link is being drawn
    frame.innerHTML = await res.text();
    frame.firstElementChild?.setAttribute('role', 'img');
    frame.firstElementChild?.setAttribute('aria-label', 'QR code for this session link');
  } catch {
    if (drawnFor === link) frame.textContent = 'Could not create the QR code.';
  }
}

function tunnelText() {
  switch (tunnel.status) {
    case 'starting': return 'Starting a secure tunnel…';
    case 'live': return `On · works from any network${tunnel.awake ? ' · Mac kept awake' : ''}`;
    case 'error': return tunnel.error || 'The tunnel could not start';
    case 'missing': return 'Needs cloudflared or ngrok — brew install cloudflared';
    case 'unavailable': return tunnel.error || 'Not available here';
    default: return tunnel.can_toggle ? 'Off · works on this network only' : 'Off · turn it on from your computer';
  }
}

function render() {
  const wrap = $('qr-anywhere');
  const sw = $('sw-anywhere');
  if (!wrap || !sw) return;
  const { status } = tunnel;
  wrap.dataset.status = status;
  wrap.classList.toggle('is-live', status === 'live');
  sw.checked = status === 'live' || status === 'starting';
  sw.disabled = !tunnel.can_toggle || status === 'missing' || status === 'unavailable';
  sw.closest('.switch-row')?.classList.toggle('is-disabled', sw.disabled);
  $('qr-anywhere-sub').textContent = tunnelText();
  $('qr-anywhere-spin').hidden = status !== 'starting';
  $('qr-frame')?.classList.toggle('is-waiting', status === 'starting');

  const termSw = $('sw-term-tunnel');
  if (termSw) {
    const row = termSw.closest('.switch-row');
    termSw.checked = !!termTunnel.on;
    termSw.disabled = !termTunnel.can_toggle;
    row?.classList.toggle('is-disabled', termSw.disabled);
    row?.classList.toggle('is-on', !!termTunnel.on);
    $('qr-term-sub').textContent = termTunnel.on
      ? 'On · anyone with this link gets a shell on this computer'
      : (termTunnel.can_toggle
        ? 'Off · the shell stays on this network'
        : 'Off · turn it on from your computer');
  }

  const link = currentLink();
  $('qr-url').textContent = link;
  const note = $('qr-note');
  note.className = 'qr-note';
  if (status === 'live') {
    note.textContent = termTunnel.on
      ? 'Anyone with this link can use Jarvis and open a shell on your computer. Turn it off when you are done.'
      : 'Anyone with this link can use Jarvis on your computer. Turn it off when you are done.';
    note.classList.add('is-warn');
  } else if (termTunnel.on) {
    note.textContent = 'The terminal opens on the public link as soon as Anywhere is on.';
    note.classList.add('is-warn');
  } else if (status !== 'starting' && !store.remoteUrl && isLocalHost()) {
    note.textContent = 'This address only works on this computer. Turn on Anywhere, or start the remote with /web for a network link.';
  } else {
    note.textContent = '';
  }
  $('qr-sub').textContent = status === 'live'
    ? 'Works on mobile data and other Wi-Fi too'
    : 'Scan with your camera to continue this session';
  if (status !== 'starting') drawQr(link);
}

function apply(data) {
  if (!data || typeof data !== 'object' || !data.status) return;
  tunnel = { ...tunnel, ...data };
  if (isOpen()) render();
  if (tunnel.status === 'starting') schedulePoll();
  else stopPoll();
}

function applyTerminal(data) {
  if (!data || typeof data !== 'object' || typeof data.on !== 'boolean') return;
  termTunnel = { ...termTunnel, on: data.on };
  if (typeof data.can_toggle === 'boolean') termTunnel.can_toggle = data.can_toggle;
  if (isOpen()) render();
}

function stopPoll() {
  clearTimeout(pollTimer);
  pollTimer = null;
}

function schedulePoll() {
  if (pollTimer || !isOpen()) return;
  pollTimer = setTimeout(async () => {
    pollTimer = null;
    if (!isOpen()) return;
    try { apply(await fetchTunnel()); } catch { schedulePoll(); }
  }, 1200);
}

/** `tunnel` event: the link went live / failed / closed (here, another page, the terminal). */
export function handleTunnelEvent(data) {
  const before = tunnel.status;
  apply(data);
  if (before === 'starting' && tunnel.status === 'live' && isOpen()) showToast('Anywhere link is live');
}

/** `terminal_tunnel` event: this switch moved (here, another page, the terminal). */
export function handleTerminalTunnelEvent(data) {
  applyTerminal(data);
  // terminal.js re-reads /api/terminal/list: its button appears / disappears.
  document.dispatchEvent(new CustomEvent('jarvis:terminal-access'));
}

export async function openQr() {
  drawnFor = '';
  render();
  openModal('qr', { focus: 'qr-copy' });
  try { apply(await fetchTunnel()); } catch { /* keep the local link */ }
  try { applyTerminal(await fetchTerminalTunnel()); } catch { /* keep it off */ }
}

async function toggleAnywhere() {
  const sw = $('sw-anywhere');
  const on = sw.checked;
  const before = tunnel;
  // Optimistic: the switch answers at once; the server reconciles.
  tunnel = { ...tunnel, status: on ? 'starting' : 'off', link: on ? tunnel.link : '', error: '' };
  render();
  try {
    const res = await setTunnel(on ? 'start' : 'stop');
    if (res && res.ok === false && res.error) showToast(res.error, true);
    apply(res);
    if (!on) showToast('Anywhere link closed');
  } catch (err) {
    tunnel = before;
    render();
    showToast(err?.message || 'Could not change the Anywhere link', true);
  }
}

async function toggleTermTunnel() {
  const sw = $('sw-term-tunnel');
  const on = sw.checked;
  const before = termTunnel;
  // Optimistic: the switch answers at once; the server reconciles.
  termTunnel = { ...termTunnel, on };
  render();
  try {
    const res = await setTerminalTunnel(on);
    if (res && res.ok === false && res.error) showToast(res.error, true);
    applyTerminal(res);
    if (res && res.ok !== false && on) showToast('The shell is open on the public link');
  } catch (err) {
    termTunnel = before;
    render();
    showToast(err?.message || 'Could not change the terminal switch', true);
  }
}

export function initQr() {
  $('qr-btn')?.addEventListener('click', openQr);
  $('sw-anywhere')?.addEventListener('change', toggleAnywhere);
  $('sw-term-tunnel')?.addEventListener('change', toggleTermTunnel);
  $('qr-copy')?.addEventListener('click', async () => {
    if (await copyText(currentLink())) showToast('Link copied');
    else showToast('Copy failed — copy the address bar instead', true);
  });
}
