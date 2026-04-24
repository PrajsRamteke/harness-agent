"""verified_search: multi-source cross-checked research."""
from concurrent.futures import ThreadPoolExecutor, as_completed

from ...console import console
from ...constants import MAX_TOOL_OUTPUT
from ._common import _domain_score, _fetch_snippet
from ._collect import gather_candidates, dedupe_by_domain
from ._claims import _extract_key_claims, _agreement_score
from ._report import build_report
from .search import _enrich_query_with_date


def verified_search(query: str, min_sources: int = 5, max_sources: int = 10) -> str:
    """
    Multi-source verified web search.

    Steps:
      1. Collect 12+ candidate URLs from DuckDuckGo (JSON + HTML).
      2. Deduplicate by domain so no single site dominates.
      3. Fetch page content from min_sources..max_sources URLs in parallel.
      4. Score each source by domain credibility (1-10).
      5. Extract key claims from the highest-trust sources.
      6. Cross-check each claim against ALL other sources (agreement score).
      7. Return a structured report: verified facts, contested points,
         source list with trust scores, and a confidence summary.
    """
    query = _enrich_query_with_date(query)
    console.print(f"[dim cyan]🔍 verified_search: collecting sources for \"{query}\"…[/]")

    candidates = gather_candidates(query)
    deduped = dedupe_by_domain(candidates)
    deduped.sort(key=lambda x: _domain_score(x[0], query)[0], reverse=True)
    to_fetch = deduped[:max_sources]

    # Guarantee the official site (if any) is in the fetch set even if DDG
    # ranked it outside the top `max_sources`.
    official_extra = [
        c for c in deduped[max_sources:]
        if _domain_score(c[0], query)[1].startswith("official")
    ]
    for item in official_extra[:2]:
        if item not in to_fetch:
            to_fetch.insert(0, item)

    if not to_fetch:
        return f'❌ verified_search: could not find any sources for "{query}".'

    console.print(
        f"[dim]  → fetching content from {len(to_fetch)} sources in parallel…[/]"
    )

    # ── Step 3: fetch page content in parallel ────────────────────────
    source_data: list = []

    def _fetch_one(item: tuple) -> dict:
        url, title, snip = item
        trust, label = _domain_score(url, query)
        content = _fetch_snippet(url, max_chars=2500)
        return {
            "url": url, "title": title, "snippet": snip,
            "content": content, "trust": trust, "label": label,
            "is_official": label.startswith("official"),
        }

    with ThreadPoolExecutor(max_workers=min(8, len(to_fetch))) as pool:
        futures = {pool.submit(_fetch_one, item): item for item in to_fetch}
        for fut in as_completed(futures):
            try:
                source_data.append(fut.result())
            except Exception:
                pass

    good_sources = [
        s for s in source_data
        if not s["content"].startswith("[fetch error")
        and len(s["content"]) > 100
    ]
    error_sources = [
        s for s in source_data
        if s["content"].startswith("[fetch error") or len(s["content"]) <= 100
    ]

    if not good_sources:
        return (
            f'⚠️  verified_search: found {len(to_fetch)} URLs but could not '
            f'fetch readable content from any of them for "{query}".\n'
            + "\n".join(f"  • {s['url']}" for s in to_fetch)
        )

    # Always sort official sources to the very top, then by trust.
    good_sources.sort(key=lambda s: (s.get("is_official", False), s["trust"]), reverse=True)
    all_contents = [s["content"] for s in good_sources]
    official_sources = [s for s in good_sources if s.get("is_official")]

    # ── Step 4 & 5: extract key claims, prioritising official sources ─
    top_sources = official_sources[:2] + [
        s for s in good_sources if not s.get("is_official")
    ][: max(1, 3 - len(official_sources[:2]))]
    raw_claims: list = []
    official_claims: set = set()
    for src in top_sources:
        claims = _extract_key_claims(src["content"], max_sentences=6)
        raw_claims.extend(claims)
        if src.get("is_official"):
            official_claims.update(claims)
    seen_claim_words: set = set()
    unique_claims: list = []
    for c in raw_claims:
        sig = frozenset(c.lower().split())
        overlap = len(sig & seen_claim_words) / max(len(sig), 1)
        if overlap < 0.6:
            unique_claims.append(c)
            seen_claim_words |= sig
        if len(unique_claims) >= 10:
            break

    # ── Step 6: cross-check each claim ───────────────────────────────
    # A claim is "verified" if it came from the official source OR if
    # ≥50% of other sources agree. Official-source facts are ground truth
    # for "what does X say about X" questions, so we never demote them to
    # 'contested' just because secondary sources haven't indexed them yet.
    verified: list = []
    contested: list = []
    for claim in unique_claims:
        ratio = _agreement_score(claim, all_contents)
        if claim in official_claims or ratio >= 0.5:
            verified.append((claim, ratio))
        else:
            contested.append((claim, ratio))

    report = build_report(query, good_sources, error_sources, to_fetch,
                          verified, contested, unique_claims)
    return report[:MAX_TOOL_OUTPUT]
