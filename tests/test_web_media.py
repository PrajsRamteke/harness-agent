"""Web remote attachments: upload store, endpoints, prompts, transcripts, model content."""
from __future__ import annotations

import io
import json
import socket
import struct
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import zlib
from pathlib import Path

import pytest

from jarvis import media, state
from jarvis.web import state_api
from jarvis.web.bridge import WebBridge


def _png(w: int = 4, h: int = 3) -> bytes:
    """A real (tiny) PNG, no Pillow needed."""
    def chunk(tag: bytes, data: bytes) -> bytes:
        return struct.pack(">I", len(data)) + tag + data + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)

    raw = b"".join(b"\x00" + b"\xff\x00\x00" * w for _ in range(h))
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(raw)) + chunk(b"IEND", b""))


PDF = b"%PDF-1.4\n1 0 obj<<>>endobj\ntrailer<<>>\n%%EOF\n"


@pytest.fixture(autouse=True)
def uploads(tmp_path, monkeypatch):
    folder = tmp_path / "uploads"
    monkeypatch.setattr(media, "UPLOAD_DIR", folder)
    return folder


def _save(data: bytes, name: str, ctype: str = "") -> dict:
    return media.save_upload(io.BytesIO(data), len(data), name, ctype)


# ─── Store ──────────────────────────────────────────────────────────────


def test_upload_is_saved_with_metadata_and_public_record_hides_the_path(uploads):
    rec = _save(_png(40, 30), "shot.png", "image/png")
    assert rec["kind"] == "image" and rec["mime"] == "image/png"
    assert (rec["width"], rec["height"]) == (40, 30)
    assert rec["url"] == f"/api/media/{rec['id']}" and "path" not in rec
    full = media.get(rec["id"])
    assert full["path"] == uploads / rec["id"] / "shot.png" and full["path"].read_bytes() == _png(40, 30)
    assert full["sent"] is False


@pytest.mark.parametrize("name,data,code", [
    ("empty.png", b"", "empty"),
    ("fake.png", b"this is not a picture", "bad_image"),
    ("fake.pdf", b"hello", "bad_pdf"),
    ("blob.weird", bytes(range(256)) * 4, "unsupported"),
])
def test_bad_uploads_are_refused_and_leave_nothing_behind(uploads, name, data, code):
    with pytest.raises(media.UploadError) as err:
        _save(data, name)
    assert err.value.code == code and err.value.status in (400, 415)
    assert name.split(".")[0] in str(err.value) or code == "empty"
    assert not any(uploads.glob("*/*")) if uploads.exists() else True


def test_too_large_is_refused_before_reading(monkeypatch):
    monkeypatch.setattr(media, "MAX_FILE_BYTES", 10)
    stream = io.BytesIO(b"x" * 100)
    with pytest.raises(media.UploadError) as err:
        media.save_upload(stream, 100, "big.txt")
    assert err.value.status == 413 and "MB" in str(err.value)
    assert stream.tell() == 0  # nothing was read


def test_cut_off_upload_is_an_error_and_cleaned_up(uploads):
    with pytest.raises(media.UploadError) as err:
        media.save_upload(io.BytesIO(b"abc"), 10, "notes.txt")
    assert err.value.code == "interrupted"
    assert list(uploads.iterdir()) == []


def test_names_are_sanitised_and_types_sniffed(uploads):
    rec = _save(_png(), "../../etc/pass:wd.png")
    assert rec["name"] == "passwd.png"
    assert (uploads / rec["id"] / "passwd.png").is_file()
    # A pasted image with no extension is recognised from its bytes.
    assert _save(_png(), "clipboard")["name"] == "clipboard.png"
    # A .jpg that is really a PNG is served and sent as PNG.
    assert _save(_png(), "photo.jpg")["mime"] == "image/png"
    # Unknown extensions are fine when the content is text (code, logs …).
    code = _save("print('héllo')\n".encode(), "script.py")
    assert code["kind"] == "text"
    # A file literally named like the metadata file doesn't clash with it.
    odd = _save(b'{"a": 1}', "meta.json")
    assert media.get(odd["id"])["path"].read_bytes() == b'{"a": 1}'


