"""Quick DuckDuckGo web search tool."""
import json, re, html, urllib.parse, urllib.request
from datetime import datetime

from ...constants import MAX_TOOL_OUTPUT

_RECENCY_RE = re.compile(
    r"\b(latest|current|currently|recent|recently|today|todays?|now|"
    r"this\s+(?:year|month|week)|this-year|new|newest|upcoming|"
    r"as\s+of|right\s+now|nowadays)\b",
    re.I,
)
_YEAR_RE = re.compile(r"\b(19|20)\d{2}\b")


def _enrich_query_with_date(query: str) -> str:
    """Ensure queries about current/recent info are anchored to today's year.

    - If the query contains a stale year (anything other than the current year),
      rewrite it to the current year. The model often hardcodes last year's
      number in its query even when the user wants fresh data.
    - If no year is present but the query implies recency, append current year.
    """
    current_year = datetime.now().year
    has_recency = bool(_RECENCY_RE.search(query))
    if _YEAR_RE.search(query):
        if has_recency:
            def _sub(m: "re.Match") -> str:
                y = int(m.group(0))
                return str(current_year) if y != current_year else m.group(0)
            return _YEAR_RE.sub(_sub, query)
        return query
    if has_recency:
        return f"{query} {current_year}"
    return query


def web_search(query: str, max_results: int = 8) -> str:
    """
    Search the web using DuckDuckGo's free JSON API (no key required).
    Returns a ranked list of results: title, URL, and snippet.
    Also tries the HTML endpoint for extra organic results when the JSON
    Instant Answer doesn't return enough hits.
    """
    query = _enrich_query_with_date(query)
    results: list = []

    # ── 1. DuckDuckGo Instant Answer API (JSON) ──────────────────────
    try:
        ia_url = (
            "https://api.duckduckgo.com/?q="
            + urllib.parse.quote_plus(query)
            + "&format=json&no_redirect=1&no_html=1&skip_disambig=1"
        )
        req = urllib.request.Request(
            ia_url,
            headers={"User-Agent": "HarnessAgent/1.0 (macOS; python)"},
        )
        with urllib.request.urlopen(req, timeout=10) as r:
            data = json.loads(r.read().decode("utf-8", errors="replace"))

        abstract = data.get("AbstractText", "").strip()
        abstract_url = data.get("AbstractURL", "").strip()
        if abstract:
            results.append(f"[Abstract]\n{abstract}\n🔗 {abstract_url}")

        for t in data.get("RelatedTopics", [])[:max_results]:
            if isinstance(t, dict) and t.get("Text") and t.get("FirstURL"):
                results.append(f"• {t['Text']}\n  🔗 {t['FirstURL']}")
            elif isinstance(t, dict) and t.get("Topics"):
                for sub in t["Topics"][:3]:
                    if sub.get("Text") and sub.get("FirstURL"):
                        results.append(f"• {sub['Text']}\n  🔗 {sub['FirstURL']}")

        defn = data.get("Definition", "").strip()
        defn_url = data.get("DefinitionURL", "").strip()
        if defn:
            results.append(f"[Definition]\n{defn}\n🔗 {defn_url}")

        answer = data.get("Answer", "").strip()
        if answer:
            results.insert(0, f"[Direct Answer] {answer}")

    except Exception as e:
        results.append(f"[DDG JSON error: {e}]")

    # ── 2. DDG HTML scrape for organic links (fallback / supplement) ──
    if len(results) < 3:
        try:
            html_url = (
                "https://html.duckduckgo.com/html/?q="
                + urllib.parse.quote_plus(query)
            )
            req2 = urllib.request.Request(
                html_url,
                headers={
                    "User-Agent": (
                        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                        "AppleWebKit/537.36 (KHTML, like Gecko) "
                        "Chrome/124.0 Safari/537.36"
                    )
                },
            )
            with urllib.request.urlopen(req2, timeout=12) as r:
                raw_html = r.read().decode("utf-8", errors="replace")

            link_re = re.compile(
                r'<a[^>]+class="result__a"[^>]+href="([^"]+)"[^>]*>(.*?)</a>',
                re.S | re.I,
            )
            snip_re = re.compile(
                r'<a[^>]+class="result__snippet"[^>]*>(.*?)</a>', re.S | re.I
            )
            links = link_re.findall(raw_html)
            snips = [html.unescape(re.sub(r"<[^>]+>", "", s)) for s in snip_re.findall(raw_html)]

            for i, (href, title) in enumerate(links[:max_results]):
                try:
                    qs = urllib.parse.parse_qs(urllib.parse.urlparse(href).query)
                    href = qs.get("uddg", [href])[0]
                except Exception:
                    pass
                clean_title = html.unescape(re.sub(r"<[^>]+>", "", title)).strip()
                snip = snips[i].strip() if i < len(snips) else ""
                entry = f"• {clean_title}\n  🔗 {urllib.parse.unquote(href)}"
                if snip:
                    entry += f"\n  {snip}"
                results.append(entry)

        except Exception as e:
            results.append(f"[DDG HTML error: {e}]")

    if not results:
        return f'No results found for "{query}".'

    header = f'🔍 Web search: "{query}" — {len(results)} result(s)\n' + "─" * 60
    return (header + "\n\n" + "\n\n".join(results))[:MAX_TOOL_OUTPUT]
