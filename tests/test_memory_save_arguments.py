import tempfile
import unittest
from pathlib import Path

from jarvis.storage import memory as storage_memory
from jarvis.tools.memory import MEMORY_TOOLS, memory_save


class MemorySaveArgumentTests(unittest.TestCase):
    def test_memory_save_accepts_text_and_fact_alias(self):
        old_config_dir = storage_memory.CONFIG_DIR
        old_memory_file = storage_memory.MEMORY_FILE
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            storage_memory.CONFIG_DIR = temp_path
            storage_memory.MEMORY_FILE = temp_path / "memory.json"
            try:
                self.assertTrue(memory_save(text="prefers concise replies").startswith("saved #1:"))
                self.assertTrue(memory_save(fact="favorite editor: VS Code").startswith("saved #2:"))

                self.assertEqual(
                    [item["text"] for item in storage_memory.list_facts()],
                    ["prefers concise replies", "favorite editor: VS Code"],
                )
            finally:
                storage_memory.CONFIG_DIR = old_config_dir
                storage_memory.MEMORY_FILE = old_memory_file

    def test_memory_save_schema_allows_text_or_fact(self):
        schema = next(tool["input_schema"] for tool in MEMORY_TOOLS if tool["name"] == "memory_save")

        self.assertNotEqual(schema.get("required"), ["text"])
        self.assertIn({"required": ["text"]}, schema.get("anyOf", []))
        self.assertIn({"required": ["fact"]}, schema.get("anyOf", []))


if __name__ == "__main__":
    unittest.main()