def test_remove_only_deletes_unsent_uploads(uploads):
    a, b = _save(_png(), "a.png"), _save(_png(), "b.png")
    media.mark_sent([media.get(b["id"])])
    assert media.remove(a["id"]) is True and media.get(a["id"]) is None
    assert media.remove(b["id"]) is False and media.get(b["id"]) is not None
    assert media.remove("../../etc") is False


def test_resolve_checks_ids(monkeypatch):
    a = _save(_png(), "a.png")
    assert [m["id"] for m in media.resolve([a["id"], {"id": a["id"]}])] == [a["id"]]
    with pytest.raises(media.UploadError) as gone:
        media.resolve(["0123456789abcdef"])
    assert gone.value.status == 410
    with pytest.raises(media.UploadError) as many:
        media.resolve([a["id"]] * (media.MAX_FILES + 1))
    assert many.value.code == "too_many"
    with pytest.raises(media.UploadError):
        media.resolve("not-a-list")


def test_prune_keeps_recent_and_sent_files(uploads):
    fresh, old_unsent, old_sent, ancient = (_save(_png(), f"{n}.png") for n in "abcd")
    now = time.time()
    for rec, created, sent_at in ((old_unsent, now - 2 * 86400, None),
                                  (old_sent, now - 2 * 86400, now - 86400),
                                  (ancient, now - 90 * 86400, now - 60 * 86400)):
        meta = media.get(rec["id"])
        meta["created"] = created
        if sent_at:
            meta["sent"], meta["sent_at"] = True, sent_at
        media._write_meta(uploads / rec["id"], meta)
    assert media.prune(now) == 2
    assert media.get(fresh["id"]) and media.get(old_sent["id"])
    assert media.get(old_unsent["id"]) is None and media.get(ancient["id"]) is None


# ─── What the model gets ────────────────────────────────────────────────


def test_user_content_has_note_with_paths_and_image_references():
    img, pdf = _save(_png(), "chart.png"), _save(PDF, "report.pdf")
    metas = media.resolve([img["id"], pdf["id"]])
    content = media.user_content("what changed?", metas)
    assert content[0] == {"type": "image", "source": {"type": media.REF_SOURCE, "id": img["id"]}}
    text = content[-1]["text"]
    assert text.startswith("what changed?\n\n" + media.NOTE_HEAD)
    assert str(metas[1]["path"]) in text and "read_document" in text
    # Documents only → plain text content, no image blocks.
    assert isinstance(media.user_content("", [metas[1]]), str)


def test_materialize_turns_references_into_images_without_touching_history():
    img = _save(_png(), "chart.png")
    content = media.user_content("look", media.resolve([img["id"]]))
    history = [{"role": "user", "content": content}]
    sent = media.materialize_uploads(history, vision=True)
    block = sent[0]["content"][0]
    assert block["type"] == "image" and block["source"]["type"] == "base64"
    assert block["source"]["media_type"] == "image/png" and block["source"]["data"]
    assert history[0]["content"][0]["source"]["type"] == media.REF_SOURCE  # unchanged

    blind = media.materialize_uploads(history, vision=False)
    assert blind[0]["content"][0]["type"] == "text" and "can’t view images" in blind[0]["content"][0]["text"]


def test_materialize_keeps_only_the_newest_images_and_handles_missing_files():
    recs = [_save(_png(), f"{i}.png") for i in range(3)]
    history = [{"role": "user", "content": media.user_content(str(i), media.resolve([r["id"]]))}
               for i, r in enumerate(recs)]
    out = media.materialize_uploads(history, vision=True, keep=2)
    kinds = [m["content"][0]["type"] for m in out]
    assert kinds == ["text", "image", "image"]  # the oldest is not re-sent
    assert "not re-sent" in out[0]["content"][0]["text"]

    import shutil
    shutil.rmtree(media.get(recs[2]["id"])["path"].parent)
    out = media.materialize_uploads(history, vision=True)
    assert "no longer on the computer" in out[2]["content"][0]["text"]
    assert history is not out


