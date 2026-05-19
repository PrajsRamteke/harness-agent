import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from jarvis import prompt_refs
from jarvis.constants import set_cwd


class PromptRefsTests(unittest.TestCase):
    def test_extract_file_refs(self):
        text = "Fix @README.md and @jarvis/tui/app.py please"
        self.assertEqual(
            prompt_refs.extract_file_refs(text),
            ["README.md", "jarvis/tui/app.py"],
        )

    def test_extract_quoted_path(self):
        self.assertEqual(
            prompt_refs.extract_file_refs('Look at @"path with spaces.txt"'),
            ["path with spaces.txt"],
        )

    def test_expand_file_refs_inlines_content(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            set_cwd(root)
            sample = root / "note.txt"
            sample.write_text("hello world", encoding="utf-8")
            expanded, attached = prompt_refs.expand_file_refs("Summarize @note.txt")
            self.assertIn("Summarize @note.txt", expanded)
            self.assertIn("--- Attached files ---", expanded)
            self.assertIn('<file path="note.txt">', expanded)
            self.assertIn("hello world", expanded)
            self.assertEqual(attached, ["note.txt"])

    def test_active_file_ref_at_cursor(self):
        text = "Fix @read and @app.py please"
        # cursor after "read" in first mention
        active = prompt_refs.active_file_ref_at_cursor(text, 0, 9)
        self.assertEqual(active, (0, 4, "read"))

    def test_replace_file_ref_at_cursor(self):
        text = "See @rea please"
        new_text, (row, col) = prompt_refs.replace_file_ref_at_cursor(
            text, 0, 8, "README.md"
        )
        self.assertEqual(new_text, "See @README.md  please")
        self.assertEqual(row, 0)
        self.assertEqual(col, len("See @README.md "))

        new_text, (row, col) = prompt_refs.replace_file_ref_at_cursor(
            "Open @", 0, 6, "pkg/"
        )
        self.assertEqual(new_text, "Open @pkg/")
        self.assertEqual(col, len("Open @pkg/"))

        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            set_cwd(root)
            (root / "alpha.py").write_text("x", encoding="utf-8")
            (root / ".env").write_text("secret", encoding="utf-8")
            (root / ".secret").mkdir()
            (root / ".secret" / "hide.py").write_text("z", encoding="utf-8")
            sub = root / "pkg"
            sub.mkdir()
            (sub / "beta.py").write_text("y", encoding="utf-8")

            root_hits = prompt_refs.search_project_files("")
            self.assertIn("alpha.py", root_hits)
            self.assertIn("pkg/", root_hits)
            self.assertNotIn(".env", root_hits)
            self.assertFalse(any(h.startswith(".") for h in root_hits))
            self.assertNotIn("pkg/beta.py", root_hits)

            hits = prompt_refs.search_project_files("beta")
            self.assertIn("pkg/beta.py", hits)
            self.assertFalse(any(".secret" in h for h in hits))

    def test_build_file_ref_highlights(self):
        from jarvis.tui.prompt_highlight import build_file_ref_highlights

        text = "Fix @README.md and @jarvis/tui/app.py please"
        highlights = build_file_ref_highlights(text, cursor_row=0, cursor_col=len(text))
        self.assertIn("file_ref", [span[2] for spans in highlights.values() for span in spans])

        partial = build_file_ref_highlights("See @rea", cursor_row=0, cursor_col=7)
        active = partial[0]
        self.assertTrue(any(span[2] == "file_ref_active" for span in active))


if __name__ == "__main__":
    unittest.main()
