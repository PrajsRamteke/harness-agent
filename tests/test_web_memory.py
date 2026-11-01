"""Web Memory dialog: facts + lessons as a dashboard (``jarvis/web/memory_api.py``).

Covers the two stores' new edit / undo calls, the API ops (add · update ·
delete · clear · restore) with their validation, the HTTP routes + the
``memory`` event, and the page wiring (/memory and /lesson open the dialog
in the browser instead of the terminal).
"""
from __future__ import annotations

import json
import re
import socket
import urllib.error
import urllib.request
from pathlib import Path

import pytest

from jarvis.storage import lessons as ls
from jarvis.storage import memory as mem
from jarvis.web import memory_api as ma
from jarvis.web.bridge import WebBridge

STATIC = Path(ma.__file__).with_name("static")


@pytest.fixture(autouse=True)
def _empty_stores():
    """Each test starts with no facts and no lessons (and leaves none behind)."""
    mem.clear_all()
    ls.clear_all()
    yield
    mem.clear_all()
    ls.clear_all()


# ─── Stores ───────────────────────────────────────────────────────────────


def test_update_fact_rewrites_text_and_keeps_id_and_date():
    f = mem.add_fact("name: Prajwal")
    out = mem.update_fact(f["id"], "  name: Prajwal R  ")
    assert out["text"] == "name: Prajwal R" and out["id"] == f["id"] and out["ts"] == f["ts"]
    assert [x["text"] for x in mem.list_facts()] == ["name: Prajwal R"]


def test_update_fact_refuses_empty_and_copies_and_reports_missing():
    a = mem.add_fact("likes tea")
    mem.add_fact("likes coffee")
    with pytest.raises(ValueError):
        mem.update_fact(a["id"], "   ")
    with pytest.raises(ValueError):
        mem.update_fact(a["id"], "LIKES COFFEE")
    assert mem.update_fact(a["id"], "likes tea") is not None  # its own text is fine
    assert mem.update_fact(9999, "x") is None


def test_restore_facts_brings_back_ids_and_skips_what_is_there():
    a, b = mem.add_fact("one"), mem.add_fact("two")
    mem.delete_fact(a["id"])
    mem.delete_fact(b["id"])
    assert mem.restore_facts([a, b, a]) == 2  # the repeat is skipped
    assert [f["id"] for f in mem.list_facts()] == [a["id"], b["id"]]
    assert mem.restore_facts([a]) == 0
    assert mem.add_fact("three")["id"] > b["id"]  # ids never collide after a restore


def test_update_lesson_and_restore_keep_hits_and_tags():
    s = ls.add_lesson("rebase", "use -i", ["Git"])
    ls.bump_hits(s["id"])
    out = ls.update_lesson(s["id"], "rebase a branch", "git rebase -i HEAD~N", ["git", "  Rebase "])
    assert out["task"] == "rebase a branch" and out["tags"] == ["git", "rebase"] and out["hits"] == 1
    with pytest.raises(ValueError):
        ls.update_lesson(s["id"], "", "x")
    assert ls.update_lesson(9999, "a", "b") is None
    saved = [dict(x) for x in ls.list_lessons()]
    ls.delete_lesson(s["id"])
    assert ls.restore_lessons(saved) == 1
    back = ls.list_lessons()[0]
    assert back["id"] == s["id"] and back["hits"] == 1 and back["tags"] == ["git", "rebase"]
    assert ls.restore_lessons(saved) == 0


# ─── API ──────────────────────────────────────────────────────────────────


def test_public_has_both_stores_with_the_dashboard_numbers():
    mem.add_fact("name: Prajwal")
    mem.add_fact("uses zsh")
    a = ls.add_lesson("git rebase", "use -i", ["git", "rebase"])
    ls.add_lesson("css grid", "minmax(0,1fr)", ["css", "git"])
    ls.bump_hits(a["id"])
    ls.bump_hits(a["id"])
    pub = ma.public()
    assert pub["memory"]["count"] == 2 and pub["memory"]["chars"] == len("name: Prajwal") + len("uses zsh")
    assert {f["text"] for f in pub["memory"]["items"]} == {"name: Prajwal", "uses zsh"}
    les = pub["lessons"]
    assert les["count"] == 2 and les["hits"] == 2 and les["limit"] == ls.MAX_STORE
    assert dict(les["tags"]) == {"git": 2, "rebase": 1, "css": 1} and les["tags"][0] == ["git", 2]
    assert pub["memory"]["file"].endswith("memory.json") and les["file"].endswith("lessons.json")


