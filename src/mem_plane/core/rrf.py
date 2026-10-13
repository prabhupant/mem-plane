"""Reciprocal Rank Fusion (RRF) implementation for fusing dense and lexical search results."""

from collections import defaultdict
from collections.abc import Sequence


def reciprocal_rank_fusion(
    dense_ranks: Sequence[str],
    lexical_ranks: Sequence[str],
    k: int = 60,
    top_n: int = 5,
) -> list[tuple[str, float]]:
    """Fuse ranked lists from dense vector search and lexical (BM25) search using RRF.

    RRF_Score(d) = sum_{m in M} (1 / (k + rank_m(d)))

    Args:
        dense_ranks: Ordered list of document IDs from dense vector retrieval.
        lexical_ranks: Ordered list of document IDs from lexical/BM25 retrieval.
        k: Smoothing constant preventing high-ranked items from dominating (default: 60).
        top_n: Maximum number of fused results to return (default: 5).

    Returns:
        A list of tuples (doc_id, score) sorted by score descending, with deterministic
        tie-breaking by doc_id ascending.
    """
    if top_n <= 0 or k < 0:
        return []

    scores: dict[str, float] = defaultdict(float)

    # Process dense rankings (1-based rank)
    seen_dense: set[str] = set()
    rank = 1
    for doc_id in dense_ranks:
        if doc_id not in seen_dense:
            seen_dense.add(doc_id)
            scores[doc_id] += 1.0 / (k + rank)
            rank += 1

    # Process lexical rankings (1-based rank)
    seen_lexical: set[str] = set()
    rank = 1
    for doc_id in lexical_ranks:
        if doc_id not in seen_lexical:
            seen_lexical.add(doc_id)
            scores[doc_id] += 1.0 / (k + rank)
            rank += 1

    # Sort by score descending; break ties deterministically by doc_id ascending
    sorted_items = sorted(scores.items(), key=lambda item: (-item[1], item[0]))

    return sorted_items[:top_n]
