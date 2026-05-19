import unittest
from unittest import mock

from jarvis.tools.router import select_tools


class ToolRouterTests(unittest.TestCase):
    def test_skills_tools_included_when_skills_exist_without_keyword(self):
        msgs = [{"role": "user", "content": "build a frontend dashboard component"}]
        with mock.patch("jarvis.tools.router.skill_count", return_value=3):
            tools = select_tools(msgs)
        names = {t["name"] for t in tools}
        self.assertIn("skill_load", names)
        self.assertIn("skill_list", names)

    def test_skills_tools_omitted_when_no_skills_discovered(self):
        msgs = [{"role": "user", "content": "build a frontend dashboard component"}]
        with mock.patch("jarvis.tools.router.skill_count", return_value=0):
            tools = select_tools(msgs)
        names = {t["name"] for t in tools}
        self.assertNotIn("skill_load", names)
        self.assertNotIn("skill_list", names)

    def test_skills_tools_stay_active_after_skill_load(self):
        msgs = [
            {"role": "user", "content": "build a dashboard"},
            {
                "role": "assistant",
                "content": [
                    {"type": "tool_use", "name": "skill_load", "input": {"name": "frontend-design"}},
                ],
            },
        ]
        with mock.patch("jarvis.tools.router.skill_count", return_value=0):
            tools = select_tools(msgs)
        names = {t["name"] for t in tools}
        self.assertIn("skill_load", names)


if __name__ == "__main__":
    unittest.main()
