"""verified_search must not print progress into the transcript (it duplicated the tool
row, and from a subagent thread it leaked into the main chat); queries are one line."""
from jarvis.tools.web import verified


def test_quiet_and_single_line_query(monkeypatch):
    from jarvis.console import console

    printed = []
    monkeypatch.setattr(console, "print", lambda *a, **k: printed.append(a), raising=False)
    monkeypatch.setattr(verified, "gather_candidates", lambda q: [])

    out = verified.verified_search("Goldbach conjecture\n  twin prime\nunsolved")

    assert printed == []
    assert "\n" not in out
    assert 'Goldbach conjecture twin prime unsolved' in out