def test_attachment_note_reads_back_for_transcripts():
    img, doc = _save(_png(), "photo (1).png"), _save(PDF, "spec.pdf")
    content = media.user_content("compare these", media.resolve([img["id"], doc["id"]]))
    shown, files = media.attachments_in(content)
    assert shown == "compare these"
    assert [f["name"] for f in files] == ["photo (1).png", "spec.pdf"]
    assert all("path" not in f for f in files)

    import shutil
    shutil.rmtree(media.get(doc["id"])["path"].parent)
    _shown, files = media.attachments_in(content)
    assert files[1] == {"id": doc["id"], "name": "spec.pdf", "kind": "pdf", "missing": True}
    assert media.attachments_in("plain text") == ("plain text", [])


@pytest.mark.skipif(media._pil() is None and not media._have_sips(), reason="no image converter")
def test_big_and_rotated_photos_are_fitted_for_the_model(tmp_path):
    from PIL import Image  # the test builds its photo with Pillow

    img = Image.new("RGB", (3200, 1600), (200, 30, 30))
    exif = Image.Exif()
    exif[0x0112] = 6  # camera held upright: rotate 90° clockwise to view
    buf = io.BytesIO()
    img.save(buf, "JPEG", exif=exif.tobytes())
    rec = _save(buf.getvalue(), "IMG_0001.jpg")
    assert (rec["width"], rec["height"]) == (1600, 3200)  # as displayed
    path, mime = media.model_image(media.get(rec["id"]))
    assert mime == "image/jpeg" and path.name == ".model.jpg"
    with Image.open(path) as fitted:
        assert fitted.size == (784, 1568)  # upright and ≤ MODEL_EDGE
        assert fitted.getexif().get(0x0112) in (None, 1)
    # Cached: the second call doesn't convert again.
    assert media.model_image(media.get(rec["id"]))[0] == path


def test_queue_labels_mention_files():
    rec = _save(_png(), "photo.png")
    assert media.queue_label(("hi", ({}, {}), [rec["id"]])) == "hi · 📎 photo.png"
    assert media.queue_label(("", ({}, {}), [rec["id"]])) == "📎 photo.png"
    assert media.queue_label(("plain", ({}, {}))) == "plain"
    assert media.queue_label("old style") == "old style"


def test_tool_screenshots_get_a_servable_id(tmp_path):
    shot = tmp_path / "shot.png"
    shot.write_bytes(_png(8, 6))
    images = media.images_in_output(f"captured\n[[jarvis:image {shot}]]")
    assert len(images) == 1 and images[0]["id"].startswith("s") and images[0]["width"] == 8
    path, mime, _name = media.media_file(images[0]["id"])
    assert path == shot and mime == "image/png"
    assert media.images_in_output("no images here") == []

    from jarvis.web.console_mux import tool_row_fields

    fields = tool_row_fields("screenshot", {"path": str(shot)}, f"ok\n[[jarvis:image {shot}]]")
    assert fields["images"][0]["id"] == images[0]["id"]


# ─── Transcript snapshot ────────────────────────────────────────────────


def test_snapshot_shows_files_not_paths(monkeypatch):
    rec = _save(_png(), "screen.png")
    monkeypatch.setattr(state, "messages", [
        {"role": "user", "content": media.user_content("why is this red?", media.resolve([rec["id"]]))},
        {"role": "user", "content": media.user_content("", media.resolve([rec["id"]]))},
        {"role": "assistant", "content": [{"type": "text", "text": "A CSS bug."}]},
    ])
    msgs = state_api.snapshot_messages()
    assert msgs[0]["role"] == "you" and msgs[0]["text"] == "why is this red?"
    assert msgs[0]["attachments"][0]["name"] == "screen.png"
    assert msgs[1]["text"] == "" and msgs[1]["attachments"]  # files only
    assert "uploads" not in json.dumps(msgs)
    snap = state_api.snapshot_from_state()
    assert snap["upload_limits"] == {"max_mb": media.MAX_FILE_MB, "max_files": media.MAX_FILES}


def test_snapshot_tool_rows_carry_their_screenshots(monkeypatch, tmp_path):
    shot = tmp_path / "s.png"
    shot.write_bytes(_png())
    monkeypatch.setattr(state, "messages", [
        {"role": "user", "content": "check the page"},
        {"role": "assistant", "content": [{"type": "tool_use", "id": "t1", "name": "screenshot", "input": {}}]},
        {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "t1", "content": "ok"}]},
    ])
    from collections import deque

    monkeypatch.setattr(state, "tool_output_history", deque(
        [{"id": "t1", "name": "screenshot", "content": f"ok\n[[jarvis:image {shot}]]"}]))
    tool = [m for m in state_api.snapshot_messages() if m["role"] == "tool"][0]
    assert tool["images"][0]["name"] == "s.png"


