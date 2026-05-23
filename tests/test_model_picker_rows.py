"""Model picker always exposes Harness Agent rows."""
from jarvis.constants.providers import PROVIDER_HARNESS_AGENT
from jarvis.tui.model_modal import model_picker_rows, _BUILTIN_HARNESS_ROWS


def test_model_picker_rows_always_includes_harness_agent():
    rows = model_picker_rows()
    harness = [(src, mid) for src, mid, _ in rows if src == PROVIDER_HARNESS_AGENT]
    assert len(harness) >= len(_BUILTIN_HARNESS_ROWS)
    assert rows[0][0] == PROVIDER_HARNESS_AGENT
    assert rows[0][1] == "deepseek-v4-flash-free"
