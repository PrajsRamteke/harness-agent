"""multi_edit outside-project guard — allow_outside_project top-level AND per edit.

Regression for the loop where the guard's error told the model to pass
``allow_outside_project=true``, the model attached it to each edit object
(mirroring edit_file), and the repair layer stripped it as an unknown field —
so the call failed identically forever.
"""

import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from jarvis import constants
from jarvis.constants import set_cwd
from jarvis.tools.files import multi_edit
from jarvis.utils.tool_repair import repair_tool_input


class MultiEditScopeTests(unittest.TestCase):
    def setUp(self):
        self._orig_cwd = constants.CWD
        self._project = TemporaryDirectory()
        self._outside = TemporaryDirectory()
        set_cwd(Path(self._project.name))
        self.outside_file = Path(self._outside.name) / "index.html"
        self.outside_file.write_text("<h1>OLD</h1>\n")

    def tearDown(self):
        set_cwd(self._orig_cwd)
        self._project.cleanup()
        self._outside.cleanup()

    def test_outside_path_refused_by_default(self):
        out = multi_edit(edits=[
            {"path": str(self.outside_file), "old_str": "OLD", "new_str": "NEW"},
        ])
        self.assertIn("refused outside-project path", out)
        self.assertIn("OLD", self.outside_file.read_text())

    def test_top_level_flag_allows_outside_edit(self):
        out = multi_edit(
            edits=[{"path": str(self.outside_file), "old_str": "OLD", "new_str": "NEW"}],
            allow_outside_project=True,
        )
        self.assertIn("1 succeeded, 0 failed", out)
        self.assertIn("NEW", self.outside_file.read_text())

    def test_per_edit_flag_allows_outside_edit(self):
        out = multi_edit(edits=[
            {
                "path": str(self.outside_file),
                "old_str": "OLD",
                "new_str": "NEW",
                "allow_outside_project": True,
            },
        ])
        self.assertIn("1 succeeded, 0 failed", out)
        self.assertIn("NEW", self.outside_file.read_text())

    def test_repair_layer_preserves_per_edit_flag(self):
        # the nested-item repair must not strip the flag (it is now in the
        # items schema) and should coerce a string "true" to a real bool
        raw = {
            "edits": [
                {
                    "path": "x.html",
                    "old_str": "a",
                    "new_str": "b",
                    "allow_outside_project": "true",
                }
            ]
        }
        fixed, log = repair_tool_input("multi_edit", raw)
        self.assertIs(fixed["edits"][0]["allow_outside_project"], True)


if __name__ == "__main__":
    unittest.main()