def test_queue_field_labels_attachment_only_prompts(monkeypatch):
    rec = _save(_png(), "a.png")
    monkeypatch.setattr(state, "prompt_queue", [("", ({}, {}), [rec["id"]])])
    assert state_api.state_fields()["queue"] == ["📎 a.png"]


# ─── HTTP endpoints ─────────────────────────────────────────────────────


@pytest.fixture
def server():
    from jarvis.web.server import start_web_server, stop_web_server

    bridge = WebBridge()
    bridge.token = "tok"
    submitted: list[tuple[str, list[str]]] = []
    bridge.set_handlers(on_submit=lambda text, files: submitted.append((text, files)))
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    srv, _urls, port = start_web_server(bridge=bridge, app=None, port=port, host="127.0.0.1")
    yield f"http://127.0.0.1:{port}", submitted
    stop_web_server(srv, bridge)


def _req(url: str, *, data: bytes | None = None, headers: dict | None = None, method: str | None = None):
    req = urllib.request.Request(url, data=data, headers=headers or {}, method=method)
    try:
        with urllib.request.urlopen(req, timeout=10) as res:
            return res.status, dict(res.headers), res.read()
    except urllib.error.HTTPError as e:
        return e.code, dict(e.headers), e.read()


def _upload(base: str, data: bytes, name: str, token: str = "tok", ctype: str = "application/octet-stream"):
    return _req(f"{base}/api/upload", data=data, method="POST", headers={
        "Authorization": f"Bearer {token}", "X-File-Name": urllib.parse.quote(name), "Content-Type": ctype,
    })


def _post_json(base: str, path: str, payload: dict):
    status, _h, body = _req(f"{base}{path}", data=json.dumps(payload).encode(), method="POST", headers={
        "Authorization": "Bearer tok", "Content-Type": "application/json",
    })
    return status, json.loads(body or b"{}")


def test_upload_endpoint_round_trip(server):
    base, _ = server
    status, _h, body = _upload(base, _png(10, 10), "Bildschirmfoto 1.png", ctype="image/png")
    rec = json.loads(body)
    assert status == 200 and rec["ok"] and rec["file"]["name"] == "Bildschirmfoto 1.png"

    status, headers, data = _req(f"{base}{rec['file']['url']}?token=tok")
    assert status == 200 and data == _png(10, 10)
    assert headers["Content-Type"] == "image/png" and headers["X-Content-Type-Options"] == "nosniff"
    assert "sandbox" in headers["Content-Security-Policy"]
    assert headers["Accept-Ranges"] == "bytes"

    status, headers, _ = _req(f"{base}{rec['file']['url']}?token=tok&dl=1")
    assert headers["Content-Disposition"].startswith("attachment;")
    assert "filename*=UTF-8''Bildschirmfoto%201.png" in headers["Content-Disposition"]


def test_upload_errors_come_back_as_readable_json(server, monkeypatch):
    base, _ = server
    status, _h, body = _upload(base, b"not an image", "pic.png")
    assert status == 415 and json.loads(body)["code"] == "bad_image"
    assert "pic.png" in json.loads(body)["error"]

    monkeypatch.setattr(media, "MAX_FILE_BYTES", 16)
    status, headers, body = _upload(base, b"x" * 64, "big.txt")
    assert status == 413 and json.loads(body)["code"] == "too_large"
    assert headers.get("Connection") == "close"

    status, _h, body = _upload(base, _png(), "a.png", token="wrong")
    assert status == 401


