"""Report-building helpers for verified_search."""


def build_report(query: str, good_sources: list, error_sources: list,
                 to_fetch: list, verified: list, contested: list,
                 unique_claims: list) -> str:
    avg_trust = sum(s["trust"] for s in good_sources) / len(good_sources)
    verified_ratio = len(verified) / max(len(unique_claims), 1)
    confidence = "🟢 HIGH" if avg_trust >= 7 and verified_ratio >= 0.6 else \
                 "🟡 MEDIUM" if avg_trust >= 5 else "🔴 LOW"

    lines: list = []
    lines.append('╔══ 🔍 VERIFIED SEARCH REPORT ══════════════════════════════╗')
    lines.append(f'  Query    : "{query}"')
    lines.append(f'  Sources  : {len(good_sources)} fetched / {len(to_fetch)} found'
                 + (f' ({len(error_sources)} unreachable)' if error_sources else ''))
    lines.append(f'  Avg trust: {avg_trust:.1f}/10   Confidence: {confidence}')
    lines.append('╚═══════════════════════════════════════════════════════════╝')
    lines.append("")

    if verified:
        lines.append("✅ VERIFIED FACTS  (agreed by ≥50% of sources)")
        lines.append("─" * 60)
        for claim, ratio in sorted(verified, key=lambda x: -x[1]):
            pct = int(ratio * 100)
            lines.append(f"  [{pct:3d}% agreement]  {claim}")
        lines.append("")

    if contested:
        lines.append("⚠️  CONTESTED / UNCERTAIN  (found in <50% of sources)")
        lines.append("─" * 60)
        for claim, ratio in sorted(contested, key=lambda x: -x[1]):
            pct = int(ratio * 100)
            lines.append(f"  [{pct:3d}% agreement]  {claim}")
        lines.append("")

    lines.append("📚 SOURCES  (sorted by trust score)")
    lines.append("─" * 60)
    for i, s in enumerate(good_sources, 1):
        bar = "█" * s["trust"] + "░" * (10 - s["trust"])
        lines.append(f"  {i:2d}. [{bar}] {s['trust']}/10  {s['label']}")
        lines.append(f"      {s['title'][:70]}")
        lines.append(f"      🔗 {s['url']}")
        if s["snippet"]:
            lines.append(f"      ↳ {s['snippet'][:120]}")
        lines.append("")

    if error_sources:
        lines.append("❌ UNREACHABLE SOURCES")
        lines.append("─" * 60)
        for s in error_sources:
            lines.append(f"  • {s['url']}")
        lines.append("")

    lines.append("─" * 60)
    lines.append(
        f"ℹ️  This answer was cross-verified across {len(good_sources)} independent "
        f"websites. Claims marked ✅ appeared in ≥50% of sources. "
        f"Always check primary sources for critical decisions."
    )
    return "\n".join(lines)
