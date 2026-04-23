"""Claim extraction / agreement scoring for verified_search."""
import re


def _extract_key_claims(text: str, max_sentences: int = 6) -> list:
    """Pull the first N sentences from a text block as 'key claims'."""
    sentences = re.split(r"(?<=[.!?])\s+", text.strip())
    out = []
    for s in sentences:
        s = s.strip()
        if len(s) > 30:
            out.append(s)
        if len(out) >= max_sentences:
            break
    return out


def _agreement_score(claim: str, corpus: list) -> float:
    """
    Simple keyword-overlap agreement: what fraction of sources contain
    the key nouns/numbers from `claim`.  Returns 0.0–1.0.
    """
    if not corpus:
        return 0.0
    stopwords = {
        "that", "this", "with", "from", "have", "will", "been", "they",
        "their", "there", "were", "also", "some", "when", "which", "what",
    }
    words = [
        w.lower()
        for w in re.findall(r"\b[a-zA-Z0-9]{4,}\b", claim)
        if w.lower() not in stopwords
    ]
    if not words:
        return 0.5
    hits = sum(
        1 for doc in corpus
        if sum(1 for w in words if w in doc.lower()) >= max(1, len(words) // 3)
    )
    return hits / len(corpus)