def test_media_ranges_and_unknown_ids(server):
    base, _ = server
    rec = json.loads(_upload(base, b"0123456789" * 10, "clip.mp4")[2])["file"]
    url = f"{base}{rec['url']}?token=tok"
    status, headers, data = _req(url, headers={"Range": "bytes=10-19"})
    assert status == 206 and data == b"0123456789" and headers["Content-Range"] == "bytes 10-19/100"
    status, headers, data = _req(url, headers={"Range": "bytes=-5"})
    assert status == 206 and data == b"56789"
    status, headers, _ = _req(url, headers={"Range": "bytes=500-"})
    assert status == 416 and headers["Content-Range"] == "bytes */100"

    assert _req(f"{base}/api/media/0123456789abcdef?token=tok")[0] == 404
    assert _req(f"{base}/api/media/..%2F..%2Fetc%2Fpasswd?token=tok")[0] == 404
    assert _req(f"{base}{rec['url']}")[0] == 401  # no token


def test_text_files_are_served_as_plain_text(server):
    base, _ = server
    rec = json.loads(_upload(base, b"<script>alert(1)</script>", "page.html")[2])["file"]
    _status, headers, _ = _req(f"{base}{rec['url']}?token=tok")
    assert headers["Content-Type"] == "text/plain; charset=utf-8"


def test_prompt_with_attachments(server):
    base, submitted = server
    rec = json.loads(_upload(base, _png(), "a.png")[2])["file"]

    status, body = _post_json(base, "/api/prompt", {"text": "what's this?", "attachments": [rec["id"]]})
    assert status == 200 and body["ok"]
    assert submitted[-1] == ("what's this?", [rec["id"]])
    assert media.get(rec["id"])["sent"] is True  # kept: it belongs to the chat now
    assert _post_json(base, "/api/upload/remove", {"id": rec["id"]})[1] == {"ok": False}

    status, body = _post_json(base, "/api/prompt", {"text": "", "attachments": [rec["id"]]})
    assert status == 200 and submitted[-1] == ("", [rec["id"]])

    status, body = _post_json(base, "/api/prompt", {"text": "/model", "attachments": [rec["id"]]})
    assert status == 400 and body["code"] == "command_with_files"

    status, body = _post_json(base, "/api/prompt", {"text": "hi", "attachments": ["0123456789abcdef"]})
    assert status == 410 and "no longer" in body["error"]
    assert len(submitted) == 2

    status, _ = _post_json(base, "/api/prompt", {"text": "", "attachments": []})
    assert status == 400


def test_removing_an_unsent_upload(server):
    base, _ = server
    rec = json.loads(_upload(base, _png(), "a.png")[2])["file"]
    assert _post_json(base, "/api/upload/remove", {"id": rec["id"]})[1] == {"ok": True}
    assert _req(f"{base}{rec['url']}?token=tok")[0] == 404


def test_bridge_passes_files_through():
    bridge = WebBridge()
    got = []
    bridge.set_handlers(on_submit=lambda text, files: got.append((text, files)))
    assert bridge.submit_prompt("", ["abc"]) is True
    assert bridge.submit_prompt("  ", []) is False
    assert bridge.submit_prompt("hi") is True
    assert got == [("", ["abc"]), ("hi", [])]


# ─── Page assets ────────────────────────────────────────────────────────


def test_page_links_the_media_styles_and_markup():
    static = Path(media.__file__).with_name("web") / "static"
    html = (static / "index.html").read_text()
    assert '/static/css/media.css' in html
    for ident in ("att-note", "att-tray", "att-input", "qc-attach", "drop-zone", "viewer", "viewer-stage", "viewer-close"):
        assert f'id="{ident}"' in html
    assert (static / "css" / "media.css").is_file()


# ─── TUI: web prompts with files start / queue turns ────────────────────


