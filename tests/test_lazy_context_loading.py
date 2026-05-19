import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from jarvis import state
from jarvis import project_context
from jarvis.repl import system
from jarvis.storage import skills


class LazyContextLoadingTests(unittest.TestCase):
    def setUp(self):
        self._state = {
            "project_context_file": state.project_context_file,
            "project_context_path": state.project_context_path,
            "project_context_content": state.project_context_content,
            "pinned_context": state.pinned_context,
            "global_skills": state.global_skills,
        }
        self._system_cache = {
            "_cached_body": system._cached_body,
            "_cached_mem_key": system._cached_mem_key,
            "_cached_sk_key": system._cached_sk_key,
            "_cached_skills_key": system._cached_skills_key,
            "_cached_pinned": system._cached_pinned,
            "_cached_cwd_branch": system._cached_cwd_branch,
            "_cached_ctx_key": system._cached_ctx_key,
        }

    def tearDown(self):
        for name, value in self._state.items():
            setattr(state, name, value)
        for name, value in self._system_cache.items():
            setattr(system, name, value)

    def _clear_system_cache(self):
        system._cached_body = ""
        system._cached_mem_key = ""
        system._cached_sk_key = ""
        system._cached_skills_key = ""
        system._cached_pinned = ""
        system._cached_cwd_branch = ""
        system._cached_ctx_key = ""

    def test_project_context_detects_path_without_reading_content(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "CLAUDE.md"
            path.write_text("SECRET PROJECT INSTRUCTIONS", encoding="utf-8")

            self.assertTrue(project_context.detect_project_context(temp_dir))
            self.assertEqual(state.project_context_file, "CLAUDE.md")
            self.assertEqual(state.project_context_path, str(path))
            self.assertEqual(state.project_context_content, "")

    def test_system_prompt_mentions_project_context_without_embedding_it(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            old_cwd = os.getcwd()
            try:
                os.chdir(temp_dir)
                context_path = Path(temp_dir) / "AGENT.md"
                context_path.write_text("DO NOT INLINE THIS UNIQUE CONTENT", encoding="utf-8")
                project_context.detect_project_context(temp_dir)
                state.pinned_context = ""
                self._clear_system_cache()

                with mock.patch.object(system, "build_base_system", return_value="BASE"), \
                        mock.patch.object(system, "_get_git_branch", return_value=None), \
                        mock.patch.object(system, "as_prompt_block", return_value=""), \
                        mock.patch.object(system, "lessons_prompt_block", return_value=""), \
                        mock.patch.object(system, "skills_prompt_block", return_value=""):
                    prompt = system._build_static_body()

                self.assertIn("PROJECT CONTEXT: AGENT.md exists", prompt)
                self.assertIn("read_file('AGENT.md')", prompt)
                self.assertNotIn("DO NOT INLINE THIS UNIQUE CONTENT", prompt)
            finally:
                os.chdir(old_cwd)

    def test_skill_prompt_block_includes_headers_not_full_body(self):
        state.global_skills = False
        with mock.patch.object(skills, "discover_skills", return_value=[
            {
                "name": "release-helper",
                "description": "Release process with complex steps",
                "scope": "project",
            },
            {
                "name": "debug-helper",
                "description": "Debug process for production issues",
                "scope": "project",
            },
        ]):
            block = skills.as_prompt_block()

        self.assertIn("SKILLS: 2 available", block)
        # Headers (name + description) ARE included for matching
        self.assertIn("release-helper", block)
        self.assertIn("Release process with complex steps", block)
        self.assertIn("debug-helper", block)
        # Instruction says to call skill_load for full body, not headers
        self.assertIn("skill_load", block)
        self.assertIn("HIGH PRIORITY", block)
        self.assertIn("call skill_load", block)
        self.assertIn("each match", block.lower())


if __name__ == "__main__":
    unittest.main()
