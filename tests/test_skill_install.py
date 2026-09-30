"""Installing skills from links, archives and folders (no network)."""
import io
import json
import tarfile
import zipfile

import pytest

from jarvis.storage import skill_install as si
from jarvis.storage import skills as sk

SKILL_MD = "---\nname: {name}\ndescription: {desc}\n---\n\n# {name}\nDo the thing.\n"


def make_skill(root, name, desc="Does a thing when asked.", extra=None):
    folder = root / name
    folder.mkdir(parents=True)
    (folder / "SKILL.md").write_text(SKILL_MD.format(name=name, desc=desc))
    for rel, body in (extra or {}).items():
        f = folder / rel
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text(body)
    return folder


@pytest.fixture()
def repo(tmp_path):
    src = tmp_path / "src"
    make_skill(src / "skills", "pdf", extra={"scripts/run.py": "print('hi')"})
    make_skill(src / "skills", "docx")
    return src


# ── parsing ────────────────────────────────────────────────────────────────


@pytest.mark.parametrize("text,expect", [
    ("https://github.com/o/r", dict(kind="github", owner="o", repo="r", ref="HEAD", path="")),
    ("https://github.com/o/r.git", dict(kind="github", repo="r")),
    ("https://github.com/o/r/tree/main/skills/pdf", dict(ref="main", path="skills/pdf")),
    ("https://github.com/o/r/blob/main/skills/pdf/SKILL.md", dict(ref="main", path="skills/pdf")),
    ("https://raw.githubusercontent.com/o/r/main/skills/pdf/SKILL.md", dict(kind="github", ref="main", path="skills/pdf")),
    ("o/r", dict(kind="github", owner="o", repo="r")),
    ("o/r@pdf", dict(skill="pdf", path="")),
    ("o/r/skills/pdf", dict(path="skills/pdf")),
    ("https://skills.sh/o/r/pdf", dict(kind="github", skill="pdf")),
    ("npx skills add o/r --skill docx", dict(owner="o", skill="docx")),
    ("npx skills@latest add https://github.com/o/r", dict(repo="r")),
    ("https://example.com/a/SKILL.md", dict(kind="file")),
    ("https://example.com/a.zip", dict(kind="archive")),
    ("https://example.com/a.skill", dict(kind="archive")),
    ("https://example.com/a.tar.gz", dict(kind="archive")),
    ("https://gitlab.com/o/r", dict(kind="git")),
    ("git@github.com:o/r.git", dict(kind="git")),
])
def test_parse_source(text, expect):
    got = si.parse_source(text)
    for k, v in expect.items():
        assert got[k] == v, (k, got)


@pytest.mark.parametrize("text", ["", "hello there", "https://example.com/page", "https://github.com/o"])
def test_unreadable_sources_explain_themselves(text):
    with pytest.raises(si.SkillSourceError):
        si.parse_source(text)


def test_local_folder_source(tmp_path):
    assert si.parse_source(str(tmp_path))["kind"] == "local"
    with pytest.raises(si.SkillSourceError):
        si.parse_source(str(tmp_path / "missing") + "/")


# ── inspect / install (local folder) ───────────────────────────────────────


def test_inspect_lists_every_skill(ext_env, repo):
    res = si.inspect_source(str(repo))
    assert res["ok"]
    assert [s["name"] for s in res["skills"]] == ["docx", "pdf"]
    assert res["skills"][1]["folder"] == "skills/pdf" and res["skills"][1]["files"] == 2


def test_inspect_of_empty_folder(ext_env, tmp_path):
    (tmp_path / "e").mkdir()
    assert "No skills found" in si.inspect_source(str(tmp_path / "e"))["error"]


def test_install_single_skill_to_project(ext_env, repo):
    res = si.install_skills(str(repo / "skills" / "pdf"), scope="project")
    assert res["ok"], res
    dest = ext_env.proj / ".harness" / "skills" / "pdf"
    assert (dest / "SKILL.md").is_file() and (dest / "scripts" / "run.py").is_file()
    assert json.loads((dest / si.PROVENANCE_FILE).read_text())["source"]
    assert [s["name"] for s in sk.discover_skills(force=True)] == ["pdf"]


def test_multi_skill_source_asks_which(ext_env, repo):
    res = si.install_skills(str(repo), scope="project")
    assert res["needs_choice"] and {s["name"] for s in res["skills"]} == {"pdf", "docx"}
    assert not (ext_env.proj / ".harness").exists()