def test_tui_turns_carry_web_attachments(monkeypatch, tmp_path):
    import asyncio

    monkeypatch.setenv("HARNESS_SKIP_UPDATE", "1")
    import jarvis.mcp.registry as mcp_registry
    import jarvis.storage.sessions as sessions
    import jarvis.storage.settings as settings
    import jarvis.tui.prompt_history as prompt_history
    import jarvis.updater as updater

    monkeypatch.setattr(updater, "maybe_update_and_reexec", lambda: None)
    monkeypatch.setattr(mcp_registry, "auto_connect_servers", lambda console_print=None, **kw: None, raising=False)
    monkeypatch.setattr(sessions, "db_init", lambda: None)
    monkeypatch.setattr(sessions, "db_create_session", lambda model: None)
    monkeypatch.setattr(prompt_history.PromptHistory, "_save", lambda self: None)
    monkeypatch.setattr(settings, "_singleton", settings.Settings(tmp_path / "settings.json"))
    monkeypatch.setattr(settings.Settings, "save", lambda self: None)
    monkeypatch.setattr(state, "web_enabled", False)
    monkeypatch.setattr(state, "prompt_queue", [])

    from jarvis.tui.app import JarvisTUI

    monkeypatch.setattr(JarvisTUI, "_warm_model_catalogs_background", lambda self: None)
    runs: list[tuple] = []
    monkeypatch.setattr(JarvisTUI, "_run_turn", lambda self, inp, turn_id=None, attachments=None:
                        runs.append((inp, attachments)))
    rec = _save(_png(), "phone.png")

    async def run() -> None:
        app = JarvisTUI()
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause(0.2)
            bridge = WebBridge()
            sub = bridge.subscribe()
            app._web_bridge = bridge

            app._handle_web_submit("what is this?", [rec["id"]])
            await pilot.pause(0.1)
            assert runs[-1] == ("what is this?", [rec["id"]])
            events = []
            while not sub.empty():
                events.append(json.loads(sub.get_nowait()))
            you = [e for e in events if e["type"] == "message" and e["data"]["role"] == "you"][-1]
            assert you["data"]["attachments"][0]["name"] == "phone.png"

            # Busy: queued with its files, then started with them.
            app._busy = True
            app._handle_web_submit("", [rec["id"]])
            assert state.prompt_queue[-1][2] == [rec["id"]]
            assert "📎 phone.png" in app._stash_preview(state.prompt_queue[-1])
            assert app._pop_queued_for_edit() is False  # web files can't move into the terminal box
            app._turn_t0 = time.monotonic()
            app._turn_done(app._turn_id)
            await pilot.pause(0.1)
            assert runs[-1] == ("", [rec["id"]]) and state.prompt_queue == []
            app._busy = False
            app._web_bridge = None

    asyncio.run(run())


def test_openai_style_providers_receive_the_image():
    """Chat-completions (Zen / Go / models.dev) and Codex convert the materialised block."""
    from jarvis.auth.codex_client import _anthropic_messages_to_responses_input
    from jarvis.auth.opencode_client import _anthropic_messages_to_openai

    rec = _save(_png(), "a.png")
    msgs = media.materialize_uploads(
        [{"role": "user", "content": media.user_content("look", media.resolve([rec["id"]]))}], vision=True)
    chat = _anthropic_messages_to_openai(msgs)[0]["content"]
    assert [p["type"] for p in chat] == ["text", "image_url"]
    assert chat[1]["image_url"]["url"].startswith("data:image/png;base64,")
    codex = _anthropic_messages_to_responses_input(msgs)[0]["content"]
    assert [p["type"] for p in codex] == ["input_text", "input_image"]


# ─── Model list: which models can see images ────────────────────────────


def test_model_list_marks_models_that_see_images(monkeypatch):
    from jarvis.constants import providers
    from jarvis.web import pickers_api

    monkeypatch.setattr(pickers_api, "model_picker_rows", lambda: [
        ("openrouter", "vision/model", "sees pictures"),
        ("openrouter", "text/model", "words only"),
    ])
    monkeypatch.setattr(providers, "model_supports_images", lambda mid, provider=None: mid == "vision/model")
    rows = pickers_api.list_models()["models"]
    assert [(r["model_id"], r["images"]) for r in rows] == [("vision/model", True), ("text/model", False)]
    # "vision" / "image" in the search lists only the models that can see images.
    for word in ("vision", "Images"):
        assert [r["model_id"] for r in pickers_api.list_models(query=word)["models"]] == ["vision/model"]
    assert [r["model_id"] for r in pickers_api.list_models(query="text")["models"]] == ["text/model"]


def test_state_says_whether_the_current_model_sees_images(monkeypatch):
    from jarvis.constants import providers

    monkeypatch.setattr(state, "MODEL", "some-model")
    monkeypatch.setattr(providers, "model_supports_images", lambda mid, provider=None: False)
    assert state_api.state_fields()["vision"] is False
    monkeypatch.setattr(providers, "model_supports_images", lambda mid, provider=None: True)
    assert state_api.state_fields()["vision"] is True
