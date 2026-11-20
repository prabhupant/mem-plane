"""Tests for search result formatting (Section 7.5)."""

from __future__ import annotations

from datetime import datetime

from mem_plane.core.models import SearchHit
from mem_plane.server.formatting import format_search_results, truncate_summary


def test_format_search_results_structure() -> None:
    dt1 = datetime(2026, 10, 2)
    dt2 = datetime(2026, 9, 11)
    dt3 = datetime(2026, 10, 2)

    hits = [
        SearchHit(
            ref="f_3a91c0d2e4b7",
            ref_type="fact",
            summary="JWT access tokens expire after 15 minutes; refresh tokens after 7 days.",
            score=0.032,
            kind="decision",
            stale=False,
            created_at=dt1,
        ),
        SearchHit(
            ref="f_77b0e1aa9c13",
            ref_type="fact",
            summary="Never read JWT secrets from env in tests.",
            score=0.024,
            kind="rule",
            stale=True,
            stale_reason="auth/config.py changed",
            created_at=dt2,
        ),
        SearchHit(
            ref="e_0c4d9f2b1a6e",
            ref_type="episode",
            summary="...discussed moving token TTLs into settings.py, decided against because...",
            score=0.018,
            kind=None,
            stale=False,
            created_at=dt3,
        ),
    ]

    out = format_search_results(hits, query="jwt expiry", project_id="payments-api")

    expected_lines = [
        '3 results for "jwt expiry" (project: payments-api)',
        "f_3a91c0d2e4b7 [decision] JWT access tokens expire after 15 minutes; refresh tokens after 7 days. (2026-10-02)",
        "f_77b0e1aa9c13 [rule] [STALE: auth/config.py changed] Never read JWT secrets from env in tests. (2026-09-11)",
        "e_0c4d9f2b1a6e [episode] ...discussed moving token TTLs into settings.py, decided against because... (2026-10-02)",
        "Use get_memory(ref) for full detail.",
    ]
    assert out == "\n".join(expected_lines)


def test_format_search_results_byte_identical() -> None:
    dt = datetime(2026, 5, 20)
    hits = [
        SearchHit(
            ref="f_test001",
            ref_type="fact",
            summary="Determinism check",
            score=0.05,
            kind="convention",
            created_at=dt,
        )
    ]
    out1 = format_search_results(hits, query="determinism", project_id="p1")
    out2 = format_search_results(hits, query="determinism", project_id="p1")
    assert out1.encode("utf-8") == out2.encode("utf-8")


def test_truncate_summary() -> None:
    short_text = "Short summary"
    assert truncate_summary(short_text, max_chars=20) == "Short summary"

    long_text = "a" * 250
    truncated = truncate_summary(long_text, max_chars=200)
    assert len(truncated) == 200
    assert truncated.endswith("…")