def test_add_update_delete_a_fact():
    res = ma.apply({"op": "add", "kind": "fact", "text": "  prefers pnpm "})
    assert res["ok"] and res["data"]["memory"]["items"][0]["text"] == "prefers pnpm"
    fid = res["id"]
    assert ma.apply({"op": "update", "kind": "fact", "id": fid, "text": "prefers pnpm over npm"})["ok"]
    assert mem.list_facts()[0]["text"] == "prefers pnpm over npm"
    res = ma.apply({"op": "delete", "kind": "fact", "id": fid})
    assert res["ok"] and res["data"]["memory"]["count"] == 0


def test_fact_validation_errors_come_back_with_fresh_data():
    ma.apply({"op": "add", "kind": "fact", "text": "likes tea"})
    for body, needle in (
        ({"op": "add", "kind": "fact", "text": "   "}, "Write something"),
        ({"op": "add", "kind": "fact", "text": "Likes TEA"}, "already remembers"),
        ({"op": "add", "kind": "fact", "text": "x" * (ma.MAX_FACT_CHARS + 1)}, "under"),
        ({"op": "update", "kind": "fact", "id": 9999, "text": "x"}, "gone"),
        ({"op": "update", "kind": "fact", "text": "x"}, "Missing item id"),
        ({"op": "delete", "kind": "fact", "id": 9999}, "already removed"),
    ):
        res = ma.apply(body)
        assert not res["ok"] and needle in res["error"], body
        assert res["data"]["memory"]["count"] == 1  # the dialog repaints from this
    assert not ma.apply({"op": "add", "kind": "nope", "text": "x"})["ok"]
    assert not ma.apply({"op": "explode", "kind": "fact"})["ok"]
    assert mem.list_facts()[0]["text"] == "likes tea"


def test_a_duplicate_edit_is_refused_in_plain_words():
    a = ma.apply({"op": "add", "kind": "fact", "text": "one"})["id"]
    ma.apply({"op": "add", "kind": "fact", "text": "two"})
    res = ma.apply({"op": "update", "kind": "fact", "id": a, "text": "TWO"})
    assert not res["ok"] and "already remembers" in res["error"]


def test_add_and_edit_a_lesson_with_tags_as_text_or_list():
    res = ma.apply({"op": "add", "kind": "lesson", "task": "redux saga", "lesson": "wrap in put()",
                    "tags": "#Redux, saga ,, redux"})
    assert res["ok"]
    s = res["data"]["lessons"]["items"][0]
    assert s["tags"] == ["redux", "saga"] and s["hits"] == 0
    res = ma.apply({"op": "update", "kind": "lesson", "id": res["id"], "task": "redux saga",
                    "lesson": "always wrap dispatch in put()", "tags": ["redux", "typescript"]})
    assert res["ok"] and ls.list_lessons()[0]["tags"] == ["redux", "typescript"]
    assert ls.list_lessons()[0]["lesson"] == "always wrap dispatch in put()"


def test_lesson_needs_task_and_lesson_and_respects_limits():
    for body in ({"task": "t", "lesson": " "}, {"task": "", "lesson": "l"},
                 {"task": "t" * (ma.MAX_TASK_CHARS + 1), "lesson": "l"},
                 {"task": "t", "lesson": "l" * (ma.MAX_LESSON_CHARS + 1)}):
        res = ma.apply({"op": "add", "kind": "lesson", **body})
        assert not res["ok"] and res["error"]
    assert ls.list_lessons() == []
    many = ",".join(f"tag{i}" for i in range(40))
    res = ma.apply({"op": "add", "kind": "lesson", "task": "t", "lesson": "l", "tags": many})
    assert len(res["data"]["lessons"]["items"][0]["tags"]) == ma.MAX_TAGS


def test_clear_then_restore_is_an_undo():
    ma.apply({"op": "add", "kind": "fact", "text": "a"})
    ma.apply({"op": "add", "kind": "fact", "text": "b"})
    before = ma.public()["memory"]["items"]
    res = ma.apply({"op": "clear", "kind": "fact"})
    assert res["ok"] and res["removed"] == 2 and res["data"]["memory"]["count"] == 0
    res = ma.apply({"op": "restore", "kind": "fact", "items": before})
    assert res["ok"] and res["restored"] == 2 and res["data"]["memory"]["items"] == before


def test_delete_lesson_then_restore_keeps_how_often_it_was_used():
    a = ma.apply({"op": "add", "kind": "lesson", "task": "t", "lesson": "l", "tags": "x"})["id"]
    ls.bump_hits(a)
    item = ma.public()["lessons"]["items"][0]
    assert ma.apply({"op": "delete", "kind": "lesson", "id": a})["data"]["lessons"]["count"] == 0
    res = ma.apply({"op": "restore", "kind": "lesson", "items": [item]})
    assert res["ok"] and res["data"]["lessons"]["items"][0] == item


