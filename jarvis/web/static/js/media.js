/** Attachments: the composer tray (pick, drop or paste → upload with progress),
 * files in sent messages and tool screenshots, and the full-screen viewer.
 *
 * Each file uploads on its own as soon as it's added (POST /api/upload, raw
 * body — jarvis/media.py), so sending is instant: a message only carries the
 * ids. The tray survives a reload (finished uploads are kept in
 * localStorage). The server has the final say on size and type; its reason
 * is shown on the tile, with Retry where retrying can help.
 */
import { $, escapeHtml, showToast, storageGet, storageSet, haptic, animateEl, SPRING, readToken, BASE } from './utils.js';
import { icon } from './icons.js';
import { store, subscribe } from './store.js';
import { openModal, closeModal, topModal } from './modal.js';

const TRAY_KEY = 'jarvis-attachments';
const MAX_PARALLEL = 3;
/** No upload progress for this long = the connection died under us. */
const STALL_MS = 45_000;
const DEFAULT_LIMITS = { max_mb: 50, max_files: 10 };

const EXT_KIND = {
  image: 'png jpg jpeg gif webp heic heif avif bmp tif tiff svg',
  video: 'mp4 m4v mov webm mkv avi',
  audio: 'mp3 m4a aac wav ogg oga opus flac weba',
  pdf: 'pdf',
  document: 'doc docx odt rtf pages epub',
  sheet: 'xls xlsx ods numbers csv tsv',
  slides: 'ppt pptx odp key',
  archive: 'zip tar gz tgz 7z',
};
const KIND_OF_EXT = Object.fromEntries(
  Object.entries(EXT_KIND).flatMap(([kind, exts]) => exts.split(' ').map((e) => [e, kind])),
);
/** Never worth uploading: the server would refuse them anyway. */
const REFUSED_EXT = new Set('exe dmg pkg app msi iso bin dll so dylib apk ipa deb rpm'.split(' '));

const KIND = {
  image: { icon: 'image', label: 'Image' },
  video: { icon: 'film', label: 'Video' },
  audio: { icon: 'music', label: 'Audio' },
  pdf: { icon: 'file-text', label: 'PDF' },
  document: { icon: 'file-text', label: 'Document' },
  sheet: { icon: 'file-spreadsheet', label: 'Spreadsheet' },
  slides: { icon: 'presentation', label: 'Slides' },
  archive: { icon: 'file-archive', label: 'Archive' },
  text: { icon: 'file-code', label: 'Text' },
  file: { icon: 'file', label: 'File' },
};
/** Images the browser can preview from the local file before it uploads. */
const LOCAL_PREVIEW = /^image\/(png|jpe?g|gif|webp|avif|bmp|svg\+xml)$/;

/** @type {Array<{key:string,file?:File,id?:string,name:string,size:number,kind:string,mime:string,
 *   width?:number,height?:number,status:'queued'|'uploading'|'done'|'error',progress:number,
 *   error?:string,retry?:boolean,preview?:string,xhr?:XMLHttpRequest}>} */
let items = [];
let seq = 0;
let locked = false;
let lastStatusKey = '';
let lastNoteKey = '';
let openPicker = null;
const listeners = new Set();

// ─── Small helpers ────────────────────────────────────────────────────────

export function limits() {
  return { ...DEFAULT_LIMITS, ...(store.uploadLimits || {}) };
}

/** `/api/media/<id>` with this page's token (img / video tags can't send headers). */
export function mediaUrl(id, { variant = '', download = false } = {}) {
  const qs = new URLSearchParams({ token: readToken() });
  if (variant) qs.set('v', variant);
  if (download) qs.set('dl', '1');
  return `${BASE}/api/media/${encodeURIComponent(id)}?${qs}`;
}

export function fmtSize(n) {
  const v = Number(n) || 0;
  if (v < 1024) return `${v} B`;
  const units = ['KB', 'MB', 'GB'];
  let x = v;
  let u = -1;
  do { x /= 1024; u += 1; } while (x >= 1024 && u < units.length - 1);
  return `${x < 10 ? x.toFixed(1).replace(/\.0$/, '') : Math.round(x)} ${units[u]}`;
}

function extOf(name) {
  const m = /\.([a-z0-9]{1,8})$/i.exec(String(name || ''));
  return m ? m[1].toLowerCase() : '';
}

function kindOf(name, type) {
  const byExt = KIND_OF_EXT[extOf(name)];
  if (byExt) return byExt;
  const t = String(type || '');
  if (t.startsWith('image/')) return 'image';
  if (t.startsWith('video/')) return 'video';
  if (t.startsWith('audio/')) return 'audio';
  if (t === 'application/pdf') return 'pdf';
  return 'text';
}

