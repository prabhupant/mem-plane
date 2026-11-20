"""Reciprocal Rank Fusion (RRF) and weighted RRF implementation."""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Sequence


def weighted_rrf(
    ranked_lists: Sequence[tuple[Sequence[str], float]],
    k: int = 60,
    top_n: int = 5,
) -> list[tuple[str, float]]:
    """Fuse N ranked lists with weights using Reciprocal Rank Fusion.

    score(d) = sum over lists of weight / (k + rank_m(d)), rank starting at 1.
    Ties broken deterministically by doc_id ascending.

    Args:
        ranked_lists: Sequence of (ids in rank order, weight) tuples.
        k: Smoothing constant preventing high-ranked items from dominating (default: 60).
        top_n: Maximum number of fused results to return (default: 5).

    Returns:
        List of tuples (doc_id, score) sorted by score descending, then doc_id ascending.
    """
    if top_n <= 0 or k < 0 or not ranked_lists:
        return []

    scores: dict[str, float] = defaultdict(float)

    for items, weight in ranked_lists:
        if weight <= 0.0:
            continue
        seen: set[str] = set()
        rank = 1
        for doc_id in items:
            if doc_id not in seen:
                seen.add(doc_id)
                scores[doc_id] += float(weight) / (k + rank)
                rank += 1

    if not scores:
        return []

    # Sort by score descending; break ties deterministically by doc_id ascending
    sorted_items = sorted(scores.items(), key=lambda item: (-item[1], item[0]))
    return sorted_items[:top_n]


def reciprocal_rank_fusion(
    dense_ranks: Sequence[str],
    lexical_ranks: Sequence[str],
    k: int = 60,
    top_n: int = 5,
) -> list[tuple[str, float]]:
    """Legacy 2-list RRF wrapper delegating to weighted_rrf with weight 1.0."""
    return weighted_rrf(
        [(dense_ranks, 1.0), (lexical_ranks, 1.0)],
        k=k,
        top_n=top_n,
    )
