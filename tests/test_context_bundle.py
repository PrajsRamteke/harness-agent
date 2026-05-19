"""Tests for budget-aware context bundles."""
import unittest

from jarvis.tools import context as ctx


class TestBundleBudget(unittest.TestCase):
    def test_allocate_prefers_root_targets(self):
        items = [
            ("a.py", "root_target"),
            ("b.py", "imported_by_a"),
            ("c.py", "sibling_of_a"),
        ]
        budgets = ctx._allocate_char_budget(items, 12_000, 20_000)
        self.assertGreater(budgets["a.py"], budgets["c.py"])

    def test_allocate_respects_total_cap(self):
        items = [(f"f{i}.py", "root_target") for i in range(10)]
        budgets = ctx._allocate_char_budget(items, 10_000, 20_000)
        self.assertLessEqual(sum(budgets.values()), 10_000)

    def test_normalize_mode_fallback(self):
        self.assertEqual(ctx._normalize_mode("FULL", "skeleton"), "full")
        self.assertEqual(ctx._normalize_mode("bogus", "skeleton"), "skeleton")

    def test_build_bundle_manifest_no_disk_read(self):
        items = [("jarvis/tools/context.py", "requested")]
        out = ctx._build_bundle(
            items,
            mode="manifest",
            max_chars=8_000,
            per_file_max=4_000,
            header_lines=["Test manifest"],
        )
        self.assertIn(ctx.BUNDLE_MARKER, out)
        self.assertIn("manifest=1", out)
        self.assertIn("disk_read≈0 chars", out)
        self.assertIn("jarvis/tools/context.py", out)
        self.assertNotIn("def resolve_context", out)

    def test_build_bundle_skeleton_skips_related_body(self):
        graph = {
            "root.py": {
                "symbols": ["main"],
                "types": [],
                "imports": ["other.py"],
            },
            "other.py": {
                "symbols": ["helper"],
                "types": ["Thing"],
                "imports": [],
            },
        }
        items = [("root.py", "root_target"), ("other.py", "imported_by_root")]
        out = ctx._build_bundle(
            items,
            mode="skeleton",
            max_chars=20_000,
            per_file_max=5_000,
            header_lines=["Task: test"],
            graph=graph,
        )
        self.assertIn("[skeleton]", out)
        self.assertIn("symbols: helper", out)
        self.assertIn("skeleton=1", out)


if __name__ == "__main__":
    unittest.main()
