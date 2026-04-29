"""Quick DuckDuckGo web search tool."""
import json, re, html, urllib.parse, urllib.request
from datetime import datetime

from ...constants import MAX_TOOL_OUTPUT, SEARCH_DEFAULT_MAX_RESULTS

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


_BLOCKED_MARKER = "STOP_RETRYING"


def web_search(query: str, max_results: int = SEARCH_DEFAULT_MAX_RESULTS) -> str:
    """
    Search the web using DuckDuckGo's free JSON API (no key required).
    Returns a ranked list of results: title, URL, and snippet.

    Detects rate-limiting / IP blocks (HTTP 202 + DDG splash page) and returns
    an explicit STOP_RETRYING signal so the agent doesn't keep retrying.
    """
    query = _enrich_query_with_date(query)
    results: list = []
    ddg_blocked = False
    last_error: str = ""

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
            status = r.status
            data = json.loads(r.read().decode("utf-8", errors="replace"))
        # 202 from DDG IA = "request accepted but no answer" — typically the
        # rate-limit/block path; payload is empty when this happens.
        if status == 202 and not any([
            data.get("AbstractText"), data.get("Answer"),
            data.get("Definition"), data.get("RelatedTopics"),
        ]):
            ddg_blocked = True

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
        last_error = f"DDG JSON error: {e}"

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
                status2 = r.status
                raw_html = r.read().decode("utf-8", errors="replace")

            link_re = re.compile(
                r'<a[^>]+class="result__a"[^>]+href="([^"]+)"[^>]*>(.*?)</a>',
                re.S | re.I,
            )
            snip_re = re.compile(
                r'<a[^>]+class="result__snippet"[^>]*>(.*?)</a>', re.S | re.I
            )
            links = link_re.findall(raw_html)
            # Block detection: 202 status, OR no result__a markers AND the
            # response is the DDG homepage (canonical URL present).
            html_blocked = (
                status2 == 202
                or (not links and 'href="https://duckduckgo.com/"' in raw_html)
            )
            if html_blocked:
                ddg_blocked = True
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
            last_error = f"DDG HTML error: {e}"

    if not results:
        if ddg_blocked:
            return (
                f"⚠️  {_BLOCKED_MARKER}: DuckDuckGo is rate-limiting this IP "
                f"(HTTP 202 / splash page). Re-running web_search with a "
                f"different query will NOT help — same block applies to every "
                f"query. Do NOT retry web_search this turn. "
                f"Tell the user the search backend is temporarily blocked and "
                f"answer from your own knowledge, or wait ~1 hour and try again. "
                f"(query was: \"{query}\")"
            )
        msg = f'No results found for "{query}".'
        if last_error:
            msg += f" [{last_error}]"
        return msg

    header = f'🔍 Web search: "{query}" — {len(results)} result(s)\n' + "─" * 60
    return (header + "\n\n" + "\n\n".join(results))[:MAX_TOOL_OUTPUT]
