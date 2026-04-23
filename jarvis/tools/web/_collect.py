"""Candidate URL gathering for verified_search."""
import json, urllib.parse, urllib.request

from ._common import _ddg_organic_urls


def gather_candidates(query: str) -> list:
    """Return list of (url, title, snippet) candidates for a query."""
    candidates: list = []

    # DDG JSON Instant Answer
    try:
        ia_url = (
            "https://api.duckduckgo.com/?q="
            + urllib.parse.quote_plus(query)
            + "&format=json&no_redirect=1&no_html=1&skip_disambig=1"
        )
        req = urllib.request.Request(
            ia_url, headers={"User-Agent": "HarnessAgent/1.0 (macOS; python)"}
        )
        with urllib.request.urlopen(req, timeout=10) as r:
            data = json.loads(r.read().decode("utf-8", errors="replace"))

        if data.get("AbstractURL") and data.get("AbstractText"):
            candidates.append((
                data["AbstractURL"],
                data.get("AbstractSource", "Abstract"),
                data["AbstractText"][:300],
            ))
        for t in data.get("RelatedTopics", [])[:8]:
            if isinstance(t, dict) and t.get("FirstURL") and t.get("Text"):
                candidates.append((t["FirstURL"], t.get("Text", "")[:80], t.get("Text", "")[:200]))
    except Exception:
        pass

    # DDG HTML organic results
    organic = _ddg_organic_urls(query, want=14)
    candidates.extend(organic)
    return candidates


def dedupe_by_domain(candidates: list, per_domain: int = 2) -> list:
    seen_domains: dict = {}
    deduped: list = []
    for url, title, snip in candidates:
        try:
            domain = urllib.parse.urlparse(url).netloc.lower().lstrip("www.")
        except Exception:
            domain = url
        if seen_domains.get(domain, 0) < per_domain:
            deduped.append((url, title, snip))
            seen_domains[domain] = seen_domains.get(domain, 0) + 1
    return deduped