/** "PDF · 1.2 MB", "JPG · 4032×3024 · 3.1 MB" */
function fileMeta(f) {
  const ext = extOf(f.name).toUpperCase();
  const parts = [ext || (KIND[f.kind] || KIND.file).label];
  if (f.width && f.height && f.kind === 'image') parts.push(`${f.width}×${f.height}`);
  if (f.size) parts.push(fmtSize(f.size));
  return parts.join(' · ');
}

const isVisual = (f) => (f.kind === 'image' || f.kind === 'video') && !f.missing;

// ─── Tray state ───────────────────────────────────────────────────────────

/** Counts the composer needs: can it send, and what to say if not. */
export function trayStatus() {
  const uploading = items.filter((i) => i.status === 'uploading' || i.status === 'queued').length;
  const failed = items.filter((i) => i.status === 'error').length;
  const ready = items.filter((i) => i.status === 'done');
  return { count: items.length, uploading, failed, ready: ready.map(publicRecord), locked };
}

export function onTrayChange(fn) {
  listeners.add(fn);
  return () => listeners.delete(fn);
}

function changed() {
  save();
  render();
  const st = trayStatus();
  const key = `${st.count}|${st.uploading}|${st.failed}|${st.ready.length}|${locked}`;
  if (key === lastStatusKey) return;
  lastStatusKey = key;
  for (const fn of listeners) fn(st);
}

function publicRecord(it) {
  const { id, name, size, kind, mime, width, height } = it;
  return { id, name, size, kind, mime, ...(width && height ? { width, height } : {}) };
}

function save() {
  const done = items.filter((i) => i.status === 'done' && i.id).map(publicRecord);
  storageSet(TRAY_KEY, JSON.stringify(done));
}

function restore() {
  let saved = [];
  try {
    saved = JSON.parse(storageGet(TRAY_KEY, '[]'));
  } catch {
    saved = [];
  }
  if (!Array.isArray(saved)) return;
  for (const f of saved.slice(0, limits().max_files)) {
    if (!f?.id || typeof f.id !== 'string') continue;
    items.push({
      ...f,
      key: `a${++seq}`,
      status: 'done',
      progress: 1,
      preview: f.kind === 'image' ? mediaUrl(f.id, { variant: 'thumb' }) : '',
    });
  }
}

/** Lock the tray while a message with these files is on its way. */
export function lockTray(on) {
  locked = !!on;
  changed();
}

/** The message went out: its files now belong to it. */
export function clearTray() {
  for (const it of items) if (it.preview?.startsWith('blob:')) URL.revokeObjectURL(it.preview);
  items = [];
  locked = false;
  changed();
}

// ─── Adding files ─────────────────────────────────────────────────────────

/** Validate and queue files from the picker, a drop or a paste. */
export function addFiles(list, { from = 'pick' } = {}) {
  const files = [...(list || [])].filter((f) => f && typeof f.size === 'number');
  if (!files.length) return 0;
  if (!store.connected) {
    showToast('Not connected to Jarvis, so files can’t upload right now', true);
    return 0;
  }
  if (locked) {
    showToast('Sending… add more files to the next message', true);
    return 0;
  }
  const { max_mb, max_files } = limits();
  const room = max_files - items.length;
  if (room <= 0) {
    showToast(`Up to ${max_files} files per message`, true);
    return 0;
  }
  const problems = [];
  let added = 0;
  for (const file of files) {
    const name = file.name || (from === 'paste' ? `pasted-image.${(file.type.split('/')[1] || 'png').replace('jpeg', 'jpg')}` : 'file');
    if (added >= room) {
      problems.push(`Only ${max_files} files fit in one message`);
      break;
    }
    if (!file.size) { problems.push(`“${name}” is empty`); continue; }
    if (file.size > max_mb * 1024 * 1024) { problems.push(`“${name}” is ${fmtSize(file.size)}. Files can be up to ${max_mb} MB`); continue; }
    if (REFUSED_EXT.has(extOf(name))) { problems.push(`“${name}” can’t be attached (programs and disk images aren’t supported)`); continue; }
    const dupe = items.some((it) => it.file && it.file.name === file.name && it.file.size === file.size
      && it.file.lastModified === file.lastModified);
    if (dupe) continue;
    const it = {
      key: `a${++seq}`,
      file,
      name,
      size: file.size,
      kind: kindOf(name, file.type),
      mime: file.type || '',
      status: 'queued',
      progress: 0,
    };
    if (it.kind === 'image' && LOCAL_PREVIEW.test(file.type)) it.preview = URL.createObjectURL(file);
    items.push(it);
    added += 1;
  }
  if (problems.length) {
    showToast(problems.length === 1 ? problems[0] : `${problems.length} files weren’t added. ${problems[0]}`, true);
  }
  if (added) {
    haptic(6);
    changed();
    pump();
    requestAnimationFrame(() => $('att-tray')?.lastElementChild?.scrollIntoView({ block: 'nearest', inline: 'end', behavior: 'smooth' }));
  }
  return added;
}

