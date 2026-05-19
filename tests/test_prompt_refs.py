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

        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            set_cwd(root)
            (root / "alpha.py").write_text("x", encoding="utf-8")
            sub = root / "pkg"
            sub.mkdir()
            (sub / "beta.py").write_text("y", encoding="utf-8")
            hits = prompt_refs.search_project_files("beta")
            self.assertIn("pkg/beta.py", hits)


if __name__ == "__main__":
    unittest.main()
