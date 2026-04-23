"""Hallucination scrubber — drop sentences that look like fabricated facts."""
import re

# Patterns that strongly indicate fabricated facts stated as truth.
_HALLUCINATION_PATTERNS: list = [
    # "the official changelog / docs / release notes states / says / shows"
    re.compile(r'\b(official|changelog|release notes?|docs?|documentation)\b.{0,40}\b(states?|says?|shows?|confirms?|reads?|lists?)\b', re.I),
    # "according to <site>.com / <site>.org"
    re.compile(r'\baccording to\b.{0,60}\.(com|org|io|net|gov|edu)\b', re.I),
    # "the correct (date|version|release) is …"
    re.compile(r'\bthe correct\b.{0,40}\bis\b', re.I),
    # "I should have verified" / "I verified" / "I checked the source"
    re.compile(r'\bI (should have |have )?(verified|checked|confirmed|looked up)\b', re.I),
    # explicit fake self-correction: "not 2025" / "not 2024" etc.
    re.compile(r'\bnot 20\d\d\b', re.I),
]

_HALLUCINATION_WARNING = (
    "\n\n> ⚠️ **[Hallucination guard]** "
    "A sentence was removed because it contained unverified facts "
    "(version number, date, citation, or source) that were not fetched this session. "
    "Use `verified_search` to get real data.\n"
)


def _scrub_hallucinations(text: str) -> tuple:
    """
    Scan text line-by-line. Flag any line that matches a hallucination pattern.
    Flagged lines are dropped in-place; surrounding whitespace/structure is preserved.
    Returns (cleaned_text, was_flagged).
    """
    lines = text.splitlines(keepends=True)
    clean: list = []
    flagged = False
    for line in lines:
        hit = any(p.search(line) for p in _HALLUCINATION_PATTERNS)
        if hit:
            flagged = True
            clean.append("\n")
        else:
            clean.append(line)
    result = "".join(clean).strip()
    if flagged:
        result += _HALLUCINATION_WARNING
    return result, flagged