function pump() {
  let active = items.filter((i) => i.status === 'uploading').length;
  for (const it of items) {
    if (active >= MAX_PARALLEL) break;
    if (it.status === 'queued' && it.file) {
      upload(it);
      active += 1;
    }
  }
}

function httpError(status) {
  if (status === 0) return 'Upload failed. Check the connection and retry.';
  if (status === 401) return 'This page’s link has expired. Open the new link from the terminal.';
  if (status === 413) return 'Too large to upload.';
  if (status === 507) return 'The computer running Jarvis is out of disk space.';
  if (status >= 500) return `Jarvis couldn’t save the file (error ${status}).`;
  return `Upload failed (error ${status}).`;
}

function fail(it, message, { retry = true } = {}) {
  it.status = 'error';
  it.error = message;
  it.retry = retry && !!it.file;
  it.xhr = null;
  clearTimeout(it.stallTimer);
  showToast(message, true);
  changed();
  pump();
}

function upload(it) {
  it.status = 'uploading';
  it.progress = 0;
  it.error = '';
  const xhr = new XMLHttpRequest();
  it.xhr = xhr;
  const arm = () => {
    clearTimeout(it.stallTimer);
    it.stallTimer = setTimeout(() => {
      if (it.xhr !== xhr) return;
      xhr.abort();
      fail(it, `“${it.name}” stopped uploading. Check the connection and retry.`);
    }, STALL_MS);
  };
  xhr.open('POST', `${BASE}/api/upload`);
  xhr.setRequestHeader('Authorization', `Bearer ${readToken()}`);
  xhr.setRequestHeader('X-File-Name', encodeURIComponent(it.name));
  xhr.setRequestHeader('Content-Type', it.file.type || 'application/octet-stream');
  xhr.upload.onprogress = (e) => {
    if (it.xhr !== xhr) return;
    arm();
    if (e.lengthComputable && e.total) {
      it.progress = Math.min(0.99, e.loaded / e.total);
      paintProgress(it);
    }
  };
  xhr.onload = () => {
    if (it.xhr !== xhr) return;
    clearTimeout(it.stallTimer);
    let body = null;
    try {
      body = JSON.parse(xhr.responseText);
    } catch { /* not JSON: a proxy error page */ }
    if (xhr.status === 200 && body?.ok && body.file?.id) {
      const f = body.file;
      Object.assign(it, {
        status: 'done', progress: 1, xhr: null, id: f.id, name: f.name, kind: f.kind,
        mime: f.mime, size: f.size, width: f.width, height: f.height, justDone: true,
      });
      if (it.kind === 'image' && !it.preview) it.preview = mediaUrl(f.id, { variant: 'thumb' });
      changed();
      pump();
      return;
    }
    // Wrong type / damaged file: retrying the same bytes won't help.
    const permanent = [400, 410, 411, 413, 415].includes(xhr.status);
    fail(it, body?.error || httpError(xhr.status), { retry: !permanent || body?.code === 'interrupted' });
  };
  xhr.onerror = () => {
    if (it.xhr === xhr) fail(it, `“${it.name}” didn’t upload. Check the connection and retry.`);
  };
  arm();
  changed();
  try {
    xhr.send(it.file);
  } catch {
    fail(it, `“${it.name}” couldn’t be read from this device.`, { retry: false });
  }
}

export function removeAttachment(key) {
  const i = items.findIndex((it) => it.key === key);
  if (i === -1 || locked) return;
  const [it] = items.splice(i, 1);
  clearTimeout(it.stallTimer);
  if (it.xhr) {
    const x = it.xhr;
    it.xhr = null;
    x.abort();
  }
  if (it.preview?.startsWith('blob:')) URL.revokeObjectURL(it.preview);
  if (it.id) {
    // Never sent: free the copy on the computer (fire and forget).
    fetch(`${BASE}/api/upload/remove`, {
      method: 'POST',
      headers: { Authorization: `Bearer ${readToken()}`, 'Content-Type': 'application/json' },
      body: JSON.stringify({ id: it.id }),
    }).catch(() => {});
  }
  changed();
  pump();
}

function retryAttachment(key) {
  const it = items.find((x) => x.key === key);
  if (!it || !it.file || locked) return;
  it.status = 'queued';
  it.error = '';
  changed();
  pump();
}

// ─── Tray rendering ───────────────────────────────────────────────────────

const RING_R = 15;
const RING_C = 2 * Math.PI * RING_R;

