"""Integration and unit tests for SQLiteBackend hybrid search and storage."""

from collections.abc import AsyncGenerator

import pytest

from mem_plane.core.models import FactEntity, HydrationBundle
from mem_plane.pipeline.embeddings import DummyEmbeddingClient
from mem_plane.storage.sqlite.backend import SQLiteBackend


@pytest.fixture
async def sqlite_backend() -> AsyncGenerator[SQLiteBackend, None]:
    """Fixture providing an initialized in-memory SQLiteBackend."""
    backend = SQLiteBackend(database=":memory:", dimension=384)
    await backend.initialize()
    try:
        yield backend
    finally:
        await backend.close()


@pytest.mark.asyncio
async def test_fact_crud_lifecycle(sqlite_backend: SQLiteBackend) -> None:
    """Verify O(1) fact persistence and retrieval with provenance metadata."""
    fact = FactEntity(
        fact_id="fact-core-001",
        tenant_id="tenant-alpha",
        session_id="session-42",
        summary="JWT token expiration set to 15 minutes",
        raw_content="In auth_service.py, ACCESS_TOKEN_EXPIRE_MINUTES is configured to 15.",
        source_type="codebase",
        provenance_metadata={
            "author": "alice",
            "commit_sha": "a1b2c3d4",
            "path": "auth_service.py",
        },
    )

    await sqlite_backend.save_fact(fact)
    retrieved = await sqlite_backend.get_fact_by_id("fact-core-001")

    assert retrieved is not None
    assert retrieved.fact_id == "fact-core-001"
    assert retrieved.tenant_id == "tenant-alpha"
    assert retrieved.session_id == "session-42"
    assert retrieved.summary == "JWT token expiration set to 15 minutes"
    assert retrieved.raw_content == fact.raw_content
    assert retrieved.source_type == "codebase"
    assert retrieved.provenance_metadata["author"] == "alice"
    assert retrieved.provenance_metadata["commit_sha"] == "a1b2c3d4"

    # Non-existent fact returns None
    assert await sqlite_backend.get_fact_by_id("non-existent") is None


@pytest.mark.asyncio
async def test_hydration_profile_persistence(sqlite_backend: SQLiteBackend) -> None:
    """Verify hydration data saving and deterministic fetching."""
    bundle = HydrationBundle(
        tenant_rules=["Always use Python 3.12 syntax", "No floating point score calibrations"],
        user_preferences={"theme": "dark", "output_format": "concise"},
        session_state={"active_branch": "feature/rrf"},
    )

    # Empty profile returns default bundle
    empty = await sqlite_backend.get_hydration_data("t1", "u1", "s1")
    assert empty.tenant_rules == []

    await sqlite_backend.save_hydration_profile("t1", "u1", "s1", bundle)
    fetched = await sqlite_backend.get_hydration_data("t1", "u1", "s1")

    assert fetched.tenant_rules == bundle.tenant_rules
    assert fetched.user_preferences == bundle.user_preferences
    assert fetched.session_state == bundle.session_state


@pytest.mark.asyncio
async def test_tool_trace_out_of_band_storage(sqlite_backend: SQLiteBackend) -> None:
    """Verify tool execution trace saving and dereferencing."""
    raw_stack_trace = (
        "Traceback (most recent call last):\n"
        "  File 'main.py', line 12, in <module>\n"
        "ValueError: invalid token format\n"
    )

    await sqlite_backend.save_tool_trace("trace-999", raw_stack_trace)
    retrieved_trace = await sqlite_backend.get_tool_trace("trace-999")

    assert retrieved_trace == raw_stack_trace
    assert await sqlite_backend.get_tool_trace("non-existent-trace") is None


@pytest.mark.asyncio
async def test_hybrid_search_with_technical_keywords(
    sqlite_backend: SQLiteBackend, dummy_embedding_client: DummyEmbeddingClient
) -> None:
    """Verify that exact technical symbols (CLI flags, error codes, identifiers) rank properly."""
    facts = [
        FactEntity(
            fact_id="fact-flag",
            tenant_id="tenant-1",
            summary="Pytest configuration disables coverage check with --no-cov",
            raw_content="Run pytest -s -v --no-cov when running local unit tests without coverage penalty.",
            source_type="codebase",
            provenance_metadata={"source_path": "scripts/test.sh"},
        ),
        FactEntity(
            fact_id="fact-signal",
            tenant_id="tenant-1",
            summary="Process crashed with SIGSEGV 139 due to memory overflow",
            raw_content="Kernel log indicates core dump occurred: process killed with SIGSEGV 139 in worker thread.",
            source_type="tool_output",
            provenance_metadata={"source_path": "logs/worker.log"},
        ),
        FactEntity(
            fact_id="fact-jwt",
            tenant_id="tenant-1",
            summary="Token validator uses auth_jwt_v2 scheme",
            raw_content="All microservice requests must include auth_jwt_v2 authorization headers.",
            source_type="codebase",
            provenance_metadata={"source_path": "auth/tokens.py"},
        ),
        FactEntity(
            fact_id="fact-generic",
            tenant_id="tenant-1",
            summary="General notes on code styling and testing guidelines",
            raw_content="Ensure all functions have type signatures and docstrings.",
            source_type="rule",
            provenance_metadata={"source_path": "docs/style.md"},
        ),
    ]

    for f in facts:
        vec = await dummy_embedding_client.embed_text(f.summary + " " + f.raw_content)
        await sqlite_backend.save_fact_with_embedding(f, vec)

    # 1. Search for exact flag "--no-cov"
    q_vec1 = await dummy_embedding_client.embed_text("how to run tests without coverage")
    hits1 = await sqlite_backend.hybrid_search("--no-cov", q_vec1, top_k=2)
    assert len(hits1) > 0
    assert hits1[0].fact_id == "fact-flag"
    assert hits1[0].source_path == "scripts/test.sh"

    # 2. Search for exact crash code "SIGSEGV 139"
    q_vec2 = await dummy_embedding_client.embed_text("fatal crash signal error")
    hits2 = await sqlite_backend.hybrid_search("SIGSEGV 139", q_vec2, top_k=2)
    assert len(hits2) > 0
    assert hits2[0].fact_id == "fact-signal"
    assert hits2[0].source_path == "logs/worker.log"

    # 3. Search for exact token scheme "auth_jwt_v2"
    q_vec3 = await dummy_embedding_client.embed_text("authentication token verification")
    hits3 = await sqlite_backend.hybrid_search("auth_jwt_v2", q_vec3, top_k=2)
    assert len(hits3) > 0
    assert hits3[0].fact_id == "fact-jwt"
    assert hits3[0].source_path == "auth/tokens.py"


@pytest.mark.asyncio
async def test_hybrid_search_pure_semantic_match(
    sqlite_backend: SQLiteBackend, dummy_embedding_client: DummyEmbeddingClient
) -> None:
    """Verify that semantic vector search succeeds even when lexical query has zero word overlap."""
    target_text = "Database connection pool settings and maximum pool size"
    target_vec = await dummy_embedding_client.embed_text(target_text)

    fact = FactEntity(
        fact_id="fact-pool",
        tenant_id="tenant-1",
        summary="Database pool configuration",
        raw_content=target_text,
        source_type="codebase",
    )
    await sqlite_backend.save_fact_with_embedding(fact, target_vec)

    # Completely different lexical query that won't match FTS, but vector is identical
    hits = await sqlite_backend.hybrid_search("unrelated_xyz_lexical_token", target_vec, top_k=1)
    assert len(hits) == 1
    assert hits[0].fact_id == "fact-pool"
    assert hits[0].score > 0
