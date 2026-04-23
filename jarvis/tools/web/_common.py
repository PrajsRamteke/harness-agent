"""Shared constants + helpers for web tools."""
import re, html, urllib.parse, urllib.request

from ...utils.html_clean import _strip_html

# Credibility tiers: trusted domains score higher
_TRUSTED_DOMAINS: dict = {
    # encyclopaedias / reference
    "wikipedia.org": 10, "britannica.com": 10, "scholarpedia.org": 9,
    # science & academia
    "nature.com": 10, "science.org": 10, "pubmed.ncbi.nlm.nih.gov": 10,
    "ncbi.nlm.nih.gov": 10, "scholar.google.com": 9, "arxiv.org": 9,
    "researchgate.net": 8, "jstor.org": 9, "ieee.org": 9, "acm.org": 9,
    # government / official
    "gov": 9, "edu": 9, "who.int": 10, "cdc.gov": 10, "nih.gov": 10,
    "fda.gov": 10, "europa.eu": 9,
    # reputable news
    "bbc.com": 9, "bbc.co.uk": 9, "reuters.com": 9, "apnews.com": 9,
    "theguardian.com": 8, "nytimes.com": 8, "washingtonpost.com": 8,
    "economist.com": 8, "ft.com": 8, "bloomberg.com": 8, "wsj.com": 8,
    "npr.org": 8, "pbs.org": 8, "theatlantic.com": 7,
    # tech / official docs
    "docs.python.org": 10, "developer.mozilla.org": 10, "mdn.io": 10,
    "stackoverflow.com": 8, "github.com": 7, "developer.apple.com": 9,
    "docs.microsoft.com": 9, "learn.microsoft.com": 9,
    "cloud.google.com": 9, "aws.amazon.com": 9,
}
_UNTRUSTED_PATTERNS: list = [
    "quora.com", "reddit.com", "yahoo.com/answers", "answers.com",
    "buzzfeed.com", "dailymail.co.uk", "thesun.co.uk", "nypost.com",
]

_BROWSER_UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/124.0 Safari/537.36"
)


def _domain_score(url: str) -> tuple:
    """Return (trust_score 1-10, label) for a URL."""
    try:
        host = urllib.parse.urlparse(url).netloc.lower().lstrip("www.")
    except Exception:
        return 5, "unknown"
    for pat, score in _TRUSTED_DOMAINS.items():
        if host == pat or host.endswith("." + pat) or host.endswith(pat):
            return score, pat
    for bad in _UNTRUSTED_PATTERNS:
        if bad in host:
            return 3, f"low-trust ({bad})"
    tld = host.rsplit(".", 1)[-1] if "." in host else ""
    if tld in ("gov", "edu", "ac"):
        return 9, f"{tld} domain"
    return 5, "general"


def _fetch_snippet(url: str, max_chars: int = 3000) -> str:
    """Fetch a URL and return a short plain-text snippet, silently on error."""
    try:
        req = urllib.request.Request(
            url,
            headers={"User-Agent": _BROWSER_UA,
                     "Accept": "text/html,application/xhtml+xml,*/*;q=0.9",
                     "Accept-Language": "en-US,en;q=0.9"},
        )
        with urllib.request.urlopen(req, timeout=10) as r:
            raw = r.read(60_000).decode("utf-8", errors="replace")
        return _strip_html(raw)[:max_chars]
    except Exception as e:
        return f"[fetch error: {e}]"


def _ddg_organic_urls(query: str, want: int = 12) -> list:
    """
    Return up to `want` (url, title, snippet) tuples from DDG HTML scrape.
    """
    results: list = []
    try:
        html_url = (
            "https://html.duckduckgo.com/html/?q="
            + urllib.parse.quote_plus(query)
        )
        req = urllib.request.Request(html_url, headers={"User-Agent": _BROWSER_UA})
        with urllib.request.urlopen(req, timeout=12) as r:
            raw_html = r.read().decode("utf-8", errors="replace")

        link_re = re.compile(
            r'<a[^>]+class="result__a"[^>]+href="([^"]+)"[^>]*>(.*?)</a>',
            re.S | re.I,
        )
        snip_re = re.compile(
            r'<a[^>]+class="result__snippet"[^>]*>(.*?)</a>', re.S | re.I
        )
        links = link_re.findall(raw_html)
        snips = [
            html.unescape(re.sub(r"<[^>]+>", "", s)).strip()
            for s in snip_re.findall(raw_html)
        ]
        for i, (href, title) in enumerate(links[:want]):
            try:
                qs = urllib.parse.parse_qs(urllib.parse.urlparse(href).query)
                href = qs.get("uddg", [href])[0]
            except Exception:
                pass
            href = urllib.parse.unquote(href)
            clean_title = html.unescape(re.sub(r"<[^>]+>", "", title)).strip()
            snip = snips[i] if i < len(snips) else ""
            results.append((href, clean_title, snip))
    except Exception:
        pass
    return results