function ringSvg() {
  return `<svg class="att-ring" viewBox="0 0 36 36" aria-hidden="true"><circle class="att-ring-bg" cx="18" cy="18" r="${RING_R}"/><circle class="att-ring-fg" cx="18" cy="18" r="${RING_R}" stroke-dasharray="${RING_C.toFixed(2)}" stroke-dashoffset="${RING_C.toFixed(2)}"/></svg>`;
}

function paintProgress(it) {
  const tile = $('att-tray')?.querySelector(`[data-key="${it.key}"]`);
  if (!tile) return;
  const pct = Math.round((it.progress || 0) * 100);
  tile.querySelector('.att-ring-fg')?.setAttribute('stroke-dashoffset', (RING_C * (1 - (it.progress || 0))).toFixed(2));
  tile.style.setProperty('--p', String(it.progress || 0));
  const label = tile.querySelector('.att-pct');
  if (label) label.textContent = `${pct}%`;
  tile.setAttribute('aria-label', `${it.name}, uploading ${pct}%`);
}

function tileHtml(it) {
  const k = KIND[it.kind] || KIND.file;
  const state = it.status === 'queued' ? 'uploading' : it.status;
  const label = state === 'error' ? `${it.name}: ${it.error}` : state === 'uploading' ? `${it.name}, uploading` : `${it.name}, ${fileMeta(it)}`;
  const visual = it.kind === 'image' && it.preview;
  const media = visual
    ? `<img class="att-img" src="${escapeHtml(it.preview)}" alt="" decoding="async" draggable="false">`
    : `<span class="att-ic">${icon(k.icon)}</span>`;
  const body = visual ? '' : `
      <span class="att-text">
        <span class="att-name">${escapeHtml(it.name)}</span>
        <span class="att-meta">${state === 'error' ? escapeHtml(it.retry ? 'Upload failed' : 'Can’t attach') : state === 'uploading' ? '<span class="att-pct">0%</span> uploading' : escapeHtml(fileMeta(it))}</span>
      </span>`;
  const overlay = state === 'uploading'
    ? `<span class="att-busy">${ringSvg()}</span>`
    : state === 'error'
      ? `<span class="att-err" title="${escapeHtml(it.error || '')}">${icon('triangle-alert')}</span>`
      : '';
  const retry = state === 'error' && it.retry
    ? `<button type="button" class="att-retry" data-act="retry" aria-label="Retry ${escapeHtml(it.name)}" title="Retry">${icon('refresh-cw')}</button>`
    : '';
  return `
    <div class="att${visual ? ' is-visual' : ' is-file'}${it.justDone ? ' just-done' : ''}" role="listitem" data-key="${it.key}" data-kind="${escapeHtml(it.kind)}" data-state="${state}" title="${escapeHtml(label)}" aria-label="${escapeHtml(label)}">
      ${media}${body}${overlay}${retry}
      <button type="button" class="att-x" data-act="remove" aria-label="Remove ${escapeHtml(it.name)}" title="Remove"${locked ? ' disabled' : ''}>${icon('x')}</button>
    </div>`;
}

/** Pictures in the tray but the model can't see them: say so, offer a model that can. */
function paintVisionNote() {
  const note = $('att-note');
  if (!note) return;
  const pictures = items.filter((i) => i.kind === 'image').length;
  const show = pictures > 0 && store.session.vision === false;
  const key = show ? `${store.session.model}|${pictures > 1}` : '';
  if (key === lastNoteKey) return;
  lastNoteKey = key;
  note.hidden = !show;
  if (!show) {
    note.innerHTML = '';
    return;
  }
  const model = String(store.session.model || 'This model').split('/').pop();
  note.innerHTML = `${icon('image-off')}<span><strong>${escapeHtml(model)}</strong> can’t see images, so Jarvis gets only ${pictures > 1 ? 'their' : 'its'} file path.</span>
    <button type="button" class="att-note-btn" data-act="vision-models">${icon('image')}<span>Pick a model that can</span></button>`;
  note.querySelector('[data-act="vision-models"]')?.addEventListener('click', () => openPicker?.('model', 'vision'));
  animateEl(note, [{ opacity: 0, transform: 'translateY(4px)' }, { opacity: 1, transform: 'none' }], { duration: 220 });
}