def test_pick_by_name_and_all(ext_env, repo):
    assert si.install_skills(str(repo), scope="project", names=["docx"])["installed"][0]["name"] == "docx"
    res = si.install_skills(str(repo), scope="project", install_all=True)
    assert [i["name"] for i in res["installed"]] == ["pdf"]  # docx already there
    assert res["skipped"][0]["name"] == "docx"


def test_unknown_skill_name_lists_what_exists(ext_env, repo):
    res = si.install_skills(str(repo), scope="project", names=["nope"])
    assert not res["ok"] and "pdf" in res["error"]


def test_overwrite_replaces(ext_env, repo):
    si.install_skills(str(repo / "skills" / "pdf"), scope="project")
    (repo / "skills" / "pdf" / "SKILL.md").write_text(SKILL_MD.format(name="pdf", desc="Brand new description."))
    assert not si.install_skills(str(repo / "skills" / "pdf"), scope="project")["ok"]
    assert si.install_skills(str(repo / "skills" / "pdf"), scope="project", overwrite=True)["ok"]
    assert "Brand new" in (ext_env.proj / ".harness/skills/pdf/SKILL.md").read_text()


def test_global_install_turns_global_scope_on(ext_env, repo):
    from jarvis import state

    res = si.install_skills(str(repo / "skills" / "docx"), scope="global")
    assert res["ok"] and "turned them on" in res["scope_note"]
    assert state.global_skills is True
    assert (ext_env.home / ".harness/skills/docx/SKILL.md").is_file()
    assert [s["name"] for s in sk.discover_skills(force=True)] == ["docx"]


def test_confirm_can_decline(ext_env, repo):
    asked = []
    res = si.install_skills(str(repo / "skills" / "pdf"), scope="project", confirm=lambda d: asked.append(d) or False)
    assert res["denied"] and "pdf" in asked[0]
    assert not (ext_env.proj / ".harness").exists()


def test_bad_header_is_repaired_on_install(ext_env, tmp_path):
    folder = tmp_path / "Weird Skill"
    folder.mkdir()
    (folder / "SKILL.md").write_text("---\nname: Weird_Skill Name\ndescription: " + "x" * 1500 + "\n---\nbody\n")
    res = si.install_skills(str(folder), scope="project")
    assert res["ok"], res
    (only,) = sk.discover_skills(force=True)
    assert only["name"] == "weird-skill-name" and len(only["description"]) <= 1024


def test_block_scalar_description_is_read(ext_env, tmp_path):
    folder = tmp_path / "s"
    folder.mkdir()
    (folder / "SKILL.md").write_text("---\nname: folded\ndescription: >\n  Use this\n  for things\n---\nbody\n")
    (info,) = si.inspect_source(str(folder))["skills"]
    assert info["usable"] and info["description"].startswith("Use this")


def test_missing_description_is_not_installable(ext_env, tmp_path):
    folder = tmp_path / "s"
    folder.mkdir()
    (folder / "SKILL.md").write_text("---\nname: nodesc\n---\nbody\n")
    res = si.install_skills(str(folder), scope="project")
    assert not res["ok"] and res["skipped"][0]["name"] == "nodesc"


# ── archives are untrusted ─────────────────────────────────────────────────


def make_tar(files, top="repo-main"):
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tf:
        for name, body in files.items():
            data = body.encode()
            info = tarfile.TarInfo(f"{top}/{name}" if top else name)
            info.size = len(data)
            tf.addfile(info, io.BytesIO(data))
    return buf.getvalue()


def test_github_source_downloads_a_tarball(ext_env, monkeypatch):
    seen = []
    tar = make_tar({"skills/pdf/SKILL.md": SKILL_MD.format(name="pdf", desc="PDFs."), "README.md": "hi"})
    monkeypatch.setattr(si, "_http_get", lambda url, **k: seen.append(url) or tar)
    res = si.install_skills("o/r@pdf", scope="project")
    assert res["ok"], res
    assert seen == ["https://codeload.github.com/o/r/tar.gz/HEAD"]
    assert (ext_env.proj / ".harness/skills/pdf/SKILL.md").is_file()


