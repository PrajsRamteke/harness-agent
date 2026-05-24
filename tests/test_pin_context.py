"""Pinned context storage and slash-command behavior."""
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from jarvis import state
from jarvis.commands.context import handle_context
from jarvis.repl import system
from jarvis.storage import pin as pin_store
from jarvis.storage import prefs


class PinContextTests(unittest.TestCase):
    def setUp(self):
        self._orig_pinned = state.pinned_context
        self._orig_pin_enabled = state.pin_enabled
        self._orig_pin_file = prefs.PIN_FILE
        self._tmpdir = tempfile.TemporaryDirectory()
        prefs.PIN_FILE = Path(self._tmpdir.name) / "pinned.txt"
        state.pinned_context = ""
        state.pin_enabled = True
        system.invalidate_system_cache()

    def tearDown(self):
        state.pinned_context = self._orig_pinned
        state.pin_enabled = self._orig_pin_enabled
        prefs.PIN_FILE = self._orig_pin_file
        system.invalidate_system_cache()
        self._tmpdir.cleanup()

    def test_append_pin_accumulates_and_persists(self):
        lines, chars = pin_store.append_pin("always be concise")
        self.assertEqual((lines, chars), (1, 17))
        pin_store.append_pin("never run git commit")
        self.assertEqual(pin_store.pin_text(), "always be concise\nnever run git commit")
        self.assertTrue(prefs.PIN_FILE.exists())
        self.assertIn("never run git commit", prefs.PIN_FILE.read_text())

    def test_clear_pin_wipes_state_and_file(self):
        pin_store.append_pin("temporary rule")
        pin_store.clear_pin()
        self.assertEqual(pin_store.pin_text(), "")
        self.assertEqual(prefs.PIN_FILE.read_text(), "")

    def test_handle_pin_without_args_is_handled(self):
        pin_store.append_pin("line one")
        with patch("jarvis.commands.context.render_pin_preview") as preview:
            handled, new_inp = handle_context("/pin", "")
        self.assertTrue(handled)
        self.assertIsNone(new_inp)
        preview.assert_called_once()

    def test_handle_pin_with_text_appends(self):
        handled, new_inp = handle_context("/pin", "use pytest")
        self.assertTrue(handled)
        self.assertIsNone(new_inp)
        self.assertIn("use pytest", pin_store.pin_text())

    def test_handle_unpin_clears(self):
        pin_store.append_pin("remove me")
        handled, new_inp = handle_context("/unpin", "")
        self.assertTrue(handled)
        self.assertIsNone(new_inp)
        self.assertEqual(pin_store.pin_text(), "")

    def test_preview_lines_numbered(self):
        pin_store.append_pin("alpha\nbeta")
        self.assertEqual(
            pin_store.preview_lines(),
            [(1, "alpha"), (2, "beta")],
        )

    def test_disable_preserves_text_and_stops_injection(self):
        pin_store.append_pin("keep me")
        pin_store.set_enabled(False)
        self.assertEqual(pin_store.pin_text(), "keep me")
        self.assertEqual(pin_store.injection_text(), "")
        self.assertEqual(pin_store.injection_cache_key(), "0|keep me")

    def test_enable_restores_injection(self):
        pin_store.append_pin("restore me")
        pin_store.set_enabled(False)
        pin_store.set_enabled(True)
        self.assertEqual(pin_store.injection_text(), "restore me")

    def test_handle_pin_off_keeps_text(self):
        pin_store.append_pin("saved rule")
        handled, new_inp = handle_context("/pin", "off")
        self.assertTrue(handled)
        self.assertIsNone(new_inp)
        self.assertFalse(pin_store.is_enabled())
        self.assertEqual(pin_store.pin_text(), "saved rule")

    def test_system_prompt_omits_pin_when_disabled(self):
        pin_store.append_pin("secret standing rule")
        pin_store.set_enabled(False)
        system.invalidate_system_cache()
        prompt = system.build_system()
        if isinstance(prompt, list):
            body = prompt[-1]["text"]
        else:
            body = prompt
        self.assertNotIn("secret standing rule", body)
        pin_store.set_enabled(True)
        system.invalidate_system_cache()
        prompt = system.build_system()
        if isinstance(prompt, list):
            body = prompt[-1]["text"]
        else:
            body = prompt
        self.assertIn("secret standing rule", body)


if __name__ == "__main__":
    unittest.main()