function render() {
  paintVisionNote();
  const tray = $('att-tray');
  if (!tray) return;
  tray.hidden = !items.length;
  $('composer')?.classList.toggle('has-files', items.length > 0);
  // Keep tiles that didn't change (their <img> must not reload or flicker).
  const want = new Map(items.map((it) => [it.key, it]));
  for (const el of [...tray.children]) {
    if (!want.has(el.dataset.key)) el.remove();
  }
  let prev = null;
  for (const it of items) {
    let el = tray.querySelector(`:scope > [data-key="${it.key}"]`);
    const sig = `${it.status}|${it.preview || ''}|${it.name}|${it.error || ''}|${it.retry}|${locked}`;
    if (!el || el.dataset.sig !== sig) {
      const tmp = document.createElement('div');
      tmp.innerHTML = tileHtml(it).trim();
      const fresh = tmp.firstElementChild;
      fresh.dataset.sig = sig;
      if (el) {
        // Same image: keep the decoded <img> so the swap doesn't flash.
        const oldImg = el.querySelector('.att-img');
        const newImg = fresh.querySelector('.att-img');
        if (oldImg && newImg && oldImg.getAttribute('src') === newImg.getAttribute('src')) newImg.replaceWith(oldImg);
        el.replaceWith(fresh);
      } else {
        fresh.classList.add('is-new');
        setTimeout(() => fresh.classList.remove('is-new'), 500);
        if (prev) prev.after(fresh);
        else tray.prepend(fresh);
      }
      el = fresh;
      if (it.status === 'uploading' && it.progress > 0) paintProgress(it);
      if (it.justDone) {
        it.justDone = false;
        setTimeout(() => el.classList.remove('just-done'), 900);
      }
      const img = el.querySelector('.att-img');
      img?.addEventListener('error', () => {
        // A restored upload that's gone from the computer, or a format this
        // browser can't preview: fall back to the file icon.
        it.preview = '';
        if (it.status === 'done' && !it.file) {
          it.status = 'error';
          it.error = 'This file is no longer on the computer. Remove it and attach it again.';
          it.retry = false;
        }
        changed();
      }, { once: true });
    }
    prev = el;
  }
}

function onTrayClick(e) {
  const btn = e.target.closest('[data-act]');
  const tile = e.target.closest('.att');
  if (!tile) return;
  const key = tile.dataset.key;
  if (btn?.dataset.act === 'remove') {
    e.stopPropagation();
    animateEl(tile, [{ opacity: 1, transform: 'scale(1)' }, { opacity: 0, transform: 'scale(0.86)' }], { duration: 160 })
      ?.finished.then(() => removeAttachment(key), () => removeAttachment(key)) ?? removeAttachment(key);
    return;
  }
  if (btn?.dataset.act === 'retry') {
    e.stopPropagation();
    retryAttachment(key);
    return;
  }
  const it = items.find((x) => x.key === key);
  if (!it) return;
  if (it.status === 'error') {
    showToast(it.error || 'This file couldn’t be uploaded', true);
    return;
  }
  if (it.status === 'done' && it.id) {
    const visual = items.filter((x) => x.status === 'done' && x.id && isVisual(x)).map(publicRecord);
    if (isVisual(it)) openViewer(visual, visual.findIndex((x) => x.id === it.id));
    else window.open(mediaUrl(it.id), '_blank', 'noopener');
  }
}

// ─── Drop zone + paste ────────────────────────────────────────────────────

const hasFiles = (e) => [...(e.dataTransfer?.types || [])].includes('Files');
let dragDepth = 0;

function showDrop(on) {
  const zone = $('drop-zone');
  if (!zone) return;
  if (on && zone.hidden) {
    zone.hidden = false;
    const { max_mb } = limits();
    $('drop-sub').textContent = `Images, video, audio, PDFs and documents · up to ${max_mb} MB each`;
  } else if (!on) {
    zone.hidden = true;
  }
}

function initDrop() {
  document.addEventListener('dragenter', (e) => {
    if (!hasFiles(e) || document.body.classList.contains('has-modal')) return;
    e.preventDefault();
    dragDepth += 1;
    showDrop(true);
  });
  document.addEventListener('dragover', (e) => {
    if (!hasFiles(e)) return;
    // Without this the browser opens the file and the session is gone.
    e.preventDefault();
    e.dataTransfer.dropEffect = document.body.classList.contains('has-modal') ? 'none' : 'copy';
  });
  document.addEventListener('dragleave', (e) => {
    if (!hasFiles(e)) return;
    dragDepth = Math.max(0, dragDepth - 1);
    if (!dragDepth) showDrop(false);
  });
  document.addEventListener('drop', (e) => {
    if (!hasFiles(e)) return;
    e.preventDefault();
    dragDepth = 0;
    showDrop(false);
    if (document.body.classList.contains('has-modal')) return;
    if (addFiles(e.dataTransfer.files, { from: 'drop' })) $('prompt')?.focus({ preventScroll: true });
  });
  window.addEventListener('blur', () => { dragDepth = 0; showDrop(false); });
}

