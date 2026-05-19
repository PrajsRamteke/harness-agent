import unittest

from jarvis.repl.tool_display import format_tool_output_preview


class ToolDisplayTests(unittest.TestCase):
    def test_short_output_not_truncated(self):
        body, truncated = format_tool_output_preview("hello")
        self.assertFalse(truncated)
        self.assertEqual(str(body), "hello")

    def test_long_output_shows_hint(self):
        text = "\n".join(f"line {i}" for i in range(100))
        body, truncated = format_tool_output_preview(text)
        self.assertTrue(truncated)
        self.assertIn("Ctrl+F", str(body))
        self.assertIn("line 0", str(body))


if __name__ == "__main__":
    unittest.main()
