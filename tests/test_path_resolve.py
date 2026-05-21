import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from jarvis.path_resolve import robust_resolve
from jarvis.constants import set_cwd


class PathResolveTests(unittest.TestCase):
    def test_loose_match_missing_space_before_pm(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            set_cwd(root)
            actual = root / "Screenshot 2026-05-21 at 4.34.17\u202fPM.png"
            actual.write_bytes(b"\x89PNG\r\n")
            pasted = str(root / "Screenshot 2026-05-21 at 4.34.17PM.png")
            resolved = robust_resolve(pasted, root)
            self.assertTrue(resolved.is_file())
            self.assertEqual(resolved.name, actual.name)


if __name__ == "__main__":
    unittest.main()