function onPaste(e) {
  const files = [...(e.clipboardData?.files || [])];
  if (!files.length) return;
  // Text with an image alongside (copied from a web page / an office app):
  // the text is what was meant.
  if ((e.clipboardData.getData('text/plain') || '').trim()) return;
  e.preventDefault();
  addFiles(files, { from: 'paste' });
}

// ─── Files in the transcript ──────────────────────────────────────────────

function visualTile(f, list, idx) {
  const tile = document.createElement('button');
  tile.type = 'button';
  tile.className = `mtile is-${f.kind}`;
  tile.setAttribute('aria-label', `Open ${f.name}`);
  tile.title = `${f.name} · ${fileMeta(f)}`;
  if (f.width && f.height) tile.style.setProperty('--ar', `${f.width} / ${f.height}`);
  if (f.kind === 'video') {
    tile.innerHTML = `<video class="mtile-media" preload="metadata" muted playsinline src="${escapeHtml(mediaUrl(f.id))}#t=0.1"></video>
      <span class="mtile-play">${icon('play')}</span>`;
    const v = tile.querySelector('video');
    v.addEventListener('loadedmetadata', () => {
      if (v.videoWidth && v.videoHeight && !(f.width && f.height)) tile.style.setProperty('--ar', `${v.videoWidth} / ${v.videoHeight}`);
    }, { once: true });
    v.addEventListener('error', () => brokenTile(tile, f), { once: true });
  } else {
    tile.innerHTML = `<img class="mtile-media" src="${escapeHtml(mediaUrl(f.id, { variant: 'thumb' }))}" alt="${escapeHtml(f.name)}" loading="lazy" decoding="async" draggable="false">`;
    const img = tile.querySelector('img');
    img.addEventListener('load', () => {
      tile.classList.add('is-loaded');
      if (!(f.width && f.height) && img.naturalWidth) tile.style.setProperty('--ar', `${img.naturalWidth} / ${img.naturalHeight}`);
    }, { once: true });
    img.addEventListener('error', () => brokenTile(tile, f), { once: true });
  }
  tile.addEventListener('click', (e) => {
    e.stopPropagation();
    if (tile.classList.contains('is-broken')) {
      window.open(mediaUrl(f.id, { download: true }), '_blank', 'noopener');
      return;
    }
    openViewer(list, idx);
  });
  return tile;
}

function brokenTile(tile, f) {
  tile.classList.add('is-broken');
  tile.innerHTML = `<span class="mtile-broken">${icon('image-off')}<span>${escapeHtml(f.name)}</span><small>Preview unavailable · tap to download</small></span>`;
}

function missingCard(f) {
  const el = document.createElement('div');
  el.className = 'fcard is-missing';
  el.dataset.kind = f.kind || 'file';
  el.innerHTML = `<span class="fcard-ic">${icon(f.kind === 'image' ? 'image-off' : 'file-x')}</span>
    <span class="fcard-text"><span class="fcard-name">${escapeHtml(f.name || 'File')}</span><span class="fcard-meta">No longer on the computer</span></span>`;
  el.title = 'This attachment was removed from the computer (old uploads are cleaned up after a while)';
  return el;
}

function fileCard(f) {
  if (f.missing) return missingCard(f);
  const k = KIND[f.kind] || KIND.file;
  const viewable = ['pdf', 'text'].includes(f.kind);
  const el = document.createElement('div');
  el.className = `fcard is-${f.kind}`;
  el.dataset.kind = f.kind || 'file';
  const href = escapeHtml(mediaUrl(f.id, { download: !viewable }));
  el.innerHTML = `
    <a class="fcard-main" href="${href}" ${viewable ? 'target="_blank" rel="noopener"' : `download="${escapeHtml(f.name)}"`} title="${viewable ? 'Open' : 'Download'} ${escapeHtml(f.name)}">
      <span class="fcard-ic">${icon(k.icon)}</span>
      <span class="fcard-text"><span class="fcard-name">${escapeHtml(f.name)}</span><span class="fcard-meta">${escapeHtml(fileMeta(f))}</span></span>
      <span class="fcard-go">${icon(viewable ? 'external-link' : 'download')}</span>
    </a>`;
  if (f.kind === 'audio') {
    const audio = document.createElement('audio');
    audio.className = 'fcard-audio';
    audio.controls = true;
    audio.preload = 'none';
    audio.src = mediaUrl(f.id);
    audio.addEventListener('error', () => {
      audio.remove();
      el.querySelector('.fcard-meta').textContent = `${fileMeta(f)} · can’t play here, download it`;
    }, { once: true });
    el.appendChild(audio);
    el.classList.add('has-player');
  }
  el.addEventListener('click', (e) => e.stopPropagation());
  return el;
}

