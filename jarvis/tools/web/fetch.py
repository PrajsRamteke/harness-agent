"""fetch_url tool — raw or plaintext."""
import urllib.request, urllib.error

from ...constants import MAX_TOOL_OUTPUT
from ...utils.html_clean import _strip_html


def fetch_url(url: str, raw: bool = False) -> str:
    """
    Fetch a URL and return its content as plain text (HTML stripped by default).
    Set raw=True to get the raw response body (HTML/JSON/etc.).
    Follows redirects automatically. Respects MAX_TOOL_OUTPUT.
    """
    try:
        req = urllib.request.Request(
            url,
            headers={
                "User-Agent": (
                    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/124.0 Safari/537.36"
                ),
                "Accept": "text/html,application/xhtml+xml,application/json,*/*;q=0.9",
                "Accept-Language": "en-US,en;q=0.9",
            },
        )
        with urllib.request.urlopen(req, timeout=15) as r:
            content_type = r.headers.get("Content-Type", "")
            body = r.read().decode("utf-8", errors="replace")
            final_url = r.url

        if raw or "json" in content_type or not (
            "html" in content_type or body.lstrip().startswith("<")
        ):
            result = body
        else:
            result = _strip_html(body)

        header = f"📄 {final_url}\n[{len(result)} chars]\n" + "─" * 60 + "\n"
        return (header + result)[:MAX_TOOL_OUTPUT]

    except urllib.error.HTTPError as e:
        return f"HTTP ERROR {e.code}: {e.reason} — {url}"
    except urllib.error.URLError as e:
        return f"URL ERROR: {e.reason} — {url}"
    except Exception as e:
        return f"ERROR fetching {url}: {type(e).__name__}: {e}"