def test_restore_ignores_junk_and_empty_lists():
    assert not ma.apply({"op": "restore", "kind": "fact", "items": []})["ok"]
    assert not ma.apply({"op": "restore", "kind": "fact"})["ok"]
    res = ma.apply({"op": "restore", "kind": "fact", "items": ["x", {"text": "no id"}, {"id": "7", "text": "s"}]})
    assert res["ok"] and res["restored"] == 0


# ─── HTTP ─────────────────────────────────────────────────────────────────


def test_http_routes_and_the_memory_event():
    from jarvis.web.server import start_web_server, stop_web_server

    bridge = WebBridge()
    sub = bridge.subscribe()
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    server, _urls, port = start_web_server(bridge=bridge, app=None, port=port, host="127.0.0.1")
    base = f"http://127.0.0.1:{port}"
    auth = {"Content-Type": "application/json", "Authorization": f"Bearer {bridge.token}"}
    try:
        with pytest.raises(urllib.error.HTTPError):
            urllib.request.urlopen(f"{base}/api/memory", timeout=5)
        req = urllib.request.Request(
            f"{base}/api/memory", method="POST", headers=auth,
            data=json.dumps({"op": "add", "kind": "fact", "text": "name: Prajwal"}).encode(),
        )
        with urllib.request.urlopen(req, timeout=5) as r:
            body = json.loads(r.read())
        assert body["ok"] and body["data"]["memory"]["count"] == 1
        with urllib.request.urlopen(f"{base}/api/memory?token={bridge.token}", timeout=5) as r:
            got = json.loads(r.read())
        assert got["memory"]["items"][0]["text"] == "name: Prajwal" and got["lessons"]["count"] == 0
        events = []
        while not sub.empty():
            events.append(json.loads(sub.get_nowait()))
        pushed = [e for e in events if e["type"] == "memory"]
        assert pushed and pushed[-1]["data"]["memory"]["count"] == 1  # every open page repaints
    finally:
        stop_web_server(server, bridge)


# ─── Page wiring ──────────────────────────────────────────────────────────


def _text(*parts: str) -> str:
    return STATIC.joinpath(*parts).read_text()


def test_memory_and_lesson_open_in_the_browser_not_the_terminal():
    catalog = _text("js", "catalog.js")
    for cmd, kind in (("/memory", "memory"), ("/memories", "memory"), ("/lesson", "lesson"), ("/lessons", "lesson")):
        assert f"'{cmd}': '{kind}'" in catalog, cmd
    for cmd, kind in (("/memory", "memory"), ("/lesson", "lesson")):
        entry = re.search(r"\{[^{}]*cmd: '%s'[^{}]*\}" % cmd, catalog).group(0)
        assert f"picker: '{kind}'" in entry and "laptop: true" not in entry
    pickers = _text("js", "pickers.js")
    assert "kind === 'memory' || kind === 'lesson'" in pickers and "openMemory(" in pickers


def test_dialog_markup_css_and_events_are_wired():
    html = _text("index.html")
    assert '<div class="modal" id="memory"' in html and "/static/css/memory.css" in html
    for part in ("memory-tabs", "memory-stats", "memory-bar", "memory-body", "memory-foot"):
        assert f'id="{part}"' in html, part
    assert "handleMemoryEvent" in _text("js", "events.js") and "case 'memory':" in _text("js", "events.js")
    assert "initMemory()" in _text("js", "app.js")
    api = _text("js", "api.js")
    assert "fetchMemory" in api and "extPost('memory'" in api
    css = _text("css", "memory.css")
    for cls in (".mem-stat", ".mem-item", ".mem-chip", ".mem-tag", ".mem-in"):
        assert cls in css, cls


def test_dialog_uses_no_icon_missing_from_the_icon_set():
    icons = _text("js", "icons.js")
    known = set(re.findall(r'"([a-z0-9-]+)":\[\[', icons))
    js = _text("js", "memory.js")
    used = set(re.findall(r"icon\('([a-z0-9-]+)'", js)) | set(re.findall(r"ic: '([a-z0-9-]+)'", js))
    used |= set(re.findall(r"tabBtn\('\w+', '[^']+', '([a-z0-9-]+)'", js))
    for pair in re.findall(r"icon\(open \? '([a-z0-9-]+)' : '([a-z0-9-]+)'", js):  # the Show more / less chevron
        used |= set(pair)
    assert used and used <= known, used - known
