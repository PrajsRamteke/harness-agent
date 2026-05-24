import unittest
from unittest import mock

from jarvis.tui.app import PromptArea


class PromptAreaPasteTests(unittest.IsolatedAsyncioTestCase):
    async def test_on_paste_prevents_base_text_area_handler(self):
        area = PromptArea()
        area.read_only = False
        event = mock.Mock()
        event.text = "npm i axios"
        event.prevent_default = mock.Mock(return_value=event)
        end = mock.Mock(end_location=(0, 11))

        with mock.patch.object(area, "_replace_via_keyboard", return_value=end) as replace:
            with mock.patch.object(area, "move_cursor"):
                with mock.patch.object(area, "focus"):
                    await area._on_paste(event)

        event.prevent_default.assert_called_once()
        replace.assert_called_once_with("npm i axios", *area.selection)

    async def test_on_paste_skips_when_read_only(self):
        area = PromptArea()
        area.read_only = True
        event = mock.Mock()
        event.text = "npm i axios"
        event.prevent_default = mock.Mock(return_value=event)

        with mock.patch.object(area, "_replace_via_keyboard") as replace:
            await area._on_paste(event)

        event.prevent_default.assert_not_called()
        replace.assert_not_called()


if __name__ == "__main__":
    unittest.main()
