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

    def test_mcp_tools_omitted_when_not_registered_in_func(self):
        msgs = [{"role": "user", "content": "what are my clickup tickets"}]
        stale_mcp = [{"name": "mcp__clickup__get_tasks", "description": "x", "input_schema": {}}]
        with mock.patch("jarvis.tools.router.skill_count", return_value=1), \
                mock.patch.dict("jarvis.tools.router.TOOL_GROUPS", {"mcp": stale_mcp}, clear=False), \
                mock.patch.dict("jarvis.tools.router.FUNC", {}, clear=False):
            tools = select_tools(msgs)
        names = {t["name"] for t in tools}
        self.assertNotIn("mcp__clickup__get_tasks", names)


if __name__ == "__main__":
    unittest.main()


class LeanRequestTests(unittest.TestCase):
    """What every request carries stays small: no schema or prompt text for
    things that aren't in play."""

    def setUp(self):
        from jarvis.tools import router

        router.reset_sticky_groups()
        self.addCleanup(router.reset_sticky_groups)

    def _props(self, tools, name):
        tool = next(t for t in tools if t["name"] == name)
        return tool["input_schema"]["properties"]

    def test_project_argument_only_with_linked_projects(self):
        from jarvis.tools import TOOL_GROUPS

        msgs = [{"role": "user", "content": "rename the helper"}]
        with mock.patch("jarvis.tools.router._has_linked_projects", return_value=False):
            plain = select_tools(msgs)
        with mock.patch("jarvis.tools.router._has_linked_projects", return_value=True):
            linked = select_tools(msgs)
        self.assertNotIn("project", self._props(plain, "search_code"))
        self.assertNotIn("project", self._props(plain, "run_bash"))
        self.assertIn("project", self._props(linked, "search_code"))
        # The shared schema is never edited in place.
        core = next(t for t in TOOL_GROUPS["core"] if t["name"] == "search_code")
        self.assertIn("project", core["input_schema"]["properties"])

    def test_groups_match_the_tools(self):
        from jarvis.tools import TOOL_NAME_TO_GROUP
        from jarvis.tools.router import select_groups

        msgs = [{"role": "user", "content": "click the Safari toolbar and read the latest news"}]
        groups = select_groups(msgs)
        self.assertEqual(groups[0], "core")
        self.assertTrue({"mac", "internet", "vision"} <= set(groups))
        used = {TOOL_NAME_TO_GROUP.get(t["name"]) for t in select_tools(msgs)}
        self.assertTrue({"mac", "internet"} <= used)

    def test_prompt_explains_only_offered_groups(self):
        from jarvis.constants.system_prompt import build_base_system

        lean = build_base_system(groups=("core",))
        self.assertNotIn("Mac GUI", lean)
        self.assertNotIn("verified_search", lean)
        self.assertNotIn("run_bg, then keep", lean)
        self.assertIn("IDENTITY", lean)
        mac = build_base_system(groups=("core", "mac", "internet"))
        self.assertIn("Mac GUI", mac)
        self.assertIn("verified_search", mac)
        # Callers that don't route tools get every section.
        self.assertGreater(len(build_base_system()), len(mac))
        self.assertLess(len(lean), 3600)

    def test_default_tool_schemas_stay_small(self):
        import json

        msgs = [{"role": "user", "content": "hi"}]
        with mock.patch("jarvis.tools.router._has_linked_projects", return_value=False), \
                mock.patch("jarvis.tools.router._coding_agent_active", return_value=True), \
                mock.patch("jarvis.tools.router.skill_count", return_value=3):
            tools = select_tools(msgs)
        size = sum(len(json.dumps(t)) for t in tools)
        self.assertLess(size, 14500, f"default tool schemas grew to {size} chars")
