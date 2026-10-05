/** "QR code" dialog — scan it to open this session on a phone */
import { $, showToast, copyText, readToken, BASE } from './utils.js';
import { store } from './store.js';
import { openModal } from './modal.js';
import { shareLink } from './projects.js';

const isLocalHost = () => ['localhost', '127.0.0.1', '[::1]'].includes(location.hostname);

export async function openQr() {
  const link = shareLink();
  const frame = $('qr-frame');
  $('qr-url').textContent = link;
  $('qr-note').textContent = store.remoteUrl || !isLocalHost()
    ? ''
    : 'This address only works on this computer. Start the remote with /web so it has a network link.';
  frame.textContent = 'Loading…';
  openModal('qr', { focus: 'qr-copy' });
  try {
    const token = readToken();
    const res = await fetch(`${BASE}/api/qr?url=${encodeURIComponent(link)}`, {
      headers: token ? { Authorization: `Bearer ${token}` } : {},
    });
    if (!res.ok) throw new Error(String(res.status));
    frame.innerHTML = await res.text();
    frame.firstElementChild?.setAttribute('role', 'img');
    frame.firstElementChild?.setAttribute('aria-label', 'QR code for this session link');
  } catch {
    frame.textContent = 'Could not create the QR code.';
  }
}

export function initQr() {
  $('qr-btn')?.addEventListener('click', openQr);
  $('qr-copy')?.addEventListener('click', async () => {
    if (await copyText(shareLink())) showToast('Link copied');
    else showToast('Copy failed — copy the address bar instead', true);
  });
}
