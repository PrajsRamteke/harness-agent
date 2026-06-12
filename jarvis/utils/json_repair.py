"""Best-effort recovery of malformed streamed tool-call JSON arguments.

Providers occasionally deliver tool-call argument buffers that fail
``json.loads``.  Observed failure modes, roughly by frequency:

* **truncated mid-write** — response hit the output-token cap, leaving an
  unterminated string and unclosed braces
* **unescaped inner double quotes** — the model embeds HTML/code like
  ``class="x"`` inside a JSON string without escaping
  (manifests as ``Expecting ',' delimiter at pos N``)
* **raw control characters** — literal newlines/tabs inside string values
* **markdown fences / prose** wrapped around the JSON object
* **trailing garbage or concatenated objects** — ``{...}{...}`` when a
  provider merges two tool calls into one arguments buffer
* **dangling comma / half-written key** at the truncation point

:func:`repair_json_arguments` runs a pipeline of increasingly aggressive
strategies and returns the first candidate that parses to a dict.  Lossless
strategies run before lossy ones, so data is never discarded when a gentler
fix suffices.  Returns ``None`` when nothing works — callers then surface an
actionable error to the model.
"""

from __future__ import annotations

import json
import re
from typing import Iterator, Optional

_FENCE_OPEN_RE = re.compile(r"^\s*```[\w-]*[ \t]*\n?")
_FENCE_CLOSE_RE = re.compile(r"\n?[ \t]*```\s*$")
# Incomplete \uXXXX escape at a truncation point (0-3 hex digits then EOF).
_PARTIAL_UNICODE_RE = re.compile(r"\\u[0-9a-fA-F]{0,3}$")

_WHITESPACE = " \t\r\n"
# A quote inside a string is a *closing* quote only if the next
# non-whitespace char is one of these (or end of buffer).
_CLOSING_FOLLOWERS = ",:}]"


def _scan(s: str) -> tuple[bool, list[str]]:
    """Walk *s* tracking JSON lexer state.

    Returns ``(in_string, stack)`` where *stack* holds unclosed ``{``/``[``
    openers in order.
    """
    in_string = False
    escape = False
    stack: list[str] = []
    for ch in s:
        if escape:
            escape = False
            continue
        if in_string:
            if ch == "\\":
                escape = True
            elif ch == '"':
                in_string = False
            continue
        if ch == '"':
            in_string = True
        elif ch in "{[":
            stack.append(ch)
        elif ch in "}]" and stack:
            stack.pop()
    return in_string, stack


def _close_open(s: str) -> str:
    """Append the minimum suffix that terminates an abruptly-cut buffer:
    finish the open string, drop dangling comma, complete a dangling
    ``"key":`` with ``null``, then close brackets innermost-first."""
    in_string, stack = _scan(s)
    repaired = s
    if in_string:
        repaired = _PARTIAL_UNICODE_RE.sub("", repaired)
        if repaired.endswith("\\"):
            repaired = repaired[:-1]
        repaired += '"'
    repaired = repaired.rstrip()
    if repaired.endswith(","):
        repaired = repaired[:-1].rstrip()
    if repaired.endswith(":"):
        repaired += " null"
    for opener in reversed(stack):
        repaired += "}" if opener == "{" else "]"
    return repaired


def _escape_rogue_quotes(s: str) -> str:
    """Escape double quotes that are clearly *inside* a string value.

    Heuristic: while inside a string, a ``"`` only closes it when the next
    non-whitespace character is a JSON delimiter (``, : } ]``) or the buffer
    ends; any other follower means the model embedded an unescaped quote
    (HTML attributes, quoted prose, code) and we escape it.
    """
    out: list[str] = []
    in_string = False
    escape = False
    n = len(s)
    for i, ch in enumerate(s):
        if escape:
            out.append(ch)
            escape = False
            continue
        if ch == "\\":
            out.append(ch)
            if in_string:
                escape = True
            continue
        if ch != '"':
            out.append(ch)
            continue
        if not in_string:
            in_string = True
            out.append(ch)
            continue
        j = i + 1
        while j < n and s[j] in _WHITESPACE:
            j += 1
        if j >= n or s[j] in _CLOSING_FOLLOWERS:
            in_string = False
            out.append(ch)
        else:
            out.append('\\"')
    return "".join(out)


def _cut_candidates(s: str, limit: int = 10) -> Iterator[str]:
    """Lossy last resort: drop the trailing incomplete key/value pair.

    Yields candidates cut at the last few structural points (commas and
    container openers outside strings), each closed via :func:`_close_open`.
    """
    cuts: list[int] = []
    in_string = False
    escape = False
    for i, ch in enumerate(s):
        if escape:
            escape = False
            continue
        if in_string:
            if ch == "\\":
                escape = True
            elif ch == '"':
                in_string = False
            continue
        if ch == '"':
            in_string = True
        elif ch == ",":
            cuts.append(i)  # cut *before* the comma
        elif ch in "{[":
            cuts.append(i + 1)  # cut right after the opener
    seen: set[str] = set()
    for pos in reversed(cuts[-limit:]):
        candidate = _close_open(s[:pos])
        if candidate not in seen:
            seen.add(candidate)
            yield candidate


def _try_parse(candidate: str) -> Optional[dict]:
    """Parse *candidate*; strict=False second pass tolerates raw control
    characters (literal newlines/tabs) inside strings."""
    for strict in (True, False):
        try:
            parsed = json.loads(candidate, strict=strict)
        except Exception:
            continue
        if isinstance(parsed, dict):
            return parsed
    return None


def repair_json_arguments(raw: str) -> Optional[dict]:
    """Recover a dict from a malformed tool-arguments buffer, or ``None``.

    Strategies run lossless-first:
      1. parse as-is (strict, then strict=False for raw control chars)
      2. strip markdown fences / leading prose before the first ``{``
      3. first complete object via ``raw_decode`` (trailing garbage,
         concatenated ``{...}{...}`` buffers)
      4. close an abruptly-truncated buffer (string + brackets)
      5. escape rogue inner quotes — alone, and combined with closing
      6. lossy: cut the trailing incomplete pair at the last structural
         point, then close (also tried on the quote-escaped variant)
    """
    if not raw or not raw.strip():
        return None
    s = raw.strip()
    s = _FENCE_OPEN_RE.sub("", s)
    s = _FENCE_CLOSE_RE.sub("", s)
    start = s.find("{")
    if start == -1:
        return None
    s = s[start:]

    parsed = _try_parse(s)
    if parsed is not None:
        return parsed

    for strict in (True, False):
        try:
            obj, _end = json.JSONDecoder(strict=strict).raw_decode(s)
        except Exception:
            continue
        if isinstance(obj, dict):
            return obj

    parsed = _try_parse(_close_open(s))
    if parsed is not None:
        return parsed

    escaped = _escape_rogue_quotes(s)
    if escaped != s:
        parsed = _try_parse(escaped)
        if parsed is not None:
            return parsed
        parsed = _try_parse(_close_open(escaped))
        if parsed is not None:
            return parsed

    variants = [s] if escaped == s else [s, escaped]
    for variant in variants:
        for candidate in _cut_candidates(variant):
            parsed = _try_parse(candidate)
            if parsed is not None:
                return parsed
    return None
