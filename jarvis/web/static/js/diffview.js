/** A file's numbered diff hunks (jarvis/file_changes.py) as HTML.
 *
 * Unified or side by side, with the changed words marked inside replaced
 * lines. Pure strings — no DOM and no imports, so it can be checked in Node.
 *
 * A hunk is `{ old_start, old_len, new_start, new_len, rows }`; a row is
 * `[kind, old_no, new_no, text]` with kind " " (context), "+" or "-".
 */

const esc = (s) => String(s).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
const clean = (s) => String(s ?? '').replace(/\r$/, '');
const isWord = (ch) => !!ch && /[\p{L}\p{N}_]/u.test(ch);

/**
 * The words that differ between a replaced line and its replacement:
 * `[htmlBefore, htmlAfter]`, or null when the lines share too little for
 * highlighting to help (then the whole line is the change anyway).
 */
export function wordMarks(a, b) {
  const max = Math.min(a.length, b.length);
  let p = 0;
  while (p < max && a[p] === b[p]) p += 1;
  // Whole words: back up when the shared start ends mid-word.
  while (p > 0 && isWord(a[p - 1]) && (isWord(a[p]) || isWord(b[p]))) p -= 1;
  let s = 0;
  while (s < max - p && a[a.length - 1 - s] === b[b.length - 1 - s]) s += 1;
  while (s > 0 && isWord(a[a.length - s]) && (isWord(a[a.length - s - 1]) || isWord(b[b.length - s - 1]))) s -= 1;
  const longest = Math.max(a.length, b.length);
  if (!longest || p + s < longest * 0.3) return null;
  const wrap = (str) => {
    const mid = str.slice(p, str.length - s);
    return esc(str.slice(0, p)) + (mid ? `<mark class="wd">${esc(mid)}</mark>` : '') + esc(str.slice(str.length - s));
  };
  return [wrap(a), wrap(b)];
}

/** Set of `kind␀text` for every changed row — what "already on screen" means. */
export function rowKeys(hunks) {
  const keys = new Set();
  for (const h of hunks || []) {
    for (const [kind, , , text] of h.rows) if (kind !== ' ') keys.add(`${kind}\u0000${text}`);
  }
  return keys;
}

export function countRows(hunks) {
  return (hunks || []).reduce((n, h) => n + h.rows.length, 0);
}

/** Width in px of one line-number column. */
export function gutterWidth(hunks) {
  let top = 0;
  for (const h of hunks || []) {
    top = Math.max(top, h.old_start + h.old_len, h.new_start + h.new_len);
  }
  return Math.max(3, String(top).length) * 7 + 14;
}

/** Context rows stay single; a run of "-" rows followed by "+" rows becomes one change. */
function group(rows) {
  const items = [];
  let i = 0;
  while (i < rows.length) {
    if (rows[i][0] === ' ') {
      items.push({ ctx: rows[i] });
      i += 1;
      continue;
    }
    const dels = [];
    const adds = [];
    while (i < rows.length && rows[i][0] === '-') dels.push(rows[i++]);
    while (i < rows.length && rows[i][0] === '+') adds.push(rows[i++]);
    items.push({ dels, adds });
  }
  return items;
}

/** Highlighted html for the rows of a change: [delHtml…], [addHtml…]. */
function marked(dels, adds) {
  const d = dels.map((r) => esc(clean(r[3])));
  const a = adds.map((r) => esc(clean(r[3])));
  if (dels.length === adds.length) {
    dels.forEach((r, i) => {
      const m = wordMarks(clean(r[3]), clean(adds[i][3]));
      if (m) [d[i], a[i]] = m;
    });
  }
  return [d, a];
}

const SIGN = { '+': '+', '-': '−', ' ': '' };
const hunkHead = (h) => `<div class="dv-hunk"><b>@@ −${h.old_start},${h.old_len} +${h.new_start},${h.new_len} @@</b></div>`;

/**
 * `{ html, shown, total }` — `limit` rows are drawn at most (the rest is
 * `total - shown`); `keys` (from an earlier render) flags rows that are new.
 */
export function renderDiff(hunks, { view = 'unified', keys = null, limit = Infinity } = {}) {
  const total = countRows(hunks);
  const split = view === 'split';
  const fresh = (kind, text) => (keys && kind !== ' ' && !keys.has(`${kind}\u0000${text}`) ? ' is-live' : '');
  let shown = 0;
  let html = '<div class="dv-rows">';

  for (const h of hunks || []) {
    if (shown >= limit) break;
    html += hunkHead(h);
    for (const item of group(h.rows)) {
      if (shown >= limit) break;
      if (item.ctx) {
        const [, o, n, text] = item.ctx;
        const t = esc(clean(text)) || ' ';
        html += split
          ? `<div class="dv-srow"><span class="dv-n">${o}</span><span class="dv-cell">${t}</span><span class="dv-n">${n}</span><span class="dv-cell">${t}</span></div>`
          : `<div class="dv-row"><span class="dv-n">${o}</span><span class="dv-n">${n}</span><span class="dv-sign"></span><span class="dv-code">${t}</span></div>`;
        shown += 1;
        continue;
      }
      const [d, a] = marked(item.dels, item.adds);
      if (split) {
        const rows = Math.max(item.dels.length, item.adds.length);
        for (let k = 0; k < rows && shown < limit; k += 1) {
          const del = item.dels[k];
          const add = item.adds[k];
          const live = (del && fresh('-', del[3])) || (add && fresh('+', add[3]));
          html += `<div class="dv-srow${live}">`
            + (del ? `<span class="dv-n is-del">${del[1]}</span><span class="dv-cell is-del">${d[k] || ' '}</span>` : '<span class="dv-n"></span><span class="dv-cell is-void"></span>')
            + (add ? `<span class="dv-n is-add">${add[2]}</span><span class="dv-cell is-add">${a[k] || ' '}</span>` : '<span class="dv-n"></span><span class="dv-cell is-void"></span>')
            + '</div>';
          shown += 1;
        }
        continue;
      }
      for (let k = 0; k < item.dels.length && shown < limit; k += 1) {
        const r = item.dels[k];
        html += `<div class="dv-row is-del${fresh('-', r[3])}"><span class="dv-n">${r[1]}</span><span class="dv-n"></span><span class="dv-sign">${SIGN['-']}</span><span class="dv-code">${d[k] || ' '}</span></div>`;
        shown += 1;
      }
      for (let k = 0; k < item.adds.length && shown < limit; k += 1) {
        const r = item.adds[k];
        html += `<div class="dv-row is-add${fresh('+', r[3])}"><span class="dv-n"></span><span class="dv-n">${r[2]}</span><span class="dv-sign">${SIGN['+']}</span><span class="dv-code">${a[k] || ' '}</span></div>`;
        shown += 1;
      }
    }
  }
  return { html: `${html}</div>`, shown, total };
}