/** Files of one sent message: a photo grid, then document cards. */
export function renderFiles(files) {
  const wrap = document.createElement('div');
  wrap.className = 'msg-files';
  const visual = files.filter(isVisual);
  const others = files.filter((f) => !isVisual(f));
  if (visual.length) {
    const grid = document.createElement('div');
    const n = Math.min(visual.length, 4);
    grid.className = `mgrid n${n}${visual.length > 4 ? ' has-more' : ''}`;
    const one = visual[0];
    if (n === 1 && one.width && one.height) {
      // A single picture keeps its shape: portrait ones get narrower, not cropped.
      const w = Math.round(Math.max(150, Math.min(360, (380 * one.width) / one.height)));
      grid.style.width = `min(100%, ${w}px)`;
    }
    visual.slice(0, 4).forEach((f, i) => {
      const tile = visualTile(f, visual, i);
      if (i === 3 && visual.length > 4) {
        const more = document.createElement('span');
        more.className = 'mtile-more';
        more.textContent = `+${visual.length - 4}`;
        tile.appendChild(more);
        tile.setAttribute('aria-label', `Open ${visual.length - 3} more`);
      }
      grid.appendChild(tile);
    });
    wrap.appendChild(grid);
  }
  if (others.length) {
    const list = document.createElement('div');
    list.className = 'fcards';
    others.forEach((f) => list.appendChild(fileCard(f)));
    wrap.appendChild(list);
  }
  return wrap;
}

/** Screenshots a tool attached for the model, shown under its row. */
export function renderToolImages(images) {
  const strip = document.createElement('div');
  strip.className = 'tool-media';
  images.forEach((f, i) => strip.appendChild(visualTile(f, images, i)));
  return strip;
}

// ─── Viewer ───────────────────────────────────────────────────────────────

const viewer = { list: [], index: 0, zoomed: false, touch: null };

export function openViewer(list, index = 0) {
  const usable = (list || []).filter(isVisual);
  if (!usable.length) return;
  viewer.list = usable;
  viewer.index = Math.max(0, Math.min(index, usable.length - 1));
  openModal('viewer', { onClose: stopViewerMedia });
  paintViewer();
}

function stopViewerMedia() {
  $('viewer-stage')?.querySelectorAll('video, audio').forEach((m) => {
    try { m.pause(); } catch { /* already gone */ }
  });
  const stage = $('viewer-stage');
  if (stage) setTimeout(() => { if (topModal() !== 'viewer') stage.innerHTML = ''; }, 220);
}

function setZoom(on) {
  viewer.zoomed = !!on;
  const stage = $('viewer-stage');
  stage?.classList.toggle('is-zoomed', viewer.zoomed);
  const btn = $('viewer-zoom');
  if (btn) {
    btn.innerHTML = icon(viewer.zoomed ? 'zoom-out' : 'zoom-in');
    btn.setAttribute('aria-pressed', String(viewer.zoomed));
    btn.setAttribute('aria-label', viewer.zoomed ? 'Fit to screen' : 'Actual size');
    btn.title = viewer.zoomed ? 'Fit to screen (Z)' : 'Actual size (Z)';
  }
}

function paintViewer() {
  const f = viewer.list[viewer.index];
  const stage = $('viewer-stage');
  if (!f || !stage) return;
  setZoom(false);
  stage.querySelectorAll('video').forEach((v) => v.pause());
  $('viewer-name').textContent = f.name || 'Attachment';
  $('viewer-meta').textContent = fileMeta(f);
  const many = viewer.list.length > 1;
  $('viewer-count').textContent = many ? `${viewer.index + 1} / ${viewer.list.length}` : '';
  $('viewer-prev').hidden = !many;
  $('viewer-next').hidden = !many;
  $('viewer-zoom').hidden = f.kind !== 'image';
  const open = $('viewer-open');
  open.href = mediaUrl(f.id);
  const dl = $('viewer-download');
  dl.href = mediaUrl(f.id, { download: true });
  dl.setAttribute('download', f.name || '');

  stage.innerHTML = '<span class="spinner viewer-spin" aria-hidden="true"></span>';
  stage.dataset.kind = f.kind;
  if (f.kind === 'video') {
    const v = document.createElement('video');
    v.className = 'viewer-media';
    v.controls = true;
    v.playsInline = true;
    v.autoplay = true;
    v.src = mediaUrl(f.id);
    v.addEventListener('loadeddata', () => stage.querySelector('.viewer-spin')?.remove(), { once: true });
    v.addEventListener('error', () => viewerError(stage, f, 'This video can’t play in this browser.'), { once: true });
    stage.appendChild(v);
  } else {
    const img = new Image();
    img.className = 'viewer-media';
    img.alt = f.name || '';
    img.decoding = 'async';
    img.draggable = false;
    img.addEventListener('load', () => {
      stage.querySelector('.viewer-spin')?.remove();
      img.classList.add('is-loaded');
    }, { once: true });
    img.addEventListener('error', () => viewerError(stage, f, 'This image can’t be shown in this browser.'), { once: true });
    img.src = mediaUrl(f.id, { variant: 'view' });
    stage.appendChild(img);
  }
  // Warm the neighbours so arrowing through is instant.
  for (const d of [1, -1]) {
    const n = viewer.list[(viewer.index + d + viewer.list.length) % viewer.list.length];
    if (n && n !== f && n.kind === 'image') new Image().src = mediaUrl(n.id, { variant: 'view' });
  }
}

