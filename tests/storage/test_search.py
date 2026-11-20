"""Tests for search query building, hybrid retrieval, and fusion (Sections 7.2, 7.3, 7.4)."""

from __future__ import annotations

from pathlib import Path

from mem_plane.config import Settings
from mem_plane.core.models import Fact, FactKind, FactStatus
from mem_plane.storage.protocols import EpisodeChunk, SearchRequest
from mem_plane.storage.sqlite.search import build_fts5_query
from mem_plane.storage.sqlite.store import SQLiteStore


def test_fts5_query_sanitization() -> None:
    # 1. Empty query
    assert build_fts5_query("") == ""
    assert build_fts5_query("   ") == ""
    assert build_fts5_query("***") == ""
    assert build_fts5_query("() : ^ {}") == ""

    # 2. Quotes and parentheses stripped, tokens wrapped in quotes
    q = build_fts5_query('("auth" OR *secret*)')
    assert q == '"auth" OR "OR" OR "secret"'

    # 3. Exact identifiers preserved
    assert build_fts5_query("auth_jwt_v2") == '"auth_jwt_v2"'
    assert build_fts5_query("--no-cov") == '"--no-cov"'
    assert build_fts5_query("src/main.py") == '"src/main.py"'

    # 4. Token limit cap at 24
    long_query = " ".join(f"token{i}" for i in range(35))
    built = build_fts5_query(long_query, max_tokens=24)
    tokens = built.split(" OR ")
    assert len(tokens) == 24
    assert tokens[0] == '"token0"'
    assert tokens[-1] == '"token23"'


def test_exact_identifier_matching(tmp_path: Path) -> None:
    store = SQLiteStore(database=tmp_path / "exact_id.db")
    try:
        f1 = Fact(
            fact_id="f_ident001",
            project_id="p_test",
            kind=FactKind.RULE,
            summary="Always pass --no-cov in fast test runner",
            body="pytest command requires --no-cov to run quickly.",
        )
        f2 = Fact(
            fact_id="f_ident002",
            project_id="p_test",
            kind=FactKind.CONVENTION,
            summary="JWT auth token format is auth_jwt_v2",
            body="Headers should use auth_jwt_v2 scheme.",
        )
        f3 = Fact(
            fact_id="f_ident003",
            project_id="p_test",
            kind=FactKind.NOTE,
            summary="Unrelated note about database indexes",
            body="Index user_id and email.",
        )
        store.create_fact(f1)
        store.create_fact(f2)
        store.create_fact(f3)

        # Match exact identifier --no-cov
        hits1 = store.search(SearchRequest(project_id="p_test", query="--no-cov"))
        assert len(hits1) >= 1
        assert hits1[0].ref == "f_ident001"

        # Match exact identifier auth_jwt_v2
        hits2 = store.search(SearchRequest(project_id="p_test", query="auth_jwt_v2"))
        assert len(hits2) >= 1
        assert hits2[0].ref == "f_ident002"
    finally:
        store.close()


def test_superseded_excluded_by_default(tmp_path: Path) -> None:
    store = SQLiteStore(database=tmp_path / "superseded.db")
    try:
        f_old = Fact(
            fact_id="f_super001",
            project_id="p_test",
            kind=FactKind.DECISION,
            summary="Use Postgres for primary database",
            body="PostgreSQL v15 selected.",
            status=FactStatus.ACTIVE,
        )
        store.create_fact(f_old)

        f_new = Fact(
            fact_id="f_super002",
            project_id="p_test",
            kind=FactKind.DECISION,
            summary="Migrate primary database to SQLite with WAL",
            body="Switched from Postgres to SQLite.",
            supersedes="f_super001",
        )
        store.create_fact(f_new)

        # Default search excludes superseded
        hits = store.search(SearchRequest(project_id="p_test", query="primary database"))
        refs = [h.ref for h in hits]
        assert "f_super002" in refs
        assert "f_super001" not in refs

        # Search with include_superseded=True includes both
        hits_all = store.search(
            SearchRequest(project_id="p_test", query="primary database", include_superseded=True)
        )
        refs_all = [h.ref for h in hits_all]
        assert "f_super002" in refs_all
        assert "f_super001" in refs_all
    finally:
        store.close()


def test_cosine_distance_cutoff_drops_unrelated_vectors(tmp_path: Path) -> None:
    store = SQLiteStore(database=tmp_path / "cutoff.db")
    try:
        f1 = Fact(
            fact_id="f_cut001",
            project_id="p_test",
            kind=FactKind.RULE,
            summary="Authentication requires bcrypt hashing",
            body="Salt rounds must be at least 12.",
        )
        store.create_fact(f1)

        # Manually write vector for f1: orthogonal vector to query
        # Let f1 vector be e1 = [1.0, 0.0, ...]
        v_f1 = [1.0] + [0.0] * 383
        from mem_plane.storage.protocols import PendingItem

        store.write_vectors([(PendingItem("f_cut001", "fact", "p_test", "Auth bcrypt"), v_f1)])

        # Query vector is orthogonal e2 = [0.0, 1.0, 0.0, ...]
        # Cosine distance between e1 and e2 = 1.0 - 0.0 = 1.0 > 0.55 cut-off
        v_query = [0.0, 1.0] + [0.0] * 382

        # Search with non-matching query word so keyword search returns nothing
        hits = store.search(
            SearchRequest(project_id="p_test", query="completely_unrelated_xyz"),
            query_vector=v_query,
        )
        # Vector hit had distance 1.0, which exceeds 0.55, so it should be dropped
        assert len(hits) == 0

        # Now query with vector close to e1: e1 itself -> distance 0.0 <= 0.55
        hits_close = store.search(
            SearchRequest(project_id="p_test", query="completely_unrelated_xyz"),
            query_vector=v_f1,
        )
        assert len(hits_close) == 1
        assert hits_close[0].ref == "f_cut001"
    finally:
        store.close()


def test_weighted_rrf_and_deterministic_output(tmp_path: Path) -> None:
    settings = Settings(
        weight_fact_keyword=1.0,
        weight_fact_vector=1.0,
        weight_episode_keyword=0.5,
        weight_episode_vector=0.5,
    )
    store = SQLiteStore(database=tmp_path / "deterministic.db", settings=settings)
    try:
        f1 = Fact(
            fact_id="f_det001",
            project_id="p_det",
            kind=FactKind.DECISION,
            summary="JWT expiry TTL set to 15m",
            body="Access tokens 15m, refresh tokens 7d.",
        )
        store.create_fact(f1)

        chunk = EpisodeChunk(
            chunk_id="e_det001",
            project_id="p_det",
            session_id="s_det",
            seq=1,
            segment=0,
            start_ts=None,
            end_ts=None,
            text="User asked about JWT token expiration rules.",
            token_estimate=10,
            created_at=f1.created_at,
        )
        store.append_chunks([chunk])

        # Run identical search twice
        req = SearchRequest(project_id="p_det", query="JWT expiry token", top_k=5)
        hits1 = store.search(req)
        hits2 = store.search(req)

        assert len(hits1) == len(hits2)
        assert [h.ref for h in hits1] == [h.ref for h in hits2]
        assert [h.score for h in hits1] == [h.score for h in hits2]

        # Curated fact should outrank raw episode chunk due to higher weight
        assert hits1[0].ref == "f_det001"
        assert hits1[1].ref == "e_det001"
    finally:
        store.close()
