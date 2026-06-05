"""OpenCode usage token extraction — null-safe for MiniMax and similar providers."""
import unittest
from types import SimpleNamespace

from jarvis.auth.opencode_client import _get_usage_value


class OpenCodeUsageTests(unittest.TestCase):
    def test_none_usage_object(self):
        self.assertEqual(_get_usage_value(None, "completion_tokens"), 0)

    def test_dict_missing_key(self):
        self.assertEqual(_get_usage_value({}, "completion_tokens"), 0)

    def test_dict_null_value(self):
        self.assertEqual(
            _get_usage_value({"prompt_tokens": 10, "completion_tokens": None}, "completion_tokens"),
            0,
        )

    def test_object_null_attribute(self):
        usage = SimpleNamespace(prompt_tokens=42, completion_tokens=None)
        self.assertEqual(_get_usage_value(usage, "completion_tokens"), 0)
        self.assertEqual(_get_usage_value(usage, "prompt_tokens"), 42)

    def test_object_missing_attribute(self):
        usage = SimpleNamespace(prompt_tokens=5)
        self.assertEqual(_get_usage_value(usage, "completion_tokens"), 0)


if __name__ == "__main__":
    unittest.main()
