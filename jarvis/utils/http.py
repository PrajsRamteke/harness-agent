"""HTTP JSON helper used by OAuth flows."""
import json, urllib.request, urllib.error


def _http_json(url: str, payload: dict, timeout: int = 30) -> tuple:
    """POST JSON, return (status, body_dict_or_text)."""
    body = json.dumps(payload).encode()
    req = urllib.request.Request(
        url, data=body, method="POST",
        headers={"Content-Type": "application/json", "Accept": "application/json"},
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
