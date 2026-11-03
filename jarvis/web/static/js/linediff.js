/** Line diff for the Files editor's gutter: which lines of the text you are
 * editing were added, changed or removed compared with what git has.
 *
 * `diffLines(before, after)` takes two arrays of lines and returns the
 * changes as hunks, in order and never overlapping:
 *
 *   { aStart, aLen, bStart, bLen, kind }
 *
 * `a*` index `before`, `b*` index `after`. kind is "added" (aLen 0),
 * "deleted" (bLen 0) or "modified". A deleted hunk's `bStart` is the line the
 * removed text used to sit above.
 *
 * Pure — no DOM, no imports — so it can be checked under Node. Runs on every
 * (debounced) keystroke: the common start and end are trimmed first, so a small
 * edit in a big file costs almost nothing; Myers' O(ND) search does the rest,
 * and gives up past MAX_EDITS (a rewrite of most of a file) by calling the
 * whole differing middle one change — still correct, just coarser. */

export const MAX_EDITS = 1500;

function kindOf(h) {
  if (!h.aLen) return 'added';
  if (!h.bLen) return 'deleted';
  return 'modified';
}

/** Myers' shortest edit script between `a` and `b` (both non-empty). Returns
 * `{ del, ins }` — flags per line of `a` / `b` — or null when more than
 * MAX_EDITS lines differ. */
function myers(a, b) {
  const n = a.length;
  const m = b.length;
  const max = Math.min(n + m, MAX_EDITS);
  const off = max + 1;
  const v = new Int32Array(2 * max + 3);
  const trace = [];
  let found = -1;
  for (let d = 0; d <= max && found < 0; d += 1) {
    trace.push(v.slice(off - d - 1, off + d + 2)); // v as round d-1 left it
    for (let k = -d; k <= d; k += 2) {
      let x = (k === -d || (k !== d && v[off + k - 1] < v[off + k + 1])) ? v[off + k + 1] : v[off + k - 1] + 1;
      let y = x - k;
      while (x < n && y < m && a[x] === b[y]) {
        x += 1;
        y += 1;
      }
      v[off + k] = x;
      if (x >= n && y >= m) {
        found = d;
        break;
      }
    }
  }
  if (found < 0) return null;
  const del = new Uint8Array(n);
  const ins = new Uint8Array(m);
  let x = n;
  let y = m;
  for (let d = found; d > 0; d -= 1) {
    const snap = trace[d]; // index of k is k + d + 1
    const k = x - y;
    const down = k === -d || (k !== d && snap[k - 1 + d + 1] < snap[k + 1 + d + 1]);
    const prevK = down ? k + 1 : k - 1;
    const prevX = snap[prevK + d + 1];
    const prevY = prevX - prevK;
    while (x > prevX && y > prevY) {
      x -= 1;
      y -= 1;
    }
    if (down) {
      y -= 1;
      ins[y] = 1;
    } else {
      x -= 1;
      del[x] = 1;
    }
  }
  return { del, ins };
}

export function diffLines(before, after) {
  const a = before;
  const b = after;
  let p = 0;
  while (p < a.length && p < b.length && a[p] === b[p]) p += 1;
  let s = 0;
  while (s < a.length - p && s < b.length - p && a[a.length - 1 - s] === b[b.length - 1 - s]) s += 1;
  const n = a.length - p - s;
  const m = b.length - p - s;
  if (!n && !m) return [];
  const whole = () => [{ aStart: p, aLen: n, bStart: p, bLen: m }];
  let hunks;
  if (!n || !m) {
    hunks = whole();
  } else {
    const flags = myers(a.slice(p, p + n), b.slice(p, p + m));
    if (!flags) {
      hunks = whole();
    } else {
      hunks = [];
      let i = 0;
      let j = 0;
      while (i < n || j < m) {
        if (i < n && j < m && !flags.del[i] && !flags.ins[j]) {
          i += 1;
          j += 1;
          continue;
        }
        const aStart = i;
        const bStart = j;
        while (i < n && flags.del[i]) i += 1;
        while (j < m && flags.ins[j]) j += 1;
        hunks.push({ aStart: p + aStart, aLen: i - aStart, bStart: p + bStart, bLen: j - bStart });
      }
    }
  }
  return hunks.map((h) => ({ ...h, kind: kindOf(h) }));
}