def test_tarball_path_traversal_and_links_are_dropped(ext_env, tmp_path):
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tf:
        for name, body in {"r/../../evil.txt": "x", "r/ok/SKILL.md": SKILL_MD.format(name="ok", desc="fine.")}.items():
            data = body.encode()
            info = tarfile.TarInfo(name)
            info.size = len(data)
            tf.addfile(info, io.BytesIO(data))
        link = tarfile.TarInfo("r/link")
        link.type = tarfile.SYMTYPE
        link.linkname = "/etc/passwd"
        tf.addfile(link)
    dest = tmp_path / "out"
    dest.mkdir()
    si._unpack_tar(buf.getvalue(), dest, "")
    assert (dest / "ok" / "SKILL.md").is_file()
    assert not (tmp_path / "evil.txt").exists() and not (dest / "link").exists()
    assert sorted(p.name for p in dest.iterdir()) == ["ok"]


def test_zip_archive_with_wrapper_folder(ext_env, monkeypatch):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("mything/SKILL.md", SKILL_MD.format(name="mything", desc="Zipped."))
        zf.writestr("mything/refs/a.md", "ref")
        zf.writestr("../escape.txt", "no")
    monkeypatch.setattr(si, "_http_get", lambda url, **k: buf.getvalue())
    res = si.install_skills("https://example.com/mything.skill", scope="project")
    assert res["ok"], res
    assert (ext_env.proj / ".harness/skills/mything/refs/a.md").is_file()
    assert not (ext_env.proj / "escape.txt").exists()


def test_raw_skill_md_link(ext_env, monkeypatch):
    monkeypatch.setattr(si, "_http_get", lambda url, **k: SKILL_MD.format(name="solo", desc="One file.").encode())
    res = si.install_skills("https://example.com/solo/SKILL.md", scope="project")
    assert res["ok"] and res["installed"][0]["name"] == "solo"


def test_tree_url_only_unpacks_that_folder(ext_env, monkeypatch):
    tar = make_tar({
        "skills/pdf/SKILL.md": SKILL_MD.format(name="pdf", desc="PDFs."),
        "skills/docx/SKILL.md": SKILL_MD.format(name="docx", desc="Docs."),
    })
    monkeypatch.setattr(si, "_http_get", lambda url, **k: tar)
    res = si.inspect_source("https://github.com/o/r/tree/dev/skills/pdf")
    assert [s["name"] for s in res["skills"]] == ["pdf"]


def test_404_is_explained(ext_env, monkeypatch):
    def boom(url, **k):
        raise si.SkillSourceError("Not found (404). Check the link")

    monkeypatch.setattr(si, "_http_get", boom)
    assert "404" in si.inspect_source("o/missing")["error"]


# ── remove / move / describe / update ──────────────────────────────────────


def test_remove_and_move(ext_env, repo):
    si.install_skills(str(repo / "skills" / "pdf"), scope="project")
    assert si.move_skill("pdf", "global")["ok"]
    assert (ext_env.home / ".harness/skills/pdf").is_dir() and not (ext_env.proj / ".harness/skills/pdf").exists()
    assert not si.move_skill("pdf", "global")["ok"]
    assert si.remove_skill("pdf")["ok"]
    assert not si.remove_skill("pdf")["ok"]


def test_other_tools_skills_are_never_touched(ext_env):
    other = ext_env.proj / ".claude" / "skills"
    make_skill(other, "theirs")
    assert sk.discover_skills(force=True)[0]["name"] == "theirs"
    res = si.remove_skill("theirs")
    assert not res["ok"] and "another tool" in res["error"]
    assert (other / "theirs" / "SKILL.md").exists()
    row = si.describe_installed()["skills"][0]
    assert row["managed"] is False


def test_describe_installed_marks_hidden_global_skills(ext_env, repo):
    from jarvis import state

    si.install_skills(str(repo / "skills" / "docx"), scope="global")
    state.global_skills = False
    data = si.describe_installed()
    (row,) = data["skills"]
    assert row["scope"] == "global" and row["active"] is False and data["hidden_global_count"] == 1
    assert row["managed"] and row["origin"]


def test_update_refetches_from_recorded_source(ext_env, repo):
    si.install_skills(str(repo / "skills" / "pdf"), scope="project")
    (repo / "skills" / "pdf" / "SKILL.md").write_text(SKILL_MD.format(name="pdf", desc="Updated text."))
    res = si.update_skill("pdf")
    assert res["ok"], res
    assert "Updated text" in (ext_env.proj / ".harness/skills/pdf/SKILL.md").read_text()
    assert not si.update_skill("ghost")["ok"]
