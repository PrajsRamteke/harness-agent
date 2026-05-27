import unittest
from unittest import mock

from jarvis.constants.system_prompt import build_base_system
from jarvis.repl.system import build_system


class BrowserSystemPromptTests(unittest.TestCase):
    def test_base_prompt_mandates_browser_tools_for_web(self):
        prompt = build_base_system()
        self.assertIn("browser_* tools ONLY", prompt)
        self.assertIn("NEVER substitute Mac GUI tools", prompt)
        self.assertNotIn("AppleScript for: Messages, Mail, Safari", prompt)

    def test_runtime_prompt_includes_bridge_status(self):
        with mock.patch("jarvis.state.browser_bridge_enabled", True), \
                mock.patch("jarvis.browser_bridge.server.bridge_state") as bs:
            bs.return_value.connected = False
            bs.return_value.last_hello = {}
            out = build_system()
        text = out if isinstance(out, str) else out[-1]["text"]
        self.assertIn("BROWSER BRIDGE STATUS", text)
        self.assertIn("browser_* tools ONLY", text)


if __name__ == "__main__":
    unittest.main()
