"""Generic text input modal — used by OAuth code paste and other TUI input flows."""

from __future__ import annotations

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import CenterMiddle, Vertical
from textual.widgets import Input, Static

from .modal_chrome import TUI_MODAL_CHROME_CSS, TuiModalScreen
from .mouse_toggle import enable_mouse, disable_mouse


class TextInputScreen(TuiModalScreen[str | None]):
    """Modal with a title, optional body text, and an Input widget.

    Dismisses with the entered text on Submit, or ``None`` on Escape.
    """

    DEFAULT_CSS = (
        TUI_MODAL_CHROME_CSS
        + """
    TextInputScreen #modal {
        width: 80%;
        max-width: 120;
        padding: 2 3;
    }
    TextInputScreen #modal_body {
        color: #c9d1d9;
        margin-bottom: 1;
    }
    """
    )

    BINDINGS = [
        Binding("escape", "cancel", "Cancel", show=True),
    ]

    def __init__(
        self,
        title: str,
        body: str = "",
        placeholder: str = "",
        password: bool = False,
    ) -> None:
        super().__init__()
        self._title = title
        self._body = body
        self._placeholder = placeholder
        self._password = password

    def compose(self) -> ComposeResult:
        with CenterMiddle():
            with Vertical(id="modal"):
                yield Static(self._title, id="modal_title")
                if self._body:
                    yield Static(self._body, id="modal_body")
                yield Input(
                    placeholder=self._placeholder,
                    password=self._password,
                    id="text_input",
                )
                yield Static(
                    "Enter to submit  •  Esc to cancel",
                    id="modal_hint",
                )

    def on_mount(self) -> None:
        enable_mouse()
        inp = self.query_one("#text_input", Input)
        inp.focus()

    def on_unmount(self) -> None:
        disable_mouse()

    def on_input_submitted(self, event: Input.Submitted) -> None:
        self.dismiss(event.value)

    def action_cancel(self) -> None:
        self.dismiss(None)
