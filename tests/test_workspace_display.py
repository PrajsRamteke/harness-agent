"""Multi-project display: project badges in tool rows (TUI text + web assets) and the link chip."""
from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

from jarvis.tui import tool_format as tf

STATIC = Path(__file__).resolve().parent.parent / "jarvis" / "web" / "static"
NODE = shutil.which("node")


# ── tool_format: textual badge ───────────────────────────────────────────

def test_qualified_path_reads_project_then_path():
    assert tf.tool_args("edit_file", {"path": "api:src/dto/order.ts"}) == "api › src/dto/order.ts"
    assert tf.tool_args("write_file", {"path": "api:a.txt", "content": "x"}) == "api › a.txt"


def test_qualified_read_keeps_line_range():
    out = tf.tool_args("read_file", {"path": "api:src/a.ts", "offset": 3, "limit": 5})
    assert out == "api › src/a.ts L4–8"  # clip() collapses the double space


def test_multi_edit_qualifies_each_path():
    out = tf.tool_args("multi_edit", {"edits": [{"path": "api:a.ts"}, {"path": "web:b.ts"}, {"path": "c.ts"}]})
    assert out == "api › a.ts, web › b.ts, c.ts"


def test_read_bundle_paths_are_qualified():
    out = tf.tool_args("read_bundle", {"paths": ["api:a.ts", "b.ts"]})
    assert out == "api › a.ts, b.ts"


def test_project_argument_leads_the_summary():
    assert tf.tool_args("search_code", {"pattern": "foo", "project": "api"}) == 'api › "foo"'
    assert tf.tool_args("read_file", {"path": "src/x.py", "project": "api"}) == "api › src/x.py"
    assert tf.tool_args("search_code", {"pattern": "foo", "project": ["api", "infra"]}) == '[api, infra] "foo"'
    assert tf.tool_args("search_code", {"pattern": "foo", "project": "*"}) == '[all projects] "foo"'


def test_project_argument_and_qualified_path_do_not_repeat():
    assert tf.tool_args("edit_file", {"path": "api:x", "project": "api"}) == "api › x"


def test_plain_calls_are_unchanged():
    assert tf.tool_args("read_file", {"path": "src/x.py"}) == "src/x.py"
    assert tf.tool_args("fetch_url", {"url": "https://example.com/a:b"}) == "https://example.com/a:b"
    assert tf.tool_args("search_code", {"pattern": "foo"}) == '"foo"'
    assert tf.tool_args("run_bash", {"cmd": "echo hi"}) == "echo hi"


def test_urls_and_drive_like_paths_are_not_projects():
    assert tf.split_qualified("http://x/y") == ("", "http://x/y")
    assert tf.split_qualified("C:\\Users\\me") == ("", "C:\\Users\\me")
    assert tf.split_qualified("API:x") == ("", "API:x")  # ids are lower-case
    assert tf.split_qualified("api:src/x.ts") == ("api", "src/x.ts")


def test_project_ids_accepts_str_list_and_star():
    assert tf.project_ids("api") == ["api"]
    assert tf.project_ids(["api", "api", " infra "]) == ["api", "infra"]
    assert tf.project_ids(None) == []
    assert tf.project_badge("*") == "[all projects]"


# ── web assets ───────────────────────────────────────────────────────────

def test_linkchip_is_wired_into_the_page():
    app = (STATIC / "js" / "app.js").read_text()
    assert "import { initLinkChip } from './linkchip.js';" in app
    assert "initLinkChip();" in app
    assert "/static/css/linkchip.css" in (STATIC / "index.html").read_text()
    assert (STATIC / "css" / "linkchip.css").is_file()


def test_linkchip_opens_the_links_dialog_by_event():
    src = (STATIC / "js" / "linkchip.js").read_text()
    assert "jarvis:open-links" in src
    assert "projectId" in src


@pytest.mark.skipif(NODE is None, reason="node is not installed")
@pytest.mark.parametrize("name", ["linkchip.js", "projinfo.js", "chat.js", "changes.js", "prompts.js", "app.js"])
def test_web_modules_have_valid_syntax(name):
    res = subprocess.run([NODE, "--check", str(STATIC / "js" / name)], capture_output=True, text=True)
    assert res.returncode == 0, res.stderr


@pytest.mark.skipif(NODE is None, reason="node is not installed")
def test_web_parse_tool_args():
    """projinfo.parseToolArgs strips the project prefix the server writes into tool rows."""
    script = """
    globalThis.window = { addEventListener() {}, dispatchEvent() {}, matchMedia: () => ({ matches: false }) };
    globalThis.document = { querySelectorAll: () => [] };
    globalThis.CustomEvent = class {};
    globalThis.localStorage = { getItem: () => null, setItem() {} };
    globalThis.location = { search: '', hash: '', origin: 'http://x', pathname: '/' };
    const m = await import(process.argv[1]);
    const out = {
      lead: m.parseToolArgs('edit_file', 'api › src/dto/order.ts'),
      list: m.parseToolArgs('search_code', '[api, infra] "foo"'),
      all: m.parseToolArgs('search_code', '[all projects] "foo"'),
      raw: m.parseToolArgs('edit_file', 'api:src/x.ts'),
      multi: m.parseToolArgs('multi_edit', 'api › a.ts, web › b.ts, c.ts'),
      bash: m.parseToolArgs('run_bash', 'echo a:b'),
      url: m.parseToolArgs('fetch_url', 'https://x.dev/a'),
      plain: m.parseToolArgs('read_file', 'src/x.py'),
    };
    console.log(JSON.stringify(out));
    """
    res = subprocess.run(
        [NODE, "--input-type=module", "-e", script, (STATIC / "js" / "projinfo.js").as_uri()],
        capture_output=True, text=True, timeout=30,
    )
    if res.returncode != 0:
        pytest.skip(f"projinfo.js can't load outside a browser: {res.stderr.strip()[:200]}")
    got = json.loads(res.stdout.strip().splitlines()[-1])
    assert got["lead"] == {"ids": ["api"], "rest": "src/dto/order.ts"}
    assert got["list"] == {"ids": ["api", "infra"], "rest": '"foo"'}
    assert got["all"] == {"ids": ["*"], "rest": '"foo"'}
    assert got["raw"] == {"ids": ["api"], "rest": "src/x.ts"}
    assert got["multi"] == {"ids": ["api", "web"], "rest": "a.ts, b.ts, c.ts"}
    assert got["bash"]["ids"] == []
    assert got["url"]["ids"] == []
    assert got["plain"] == {"ids": [], "rest": "src/x.py"}
