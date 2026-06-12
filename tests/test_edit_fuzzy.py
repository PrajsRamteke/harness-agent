"""edit_file / multi_edit whitespace-tolerant matching fallback."""

import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from jarvis.constants import set_cwd
from jarvis.tools.files import (
    _apply_text_edit,
    _whitespace_tolerant_spans,
    edit_file,
    multi_edit,
)


class ApplyTextEditTests(unittest.TestCase):
    def test_exact_match_still_first(self):
        new_txt, err, n, note = _apply_text_edit("a\nb\nc\n", "b", "B")
        self.assertIsNone(err)
        self.assertEqual(new_txt, "a\nB\nc\n")
        self.assertEqual(n, 1)
        self.assertIsNone(note)

    def test_empty_old_str_rejected(self):
        new_txt, err, n, note = _apply_text_edit("abc", "", "x")
        self.assertIsNone(new_txt)
        self.assertIn("empty", err)

    def test_identical_old_and_new_rejected(self):
        new_txt, err, n, note = _apply_text_edit("abc", "abc", "abc")
        self.assertIsNone(new_txt)
        self.assertIn("identical", err)

    def test_trailing_whitespace_mismatch_recovered(self):
        txt = "def foo():   \n    return 1\n"
        new_txt, err, n, note = _apply_text_edit(
            txt, "def foo():\n    return 1", "def foo():\n    return 2"
        )
        self.assertIsNone(err)
        self.assertEqual(new_txt, "def foo():\n    return 2\n")
        self.assertIsNotNone(note)

    def test_crlf_file_lf_old_str_recovered(self):
        txt = "line1\r\nline2\r\nline3\r\n"
        new_txt, err, n, note = _apply_text_edit(txt, "line1\nline2", "LINE1\nLINE2")
        self.assertIsNone(err)
        self.assertEqual(new_txt, "LINE1\nLINE2\r\nline3\r\n")
        self.assertIsNotNone(note)

    def test_nbsp_mismatch_recovered(self):
        txt = "total: 42 items\nnext\n"  # file contains a non-breaking space
        new_txt, err, n, note = _apply_text_edit(
            txt, "total: 42 items\nnext", "total: 43 items\nnext"
        )
        self.assertIsNone(err)
        self.assertEqual(new_txt, "total: 43 items\nnext\n")
        self.assertIsNotNone(note)

    def test_ambiguous_fuzzy_match_errors(self):
        txt = "x  \ny\nx\t\ny\n"
        new_txt, err, n, note = _apply_text_edit(txt, "x\ny", "z\nw")
        self.assertIsNone(new_txt)
        self.assertIn("2 locations", err)

    def test_ambiguous_fuzzy_with_replace_all(self):
        txt = "x  \ny\nx\t\ny\n"
        new_txt, err, n, note = _apply_text_edit(txt, "x\ny", "z\nw", replace_all=True)
        self.assertIsNone(err)
        self.assertEqual(new_txt, "z\nw\nz\nw\n")
        self.assertEqual(n, 2)

    def test_not_found_error_is_actionable(self):
        new_txt, err, n, note = _apply_text_edit("hello\n", "goodbye", "x")
        self.assertIsNone(new_txt)
        self.assertIn("re-read the file", err)

    def test_fuzzy_does_not_eat_trailing_newline(self):
        txt = "alpha   \nbeta\n"
        new_txt, err, n, note = _apply_text_edit(txt, "alpha\nbeta", "ALPHA\nbeta")
        self.assertIsNone(err)
        self.assertEqual(new_txt, "ALPHA\nbeta\n")

    def test_leading_indent_difference_is_not_matched(self):
        """Leading whitespace is meaningful — fuzzy match must not ignore it."""
        txt = "    indented\n    next\n"
        new_txt, err, n, note = _apply_text_edit(txt, "indented\nnext", "x\ny")
        self.assertIsNone(new_txt)
        self.assertIsNotNone(err)


class WhitespaceTolerantSpanTests(unittest.TestCase):
    def test_span_maps_back_to_exact_file_text(self):
        txt = "aa\nbb  \ncc\n"
        spans = _whitespace_tolerant_spans(txt, "bb")
        self.assertEqual(len(spans), 1)
        start, end = spans[0]
        self.assertEqual(txt[start:end], "bb  ")

    def test_empty_old_str_no_spans(self):
        self.assertEqual(_whitespace_tolerant_spans("abc\n", ""), [])


class EditFileIntegrationTests(unittest.TestCase):
    def test_edit_file_reports_fuzzy_note(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            set_cwd(root)
            f = root / "sample.py"
            f.write_text("def foo():   \n    return 1\n")
            out = edit_file(str(f), "def foo():\n    return 1", "def foo():\n    return 2")
            self.assertIn("EDITED", out)
            self.assertIn("whitespace-tolerant", out)
            self.assertEqual(f.read_text(), "def foo():\n    return 2\n")

    def test_multi_edit_mixes_exact_and_fuzzy(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            set_cwd(root)
            f = root / "sample.txt"
            f.write_text("alpha   \nbeta\ngamma\n")
            out = multi_edit(
                edits=[
                    # fuzzy: trailing whitespace after "alpha" in the file
                    {"path": str(f), "old_str": "alpha\nbeta", "new_str": "ALPHA\nbeta"},
                    # exact
                    {"path": str(f), "old_str": "gamma", "new_str": "GAMMA"},
                ]
            )
            self.assertIn("2 succeeded, 0 failed", out)
            self.assertIn("whitespace-tolerant", out)
            self.assertEqual(f.read_text(), "ALPHA\nbeta\nGAMMA\n")


if __name__ == "__main__":
    unittest.main()
