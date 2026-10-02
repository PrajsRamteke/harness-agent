"""Token in/out reach the sidebar, footer, /stats and the web while a session runs."""
from types import SimpleNamespace as NS

from jarvis.auth.opencode_client import _OpenCodeStream, _Usage
from jarvis.repl.stream import _usage_or_estimate


def _chunk(*, text=None, usage=None, choices=True):
    delta = NS(content=text, tool_calls=None, reasoning_content=None, reasoning=None)
    return NS(choices=[NS(delta=delta)] if choices else [], usage=usage)


def test_usage_on_empty_choices_chunk_is_kept():
    # OpenAI spec / OpenCode Zen: the last chunk carries usage and `choices: []`.
    usage = NS(prompt_tokens=503, completion_tokens=6, total_tokens=509)
    s = _OpenCodeStream([_chunk(text="hi"), _chunk(usage=usage, choices=False)])
    for _ in s.text_stream:
        pass
    final = s.get_final_message()
    assert (final.usage.input_tokens, final.usage.output_tokens) == (503, 6)
    assert _usage_or_estimate(final, []) == (503, 6)


def test_no_provider_usage_falls_back_to_estimate():
    final = NS(usage=_Usage(), content=[{"type": "text", "text": "x" * 40}])
    msgs = [{"role": "user", "content": "y" * 400}]
    assert _usage_or_estimate(final, msgs) == (100, 10)