function viewerError(stage, f, message) {
  stage.innerHTML = `<div class="viewer-error">${icon('image-off')}<strong>${escapeHtml(message)}</strong>
    <a class="btn" href="${escapeHtml(mediaUrl(f.id, { download: true }))}" download="${escapeHtml(f.name || '')}">${icon('download')}<span>Download ${escapeHtml(f.name || 'file')}</span></a></div>`;
}

function step(delta) {
  if (viewer.list.length < 2) return;
  viewer.index = (viewer.index + delta + viewer.list.length) % viewer.list.length;
  paintViewer();
  animateEl($('viewer-stage')?.querySelector('.viewer-media'), [
    { opacity: 0, transform: `translateX(${delta > 0 ? 24 : -24}px)` },
    { opacity: 1, transform: 'none' },
  ], { duration: 220, easing: SPRING });
}

function initViewer() {
  $('viewer-prev')?.addEventListener('click', () => step(-1));
  $('viewer-next')?.addEventListener('click', () => step(1));
  $('viewer-zoom')?.addEventListener('click', () => setZoom(!viewer.zoomed));
  const stage = $('viewer-stage');
  stage?.addEventListener('click', (e) => {
    if (e.target === stage && !viewer.zoomed) closeModal('viewer', 'backdrop');
  });
  stage?.addEventListener('dblclick', (e) => {
    if (e.target.matches?.('img.viewer-media')) setZoom(!viewer.zoomed);
  });
  // Swipe left / right between files (not while zoomed: that's panning).
  stage?.addEventListener('touchstart', (e) => {
    if (viewer.zoomed || e.touches.length !== 1) { viewer.touch = null; return; }
    viewer.touch = { x: e.touches[0].clientX, y: e.touches[0].clientY, t: Date.now() };
  }, { passive: true });
  stage?.addEventListener('touchend', (e) => {
    const t = viewer.touch;
    viewer.touch = null;
    if (!t || viewer.zoomed) return;
    const dx = e.changedTouches[0].clientX - t.x;
    const dy = e.changedTouches[0].clientY - t.y;
    if (Math.abs(dx) > 50 && Math.abs(dx) > Math.abs(dy) * 1.4 && Date.now() - t.t < 600) step(dx < 0 ? 1 : -1);
    else if (dy > 90 && Math.abs(dy) > Math.abs(dx) * 1.4) closeModal('viewer', 'swipe');
  }, { passive: true });
  document.addEventListener('keydown', (e) => {
    if (topModal() !== 'viewer' || e.metaKey || e.ctrlKey || e.altKey) return;
    if (e.target.closest?.('video, audio')) return;
    if (e.key === 'ArrowRight') { e.preventDefault(); step(1); }
    else if (e.key === 'ArrowLeft') { e.preventDefault(); step(-1); }
    else if ((e.key === 'z' || e.key === 'Z') && viewer.list[viewer.index]?.kind === 'image') { e.preventDefault(); setZoom(!viewer.zoomed); }
  });
}

// ─── Init ─────────────────────────────────────────────────────────────────

export function openFilePicker() {
  if (!store.connected) {
    showToast('Not connected to Jarvis, so files can’t upload right now', true);
    return;
  }
  $('att-input')?.click();
}

export function initMedia({ onOpenPicker } = {}) {
  openPicker = onOpenPicker || null;
  restore();
  subscribe(paintVisionNote);  // the model can change from anywhere (terminal, picker)
  const input = $('att-input');
  input?.addEventListener('change', () => {
    addFiles(input.files, { from: 'pick' });
    input.value = '';  // picking the same file again still fires `change`
  });
  const attach = $('qc-attach');
  attach?.addEventListener('mousedown', (e) => e.preventDefault()); // keep the caret
  attach?.addEventListener('click', openFilePicker);
  $('att-tray')?.addEventListener('click', onTrayClick);
  $('prompt')?.addEventListener('paste', onPaste);
  initDrop();
  initViewer();
  changed();
  animateEl($('att-tray'), [{ opacity: 0 }, { opacity: 1 }], { duration: 200 });
}
