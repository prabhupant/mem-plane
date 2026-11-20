"""Unit tests for Reciprocal Rank Fusion (RRF) algorithm."""

import pytest

from mem_plane.core.rrf import reciprocal_rank_fusion


def test_rrf_empty_inputs() -> None:
    """Validate behavior when inputs are empty or top_n is zero."""
    assert reciprocal_rank_fusion([], []) == []
    assert reciprocal_rank_fusion(["doc1"], [], top_n=0) == []
    assert reciprocal_rank_fusion([], [], top_n=5) == []


def test_rrf_single_source_presence() -> None:
    """Validate behavior when an item only exists in one of the candidate lists."""
    results = reciprocal_rank_fusion(["docA"], [], k=60, top_n=5)
    assert len(results) == 1
    assert results[0][0] == "docA"
    expected_score = 1.0 / (60 + 1)
    assert pytest.approx(results[0][1], rel=1e-6) == expected_score

    results_lex = reciprocal_rank_fusion([], ["docB"], k=60, top_n=5)
    assert len(results_lex) == 1
    assert results_lex[0][0] == "docB"
    assert pytest.approx(results_lex[0][1], rel=1e-6) == expected_score


def test_rrf_overlapping_candidate_dominance() -> None:
    """Candidate present in both lists should outrank single-source candidates."""
    dense = ["shared_doc", "dense_only"]
    lexical = ["shared_doc", "lexical_only"]

    fused = reciprocal_rank_fusion(dense, lexical, k=60, top_n=5)

    assert len(fused) == 3
    # shared_doc gets 1/61 + 1/61
    assert fused[0][0] == "shared_doc"
    expected_shared_score = (1.0 / 61) + (1.0 / 61)
    assert pytest.approx(fused[0][1], rel=1e-6) == expected_shared_score

    # dense_only and lexical_only each have 1/62
    expected_single_score = 1.0 / 62
    assert pytest.approx(fused[1][1], rel=1e-6) == expected_single_score
    assert pytest.approx(fused[2][1], rel=1e-6) == expected_single_score
    # Alphabetical tie-breaking: "dense_only" before "lexical_only"
    assert fused[1][0] == "dense_only"
    assert fused[2][0] == "lexical_only"


def test_rrf_completely_disjoint_lists() -> None:
    """Validate fusion behavior when lists share no documents."""
    dense = ["d1", "d2"]
    lexical = ["l1", "l2"]

    fused = reciprocal_rank_fusion(dense, lexical, k=60, top_n=4)

    assert len(fused) == 4
    # d1 and l1 are both rank 1 (1/61)
    assert pytest.approx(fused[0][1], rel=1e-6) == 1.0 / 61
    assert pytest.approx(fused[1][1], rel=1e-6) == 1.0 / 61
    assert fused[0][0] == "d1"
    assert fused[1][0] == "l1"

    # d2 and l2 are both rank 2 (1/62)
    assert pytest.approx(fused[2][1], rel=1e-6) == 1.0 / 62
    assert pytest.approx(fused[3][1], rel=1e-6) == 1.0 / 62
    assert fused[2][0] == "d2"
    assert fused[3][0] == "l2"


def test_rrf_deterministic_tiebreaks() -> None:
    """Validate that identical scores break ties alphabetically by doc_id."""
    dense = ["b_doc", "a_doc"]
    lexical = ["a_doc", "b_doc"]

    fused = reciprocal_rank_fusion(dense, lexical, k=60, top_n=2)

    # Both documents have identical score: 1/61 + 1/62
    assert len(fused) == 2
    assert pytest.approx(fused[0][1], rel=1e-6) == fused[1][1]
    assert fused[0][0] == "a_doc"
    assert fused[1][0] == "b_doc"


def test_rrf_duplicate_handling_within_list() -> None:
    """Duplicate items within the same list should only count their first/highest rank."""
    dense = ["doc1", "doc1", "doc2"]
    lexical = ["doc2"]

    fused = reciprocal_rank_fusion(dense, lexical, k=60, top_n=5)

    # doc1 gets 1/61 (not 1/61 + 1/62 from duplicate)
    # doc2 gets 1/62 (from dense) + 1/61 (from lexical) = 1/62 + 1/61
    # Thus doc2 should rank first!
    assert fused[0][0] == "doc2"
    assert fused[1][0] == "doc1"
    assert pytest.approx(fused[0][1], rel=1e-6) == (1.0 / 62) + (1.0 / 61)
    assert pytest.approx(fused[1][1], rel=1e-6) == 1.0 / 61


def test_rrf_top_n_truncation() -> None:
    """Validate that top_n correctly restricts output length."""
    dense = [f"doc_{i}" for i in range(20)]
    lexical = [f"doc_{i}" for i in range(20)]

    fused = reciprocal_rank_fusion(dense, lexical, k=60, top_n=5)
    assert len(fused) == 5
    assert [doc_id for doc_id, _ in fused] == ["doc_0", "doc_1", "doc_2", "doc_3", "doc_4"]


def test_weighted_rrf_multi_list() -> None:
    """Test weighted RRF with 4 candidate lists and custom weights."""
    from mem_plane.core.rrf import weighted_rrf

    fact_kw = ["f_1", "f_2"]
    fact_vec = ["f_2", "f_3"]
    ep_kw = ["e_1", "f_1"]
    ep_vec = ["e_2"]

    # fact weights 1.0, ep weights 0.5
    fused = weighted_rrf(
        [
            (fact_kw, 1.0),
            (fact_vec, 1.0),
            (ep_kw, 0.5),
            (ep_vec, 0.5),
        ],
        k=60,
        top_n=5,
    )

    # f_1: 1.0/(61) + 0.5/(62) = 0.016393 + 0.008064 = 0.024458
    # f_2: 1.0/(62) + 1.0/(61) = 0.016129 + 0.016393 = 0.032522 -> highest
    # f_3: 1.0/(62) = 0.016129
    # e_1: 0.5/(61) = 0.008196
    # e_2: 0.5/(61) = 0.008196
    assert fused[0][0] == "f_2"
    assert fused[1][0] == "f_1"
    assert fused[2][0] == "f_3"
    # e_1 and e_2 have equal scores; tie broke by id: e_1 < e_2
    assert fused[3][0] == "e_1"
    assert fused[4][0] == "e_2"
    assert len(fused) == 5


def test_weighted_rrf_zero_or_negative_weights() -> None:
    from mem_plane.core.rrf import weighted_rrf

    fused = weighted_rrf(
        [(["docA"], 0.0), (["docB"], -1.0), (["docC"], 1.0)],
        k=60,
        top_n=5,
    )
    assert len(fused) == 1
    assert fused[0][0] == "docC"
