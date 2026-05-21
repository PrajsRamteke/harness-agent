import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from jarvis import prompt_attachments
from jarvis.constants import set_cwd


class PromptAttachmentTests(unittest.TestCase):
    def setUp(self):
        prompt_attachments.reset_registry()

    def test_classify_attachment(self):
        self.assertEqual(prompt_attachments.classify_attachment(Path("a.png")), "image")
        self.assertEqual(prompt_attachments.classify_attachment(Path("b.csv")), "csv")
        self.assertEqual(prompt_attachments.classify_attachment(Path("c.pdf")), "document")
        self.assertEqual(prompt_attachments.classify_attachment(Path("clip.mp4")), "video")
        self.assertEqual(prompt_attachments.classify_attachment(Path("song.mp3")), "audio")
        self.assertEqual(prompt_attachments.classify_attachment(Path("d.zip")), "file")
        self.assertEqual(prompt_attachments.classify_attachment(Path("readme.md")), "file")

    def test_is_attachable_skips_non_whitelisted(self):
        self.assertTrue(prompt_attachments.is_attachable(Path("photo.png")))
        self.assertTrue(prompt_attachments.is_attachable(Path("data.csv")))
        self.assertTrue(prompt_attachments.is_attachable(Path("clip.mov")))
        self.assertFalse(prompt_attachments.is_attachable(Path("main.py")))
        self.assertFalse(prompt_attachments.is_attachable(Path("readme.md")))
        self.assertFalse(prompt_attachments.is_attachable(Path("archive.zip")))

    def test_tokenize_dropped_paths(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            set_cwd(root)
            image = root / "photo.png"
            image.write_bytes(b"\x89PNG\r\n")
            csv = root / "data.csv"
            csv.write_text("a,b\n1,2", encoding="utf-8")

            text = f"Review {image} and {csv}"
            tokenized, _, _ = prompt_attachments.tokenize_dropped_paths(text)
            self.assertIn("[image 1]", tokenized)
            self.assertIn("[csv 1]", tokenized)
            self.assertNotIn(str(image), tokenized)

    def test_tokenize_quoted_paths_with_spaces(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            set_cwd(root)
            a = root / "Screenshot 2026-05-21 at 4.34.17PM.png"
            b = root / "Screenshot 2026-05-21 at 4.31.05PM.png"
            a.write_bytes(b"\x89PNG\r\n")
            b.write_bytes(b"\x89PNG\r\n")
            text = f"'{a}'\n'{b}'"
            tokenized, _, _ = prompt_attachments.tokenize_dropped_paths(text)
            self.assertEqual(tokenized, "[image 1]\n[image 2]")
            self.assertNotIn(str(a), tokenized)

    def test_tokenize_macos_screenshot_paths(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            set_cwd(root)
            actual = root / "Screenshot 2026-05-21 at 4.34.17\u202fPM.png"
            actual.write_bytes(b"\x89PNG\r\n")
            pasted = f"'{root / 'Screenshot 2026-05-21 at 4.34.17PM.png'}'"
            tokenized, _, _ = prompt_attachments.tokenize_dropped_paths(pasted)
            self.assertEqual(tokenized, "[image 1]")

    def test_tokenize_skips_non_media_files(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            set_cwd(root)
            code = root / "main.py"
            code.write_text("print('hi')", encoding="utf-8")
            tokenized, _, _ = prompt_attachments.tokenize_dropped_paths(f"edit {code}")
            self.assertEqual(tokenized, f"edit {code}")

    def test_tokenize_file_url(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            set_cwd(root)
            image = root / "shot.png"
            image.write_bytes(b"\x89PNG\r\n")
            url = f"file://{image}"
            tokenized, _, _ = prompt_attachments.tokenize_dropped_paths(url)
            self.assertEqual(tokenized, "[image 1]")

    def test_tokenize_places_cursor_after_chip(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            set_cwd(root)
            image = root / "photo.png"
            image.write_bytes(b"\x89PNG\r\n")
            text = str(image)
            tokenized, row, col = prompt_attachments.tokenize_dropped_paths(
                text,
                cursor_row=0,
                cursor_col=len(text),
            )
            self.assertEqual(tokenized, "[image 1]")
            self.assertEqual((row, col), (0, len("[image 1]")))

    def test_expand_attachment_tokens_paths_only(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            set_cwd(root)
            note = root / "note.txt"
            note.write_text("hello attachment", encoding="utf-8")

            reg = prompt_attachments.AttachmentRegistry()
            label = reg.register(note)
            text = f"Summarize {label}"
            expanded, attached = prompt_attachments.expand_attachment_tokens(text, reg)
            self.assertEqual(attached, [str(note)])
            self.assertIn(f"Summarize {note}", expanded)
            self.assertNotIn("[document 1]", expanded)
            self.assertNotIn("--- Dropped files ---", expanded)
            self.assertNotIn("hello attachment", expanded)

    def test_registry_snapshot_roundtrip(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            set_cwd(root)
            sample = root / "sheet.csv"
            sample.write_text("x,y", encoding="utf-8")
            reg = prompt_attachments.AttachmentRegistry()
            label = reg.register(sample, dropped_as=str(sample))
            snap = reg.snapshot()
            llm_snap = reg.snapshot_llm_paths()
            prompt_attachments.reset_registry()
            self.assertEqual(prompt_attachments.get_registry().by_label, {})
            reg.restore(snap)
            reg.llm_paths = dict(llm_snap)
            self.assertEqual(reg.path_for_label(label), sample.resolve())
            self.assertEqual(reg.llm_path_for_label(label), str(sample))

    def test_build_attachment_highlights(self):
        from jarvis.tui.prompt_highlight import build_attachment_highlights

        text = "Look at [image 1] and [csv 2]"
        highlights = build_attachment_highlights(text)
        styles = [span[2] for spans in highlights.values() for span in spans]
        self.assertIn("attachment_image", styles)
        self.assertIn("attachment_csv", styles)


if __name__ == "__main__":
    unittest.main()
