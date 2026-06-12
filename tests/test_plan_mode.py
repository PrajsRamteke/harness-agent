import unittest
from unittest import mock

from jarvis import state
from jarvis.tools.plan import PLAN_MODE_ALLOWED, exit_plan_mode
from jarvis.tools.router import select_tools


class PlanModeRouterTests(unittest.TestCase):
    def setUp(self):
        state.plan_mode = False

    def tearDown(self):
        state.plan_mode = False

    def test_plan_mode_off_excludes_exit_plan_mode(self):
        msgs = [{"role": "user", "content": "fix the login bug"}]
        with mock.patch("jarvis.tools.router.skill_count", return_value=0):
            tools = select_tools(msgs)
        names = {t["name"] for t in tools}
        self.assertNotIn("exit_plan_mode", names)
        self.assertIn("edit_file", names)
        self.assertIn("run_bash", names)

    def test_plan_mode_on_strips_mutating_tools(self):
        msgs = [
            {"role": "user", "content": "fix the login bug, click the safari app, search the web"},
        ]
        state.plan_mode = True
        with mock.patch("jarvis.tools.router.skill_count", return_value=3):
            tools = select_tools(msgs)
        names = {t["name"] for t in tools}
        self.assertIn("exit_plan_mode", names)
        # read-only survivors
        self.assertIn("read_file", names)
        self.assertIn("search_code", names)
        self.assertIn("git_diff", names)
        self.assertIn("web_search", names)
        self.assertIn("skill_load", names)
        self.assertIn("ask_user_question", names)
        # mutating tools stripped
        self.assertNotIn("edit_file", names)
        self.assertNotIn("write_file", names)
        self.assertNotIn("multi_edit", names)
        self.assertNotIn("run_bash", names)
        self.assertNotIn("click_element", names)
        self.assertNotIn("memory_save", names)
        # everything exposed is allowlisted
        self.assertTrue(names <= PLAN_MODE_ALLOWED)


class ExitPlanModeTests(unittest.TestCase):
    def setUp(self):
        state.plan_mode = True

    def tearDown(self):
        state.plan_mode = False

    def _answer(self, option_id):
        return (
            '{"answers": [{"question_id": "plan_approval", '
            f'"selected_ids": ["{option_id}"], "labels": ["x"]}}]}}'
        )

    def test_approve_exits_plan_mode(self):
        with mock.patch(
            "jarvis.tools.ask_user.ask_user_question",
            return_value=self._answer("approve"),
        ):
            out = exit_plan_mode(plan="## Plan\n1. edit foo.py")
        self.assertFalse(state.plan_mode)
        self.assertIn("APPROVED", out)

    def test_reject_stays_in_plan_mode(self):
        with mock.patch(
            "jarvis.tools.ask_user.ask_user_question",
            return_value=self._answer("revise"),
        ):
            out = exit_plan_mode(plan="## Plan\n1. edit foo.py")
        self.assertTrue(state.plan_mode)
        self.assertIn("NOT approved", out)

    def test_empty_plan_rejected(self):
        out = exit_plan_mode(plan="")
        self.assertTrue(state.plan_mode)
        self.assertTrue(out.startswith("ERROR"))

    def test_noop_when_plan_mode_off(self):
        state.plan_mode = False
        out = exit_plan_mode(plan="## Plan")
        self.assertTrue(out.startswith("ERROR"))


class PlanModeSystemPromptTests(unittest.TestCase):
    def tearDown(self):
        state.plan_mode = False

    def test_plan_block_appended_only_when_active(self):
        from jarvis.repl.system import _plan_mode_block
        state.plan_mode = False
        self.assertEqual(_plan_mode_block(), "")
        state.plan_mode = True
        self.assertIn("PLAN MODE", _plan_mode_block())


if __name__ == "__main__":
    unittest.main()
