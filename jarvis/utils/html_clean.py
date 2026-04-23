"""Very lightweight HTML → plain-text helper."""
import re, html


def _strip_html(raw: str) -> str:
    """Very lightweight HTML → plain-text: strip tags, decode entities."""
    # remove <script>, <style> blocks entirely
    raw = re.sub(r"<(script|style)[^>]*>.*?</\1>", " ", raw, flags=re.S | re.I)
    # remove all other tags
    raw = re.sub(r"<[^>]+>", " ", raw)
    # collapse whitespace
    raw = re.sub(r"[ \t]+", " ", raw)
    raw = re.sub(r"\n{3,}", "\n\n", raw)
    return html.unescape(raw).strip()
