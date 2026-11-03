"""jarvis/web/static/js/linediff.js — the line diff behind the editor's change gutter, run under Node.

Pure module (no DOM, no imports), so it is fuzzed here: for any two texts, applying
the hunks to the old lines must give exactly the new ones. Skipped without Node.
"""
from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

from jarvis.web import handler as web_handler

NODE = shutil.which("node")
LINEDIFF = Path(web_handler.__file__).with_name("static") / "js" / "linediff.js"

pytestmark = pytest.mark.skipif(NODE is None, reason="node not installed")


def run_js(body: str, timeout: int = 60):
    script = f"import {{ diffLines, MAX_EDITS }} from {json.dumps(LINEDIFF.as_uri())};\n{body}"
    res = subprocess.run([NODE, "--input-type=module", "-e", script], capture_output=True, text=True, timeout=timeout)
    assert res.returncode == 0, res.stderr
    return json.loads(res.stdout.strip())


def test_kinds_and_positions():
    out = run_js("""
        const d = (a, b) => diffLines(a.split(''), b.split(''));
        console.log(JSON.stringify({
          same: d('abc', 'abc'),
          added: d('ac', 'abc'),
          deleted: d('abc', 'ac'),
          modified: d('abc', 'aXc'),
          tail: d('abc', 'abcde'),
          head: d('abc', 'zabc'),
          empty: d('', 'ab'),
          wipe: d('ab', ''),
          two: d('abcdef', 'aXcdeYf'),
        }));
    """)
    assert out["same"] == []
    assert out["added"] == [{"aStart": 1, "aLen": 0, "bStart": 1, "bLen": 1, "kind": "added"}]
    assert out["deleted"] == [{"aStart": 1, "aLen": 1, "bStart": 1, "bLen": 0, "kind": "deleted"}]
    assert out["modified"] == [{"aStart": 1, "aLen": 1, "bStart": 1, "bLen": 1, "kind": "modified"}]
    assert out["tail"] == [{"aStart": 3, "aLen": 0, "bStart": 3, "bLen": 2, "kind": "added"}]
    assert out["head"] == [{"aStart": 0, "aLen": 0, "bStart": 0, "bLen": 1, "kind": "added"}]
    assert out["empty"][0]["kind"] == "added" and out["empty"][0]["bLen"] == 2
    assert out["wipe"][0]["kind"] == "deleted" and out["wipe"][0]["aLen"] == 2
    assert [h["kind"] for h in out["two"]] == ["modified", "added"]


def test_hunks_rebuild_the_new_text_for_random_edits():
    out = run_js("""
        let seed = 12345;
        const rnd = (n) => { seed = (seed * 1103515245 + 12345) & 0x7fffffff; return seed % n; };
        const word = () => 'abcdef'[rnd(6)];
        let bad = null;
        for (let t = 0; t < 4000 && !bad; t += 1) {
          const a = Array.from({ length: rnd(40) }, word);
          const b = a.slice();
          for (let e = rnd(6); e > 0; e -= 1) {
            const at = rnd(b.length + 1);
            if (rnd(2)) b.splice(at, rnd(4), ...Array.from({ length: rnd(4) }, word));
            else b.splice(at, rnd(4));
          }
          const hunks = diffLines(a, b);
          // apply: copy untouched lines of a, take b's lines for each hunk
          const rebuilt = [];
          let i = 0;
          let last = { aEnd: 0, bEnd: 0 };
          for (const h of hunks) {
            if (h.aStart < last.aEnd || h.bStart < last.bEnd) bad = { why: 'overlap', a, b, hunks };
            while (i < h.aStart) { rebuilt.push(a[i]); i += 1; }
            if (h.bStart !== rebuilt.length) bad = { why: 'b position', a, b, hunks };
            rebuilt.push(...b.slice(h.bStart, h.bStart + h.bLen));
            i = h.aStart + h.aLen;
            last = { aEnd: i, bEnd: h.bStart + h.bLen };
            if (!h.aLen && !h.bLen) bad = { why: 'empty hunk', a, b, hunks };
          }
          while (i < a.length) { rebuilt.push(a[i]); i += 1; }
          if (rebuilt.join('') !== b.join('')) bad = { why: 'rebuild', a, b, hunks };
        }
        console.log(JSON.stringify({ bad }));
    """)
    assert out["bad"] is None, out["bad"]


def test_a_rewrite_past_the_cap_still_gives_a_correct_coarse_answer_quickly():
    out = run_js("""
        const n = MAX_EDITS * 3;
        const a = Array.from({ length: n }, (_, i) => `old ${i}`);
        const b = Array.from({ length: n }, (_, i) => `new ${i}`);
        a.unshift('same top'); b.unshift('same top');
        a.push('same bottom'); b.push('same bottom');
        const t0 = Date.now();
        const hunks = diffLines(a, b);
        console.log(JSON.stringify({ hunks, ms: Date.now() - t0 }));
    """)
    assert out["hunks"] == [{"aStart": 1, "aLen": 4500, "bStart": 1, "bLen": 4500, "kind": "modified"}]
    assert out["ms"] < 3000


def test_a_small_edit_in_a_big_file_is_fast_and_exact():
    out = run_js("""
        const a = Array.from({ length: 40000 }, (_, i) => `line ${i}`);
        const b = a.slice();
        b[20000] = 'changed'; b.splice(30000, 0, 'inserted'); b.splice(10, 1);
        const t0 = Date.now();
        const hunks = diffLines(a, b);
        console.log(JSON.stringify({ kinds: hunks.map((h) => h.kind), at: hunks.map((h) => h.bStart), ms: Date.now() - t0 }));
    """)
    assert out["kinds"] == ["deleted", "modified", "added"]
    assert out["at"] == [10, 19999, 29999]
    assert out["ms"] < 1500
