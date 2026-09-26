/** Markdown → safe HTML for assistant messages (GFM subset) */
import { escapeHtml, showToast, copyText, flashDone } from './utils.js';
import { icon } from './icons.js';

const FENCE_RE = /```(\w*)\n?([\s\S]*?)```/g;
const TILDE_FENCE_RE = /~~~(\w*)\n?([\s\S]*?)~~~/g;

// Bare URLs in already-escaped text: stop at escaped quotes / angle brackets.
const BARE_URL_RE = /https?:\/\/(?:(?!&quot;|&#39;|&lt;|&gt;)[^\s<>"'\x00])+/g;

/** Keep sentence punctuation (and an unbalanced ")") out of a bare link. */
function splitTrailing(url) {
  let end = url.length;
  while (end > 0) {
    const ch = url[end - 1];
    if ('.,;:!?*_'.includes(ch)) {
      end -= 1;
    } else if (ch === ')') {
      const body = url.slice(0, end);
      if ((body.match(/\(/g) || []).length < (body.match(/\)/g) || []).length) end -= 1;
      else break;
    } else {
      break;
    }
  }
  return [url.slice(0, end), url.slice(end)];
}

function sanitizeUrl(url) {
  const u = String(url || '').trim();
  if (/^(https?:|mailto:|#)/i.test(u)) return u;
  return '#';
}

function sanitizeImgUrl(url) {
  const u = String(url || '').trim();
  if (/^https?:\/\//i.test(u)) return u;
  return '';
}

function renderInline(text) {
  const tokens = [];
  let s = escapeHtml(text);

  s = s.replace(/`([^`\n]+)`/g, (_, code) => {
    const i = tokens.length;
    tokens.push(`<code>${code}</code>`);
    return `\x00T${i}\x00`;
  });

  s = s.replace(/!\[([^\]]*)\]\(([^)]+)\)/g, (_, alt, url) => {
    const src = sanitizeImgUrl(url);
    if (!src) return `![${alt}](${url})`;
    const i = tokens.length;
    const altEsc = escapeHtml(alt);
    if (alt) {
      tokens.push(
        `<figure class="md-figure"><img class="md-img" src="${src}" alt="${altEsc}" loading="lazy" decoding="async"><figcaption class="md-figcaption">${altEsc}</figcaption></figure>`
      );
    } else {
      tokens.push(`<img class="md-img" src="${src}" alt="" loading="lazy" decoding="async">`);
    }
    return `\x00T${i}\x00`;
  });

  // Links — after images so ![alt](url) isn't eaten
  s = s.replace(/\[([^\]]+)\]\(([^)]+)\)/g, (_, label, href) => {
    const i = tokens.length;
    tokens.push(`<a href="${sanitizeUrl(href)}" target="_blank" rel="noopener noreferrer">${label}</a>`);
    return `\x00T${i}\x00`;
  });

  s = s.replace(/~~([^~\n]+)~~/g, '<del>$1</del>');
  s = s.replace(/\*\*\*([^*\n]+)\*\*\*/g, '<strong><em>$1</em></strong>');
  s = s.replace(/___([^_\n]+)___/g, '<strong><em>$1</em></strong>');
  s = s.replace(/\*\*([^*\n]+)\*\*/g, '<strong>$1</strong>');
  s = s.replace(/__([^_\n]+)__/g, '<strong>$1</strong>');
  s = s.replace(/\*([^*\n]+)\*/g, '<em>$1</em>');
  s = s.replace(/(?<![a-zA-Z0-9])_([^_\n]+)_(?![a-zA-Z0-9])/g, '<em>$1</em>');
  s = s.replace(/<u>([^<\n]+)<\/u>/g, '<u>$1</u>');
  s = s.replace(/==([^=\n]+)==/g, '<mark>$1</mark>');

  s = s.replace(BARE_URL_RE, (match) => {
    const [url, tail] = splitTrailing(match);
    return `<a href="${sanitizeUrl(url)}" target="_blank" rel="noopener noreferrer">${url}</a>${tail}`;
  });

  return s.replace(/\x00T(\d+)\x00/g, (_, idx) => tokens[Number(idx)] || '');
}

function isTableRow(line) {
  const t = String(line || '').trim();
  if (!t.includes('|')) return false;
  return splitTableRow(t).length >= 2;
}

function isSeparatorRow(line) {
  const t = String(line || '').trim();
  if (!t.includes('|')) return false;
  return splitTableRow(t).every((cell) => /^:?-{3,}:?$/.test(cell));
}

function splitTableRow(line) {
  let t = String(line || '').trim();
  if (t.startsWith('|')) t = t.slice(1);
  if (t.endsWith('|')) t = t.slice(0, -1);
  return t.split('|').map((cell) => cell.trim());
}

function cellAlign(cell) {
  const t = String(cell || '').trim();
  if (t.startsWith(':') && t.endsWith(':')) return 'center';
  if (t.endsWith(':')) return 'right';
  return 'left';
}

function renderTable(lines) {
  if (lines.length < 2) return '';
  const header = splitTableRow(lines[0]);
  const aligns = splitTableRow(lines[1]).map(cellAlign);
  const rows = lines.slice(2).filter(isTableRow).map(splitTableRow);

  let html = '<div class="md-table-wrap" tabindex="0" role="region" aria-label="Table"><table class="md-table"><thead><tr>';
  header.forEach((cell, i) => {
    html += `<th style="text-align:${aligns[i] || 'left'}">${renderInline(cell)}</th>`;
  });
  html += '</tr></thead><tbody>';
  for (const row of rows) {
    html += '<tr>';
    for (let i = 0; i < header.length; i += 1) {
      const cell = row[i] || '';
      const mono = i === 0 && header.length === 2 ? ' class="md-table-mono"' : '';
      html += `<td${mono} style="text-align:${aligns[i] || 'left'}">${renderInline(cell)}</td>`;
    }
    html += '</tr>';
  }
  html += '</tbody></table></div>';
  return html;
}

function parseListItem(line) {
  const task = line.match(/^(\s*)[-*+]\s+\[([ xX])\]\s+(.*)/);
  if (task) {
    return {
      indent: task[1].length,
      ordered: false,
      task: true,
      checked: task[2].toLowerCase() === 'x',
      content: task[3],
    };
  }
  const bullet = line.match(/^(\s*)[-*+]\s+(.*)/);
  if (bullet) {
    return { indent: bullet[1].length, ordered: false, task: false, content: bullet[2] };
  }
  const ordered = line.match(/^(\s*)\d+\.\s+(.*)/);
  if (ordered) {
    return { indent: ordered[1].length, ordered: true, task: false, content: ordered[2] };
  }
  return null;
}

function renderListBlock(lines) {
  const items = lines.map(parseListItem).filter(Boolean);
  if (!items.length) return '';
  const ordered = items[0].ordered;
  let html = ordered ? '<ol class="md-list">' : '<ul class="md-list">';
  for (const item of items) {
    const depth = Math.floor(item.indent / 2);
    const depthCls = depth ? ` class="md-li-nest md-li-nest-${Math.min(depth, 4)}"` : '';
    if (item.task) {
      const cls = item.checked ? 'md-task md-task-done' : 'md-task';
      const nest = depth ? ` md-li-nest md-li-nest-${Math.min(depth, 4)}` : '';
      const box = item.checked ? '☑' : '☐';
      html += `<li class="${cls}${nest}"><span class="md-task-box" aria-hidden="true">${box}</span><span>${renderInline(item.content)}</span></li>`;
    } else {
      html += `<li${depthCls}>${renderInline(item.content)}</li>`;
    }
  }
  html += ordered ? '</ol>' : '</ul>';
  return html;
}

function isHr(line) {
  return /^(\*{3,}|-{3,}|_{3,})\s*$/.test(String(line || '').trim());
}

function renderBlocks(text) {
  const lines = text.split('\n');
  const blocks = [];
  let i = 0;

  while (i < lines.length) {
    const line = lines[i];

    if (!line.trim()) {
      i += 1;
      continue;
    }

    if (/^\x00CODE\d+\x00$/.test(line.trim())) {
      blocks.push(line.trim());
      i += 1;
      continue;
    }

    const heading = line.match(/^(#{1,6})\s+(.+)$/);
    if (heading) {
      const level = Math.min(heading[1].length, 6);
      blocks.push(`<h${level}>${renderInline(heading[2])}</h${level}>`);
      i += 1;
      continue;
    }

    if (isHr(line)) {
      blocks.push('<hr class="md-hr">');
      i += 1;
      continue;
    }

    if (/^>\s?/.test(line)) {
      const quoteLines = [];
      while (i < lines.length && /^>\s?/.test(lines[i])) {
        quoteLines.push(lines[i].replace(/^>\s?/, ''));
        i += 1;
      }
      blocks.push(`<blockquote class="md-quote">${quoteLines.map((l) => renderInline(l)).join('<br>')}</blockquote>`);
      continue;
    }

    if (isTableRow(line) && i + 1 < lines.length && isSeparatorRow(lines[i + 1])) {
      const tableLines = [line, lines[i + 1]];
      i += 2;
      while (i < lines.length && isTableRow(lines[i])) {
        tableLines.push(lines[i]);
        i += 1;
      }
      blocks.push(renderTable(tableLines));
      continue;
    }

    if (/^\s*([-*+]|\d+\.)\s+/.test(line)) {
      const listLines = [];
      while (i < lines.length && /^\s*([-*+]|\d+\.)\s+/.test(lines[i])) {
        listLines.push(lines[i]);
        i += 1;
      }
      blocks.push(renderListBlock(listLines));
      continue;
    }

    const paraLines = [];
    while (i < lines.length && lines[i].trim()) {
      const l = lines[i];
      if (/^\x00CODE\d+\x00$/.test(l.trim())) break;
      if (/^(#{1,6})\s/.test(l)) break;
      if (isHr(l)) break;
      if (/^>\s?/.test(l)) break;
      if (isTableRow(l) && i + 1 < lines.length && isSeparatorRow(lines[i + 1])) break;
      if (/^\s*([-*+]|\d+\.)\s+/.test(l)) break;
      paraLines.push(l);
      i += 1;
    }
    if (paraLines.length) {
      blocks.push(`<p>${paraLines.map((l) => renderInline(l)).join('<br>')}</p>`);
    }
  }

  return blocks.join('');
}

function renderCodeBlock(lang, code, store) {
  const idx = store.length;
  const trimmed = String(code).trimEnd();
  const langLabel = escapeHtml(lang || 'code');
  store.push(
    `<div class="md-code-block">` +
      `<div class="md-code-head">` +
        `<span class="md-code-lang">${langLabel}</span>` +
        `<button type="button" class="md-code-copy" aria-label="Copy code">` +
          `${icon('copy')}<span>Copy</span>` +
        `</button>` +
      `</div>` +
      `<pre class="md-pre"><code class="language-${escapeHtml(lang || 'text')}">${escapeHtml(trimmed)}</code></pre>` +
    `</div>`,
  );
  return `\x00CODE${idx}\x00`;
}

function extractFences(text, store) {
  return text
    .replace(FENCE_RE, (_, lang, code) => renderCodeBlock(lang, code, store))
    .replace(TILDE_FENCE_RE, (_, lang, code) => renderCodeBlock(lang, code, store));
}

export function renderMarkdown(source, { streaming = false } = {}) {
  if (!source) return '';

  let src = String(source).replace(/\r\n?/g, '\n');
  // Mid-stream a code fence is often still open — close it so the partial
  // block renders as code instead of flashing raw backticks.
  if (streaming && ((src.match(/^\s*```/gm) || []).length % 2 === 1)) src += '\n```';

  const codeBlocks = [];
  let text = extractFences(src, codeBlocks);
  text = renderBlocks(text);
  text = text.replace(/\x00CODE(\d+)\x00/g, (_, idx) => codeBlocks[Number(idx)] || '');
  return text;
}

/** Plain text with linkified URLs (composer preview, etc.) */
export function renderPlainWithLinks(text) {
  const escaped = escapeHtml(text);
  return escaped.replace(
    /(https?:\/\/[^\s<]+)/g,
    (url) => `<a href="${sanitizeUrl(url)}" target="_blank" rel="noopener noreferrer">${url}</a>`
  );
}

export function applyMarkdownLinks(container) {
  if (!container) return;
  container.querySelectorAll('.md-code-copy:not([data-bound])').forEach((btn) => {
    btn.dataset.bound = '1';
    btn.addEventListener('click', async (e) => {
      e.preventDefault();
      e.stopPropagation();
      const code = btn.closest('.md-code-block')?.querySelector('code');
      const ok = await copyText(code?.textContent || '');
      if (ok) flashDone(btn);
      else showToast('Copy failed — select the text instead', true);
    });
  });
}
