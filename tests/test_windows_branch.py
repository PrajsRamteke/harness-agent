"""Tests for the windows branch — run on any OS (registry/prompt); full tool smoke needs Windows."""
import sys
import unittest


class TestWindowsBranch(unittest.TestCase):
    def test_system_prompt_is_windows(self):
        from jarvis.constants.system_prompt import build_base_system
        prompt = build_base_system()
        self.assertIn("Windows agent", prompt)
        self.assertIn("run_powershell", prompt)
        self.assertNotIn("applescript", prompt.lower())
        self.assertNotIn("macOS Keychain", prompt)

    def test_mac_tools_not_registered(self):
        from jarvis.tools import FUNC, TOOL_GROUPS
        win_names = {t["name"] for t in TOOL_GROUPS["windows"]}
        self.assertIn("run_powershell", win_names)
        self.assertIn("win_control", win_names)
        self.assertNotIn("applescript", win_names)
        self.assertNotIn("mac_control", win_names)
        for name in win_names:
            self.assertIn(name, FUNC, msg=f"missing handler {name}")
        self.assertNotIn("applescript", FUNC)
        self.assertNotIn("mac_control", FUNC)

    def test_router_includes_windows_group(self):
        from jarvis.tools.router import select_tools
        tools = select_tools([{"role": "user", "content": "launch notepad and read_ui"}])
        names = {t["name"] for t in tools}
        self.assertIn("launch_app", names)
        self.assertIn("read_ui", names)

    def test_ocr_schema_mentions_windows(self):
        from jarvis.tools.schemas_core import OCR_TOOLS
        desc = OCR_TOOLS[0]["description"]
        self.assertIn("Windows OCR", desc)
        self.assertNotIn("macOS Vision", desc)

    @unittest.skipUnless(sys.platform == "win32", "PowerShell tools require Windows")
    def test_powershell_echo(self):
        from jarvis.tools.windows.powershell import run_powershell
        out = run_powershell("Write-Output harness-smoke-ok")
        self.assertIn("harness-smoke-ok", out)
        self.assertFalse(out.startswith("ERROR"))

    @unittest.skipUnless(sys.platform == "win32", "UI tools require Windows")
    def test_check_permissions(self):
        from jarvis.tools.windows.ui import check_permissions
        out = check_permissions()
        self.assertIn("UI Automation", out)


if __name__ == "__main__":
    unittest.main()
