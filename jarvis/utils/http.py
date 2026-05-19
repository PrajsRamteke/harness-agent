"""HTTP helpers used by OAuth flows."""
import json, urllib.parse, urllib.request, urllib.error


def _http_json(
    url: str,
    payload: dict,
    timeout: int = 30,
    *,
    user_agent: str | None = None,
) -> tuple:
    """POST JSON, return (status, body_dict_or_text)."""
    body = json.dumps(payload).encode()
    req = urllib.request.Request(
        url, data=body, method="POST",
        headers={
            "Content-Type": "application/json",
            "Accept": "application/json",
            # Cloudflare rejects the default "Python-urllib/X.Y" UA on Anthropic
            # OAuth endpoints. Callers pass OAUTH_TOKEN_USER_AGENT for token
            # exchange/refresh; other callers get a generic browser UA.
            "User-Agent": user_agent or (
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/125.0.0.0 Safari/537.36"
            ),
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            raw = r.read().decode("utf-8", errors="replace")
            try: return r.status, json.loads(raw)
            except json.JSONDecodeError: return r.status, raw
    except urllib.error.HTTPError as e:
        raw = e.read().decode("utf-8", errors="replace")
        try: return e.code, json.loads(raw)
        except json.JSONDecodeError: return e.code, raw
    except urllib.error.URLError as e:
        return 0, f"network error: {e.reason}"


def _http_form(
    url: str,
    fields: dict[str, str],
    timeout: int = 30,
    *,
    user_agent: str | None = None,
) -> tuple[int, object]:
    """POST application/x-www-form-urlencoded; return (status, body_dict_or_text)."""
    body = urllib.parse.urlencode(fields).encode()
    req = urllib.request.Request(
        url,
        data=body,
        method="POST",
        headers={
            "Content-Type": "application/x-www-form-urlencoded",
            "Accept": "application/json",
            "User-Agent": user_agent or (
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/125.0.0.0 Safari/537.36"
            ),
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            raw = r.read().decode("utf-8", errors="replace")
            try:
                return r.status, json.loads(raw)
            except json.JSONDecodeError:
                return r.status, raw
    except urllib.error.HTTPError as e:
        raw = e.read().decode("utf-8", errors="replace")
        try:
            return e.code, json.loads(raw)
        except json.JSONDecodeError:
            return e.code, raw
    except urllib.error.URLError as e:
        return 0, f"network error: {e.reason}"
