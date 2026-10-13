"""Unit and contract tests for production backends (PostgreSQL and OpenSearch) with zero infra."""

from datetime import UTC, datetime
from types import TracebackType
from typing import Any, cast

import pytest

from mem_plane.config import Settings, get_storage_and_index
from mem_plane.core.models import FactEntity, HydrationBundle
from mem_plane.storage.production.opensearch import OpenSearchBackend
from mem_plane.storage.production.postgres import PostgresBackend
from mem_plane.storage.protocols import MetadataStorage, SearchIndex


class MockAsyncPGConnection:
    """In-memory mock representing an asyncpg connection."""

    def __init__(self, state: dict[str, Any]) -> None:
        self.state = state

    async def execute(self, query: str, *args: Any) -> str:
        q = query.strip()
        if "CREATE TABLE" in q:
            return "CREATE TABLE"
        elif "INSERT INTO facts" in q:
            # args: (fact_id, tenant_id, session_id, summary, raw_content, source_type, meta_json, created_at)
            self.state["facts"][args[0]] = {
                "fact_id": args[0],
                "tenant_id": args[1],
                "session_id": args[2],
                "summary": args[3],
                "raw_content": args[4],
                "source_type": args[5],
                "provenance_metadata": args[6],
                "created_at": args[7],
            }
            return "INSERT 1"
        elif "INSERT INTO hydration_profiles" in q:
            self.state["hydration_profiles"][args[0]] = {
                "profile_id": args[0],
                "tenant_id": args[1],
                "user_id": args[2],
                "session_id": args[3],
                "tenant_rules": args[4],
                "user_preferences": args[5],
                "session_state": args[6],
                "updated_at": args[7],
            }
            return "INSERT 1"
        elif "INSERT INTO tool_traces" in q:
            self.state["tool_traces"][args[0]] = {
                "trace_id": args[0],
                "raw_trace": args[1],
                "created_at": args[2],
            }
            return "INSERT 1"
        return "OK"

    async def fetchrow(self, query: str, *args: Any) -> dict[str, Any] | None:
        q = query.strip()
        if "FROM facts" in q:
            fact_id = args[0]
            return cast(dict[str, Any] | None, self.state["facts"].get(fact_id))
        elif "FROM hydration_profiles" in q:
            profile_id = args[0]
            return cast(dict[str, Any] | None, self.state["hydration_profiles"].get(profile_id))
        elif "FROM tool_traces" in q:
            trace_id = args[0]
            return cast(dict[str, Any] | None, self.state["tool_traces"].get(trace_id))
        return None


class MockAsyncPGPool:
    """Mock asyncpg connection pool for zero-infrastructure testing."""

    def __init__(self) -> None:
        self.state: dict[str, Any] = {
            "facts": {},
            "hydration_profiles": {},
            "tool_traces": {},
        }

    def acquire(self) -> Any:
        conn = MockAsyncPGConnection(self.state)

        class _Context:
            async def __aenter__(self) -> MockAsyncPGConnection:
                return conn

            async def __aexit__(
                self,
                exc_type: type[BaseException] | None,
                exc_val: BaseException | None,
                exc_tb: TracebackType | None,
            ) -> None:
                pass

        return _Context()

    async def close(self) -> None:
        pass


class MockAsyncOpenSearch:
    """In-memory mock representing OpenSearch client with Lucene HNSW k-NN and BM25 search."""

    def __init__(self) -> None:
        self.indices_created: set[str] = set()
        self.documents: dict[str, dict[str, Any]] = {}

        class _Indices:
            def __init__(self, outer: "MockAsyncOpenSearch") -> None:
                self.outer = outer

            async def exists(self, index: str) -> bool:
                return index in self.outer.indices_created

            async def create(self, index: str, body: dict[str, Any]) -> None:
                self.outer.indices_created.add(index)

        self.indices = _Indices(self)

    async def index(
        self, index: str, id: str, body: dict[str, Any], refresh: bool = True
    ) -> dict[str, Any]:
        self.documents[id] = body
        return {"result": "created", "_id": id}

    async def search(self, index: str, body: dict[str, Any]) -> dict[str, Any]:
        # Check if knn search or lexical query
        hits = []
        if "knn" in body.get("query", {}):
            for doc_id, doc in self.documents.items():
                hits.append({"_id": doc_id, "_source": doc, "_score": 0.95})
        elif "multi_match" in body.get("query", {}):
            q_str = body["query"]["multi_match"]["query"].lower()
            for doc_id, doc in self.documents.items():
                text_to_search = (doc.get("summary", "") + " " + doc.get("content", "")).lower()
                if any(word in text_to_search for word in q_str.split()):
                    hits.append({"_id": doc_id, "_source": doc, "_score": 1.5})

        return {"hits": {"hits": hits}}

    async def close(self) -> None:
        pass


@pytest.mark.asyncio
async def test_postgres_backend_crud_contract() -> None:
    """Verify PostgresBackend implements MetadataStorage protocol with identical semantics."""
    pool = MockAsyncPGPool()
    backend = PostgresBackend(pool=pool)
    await backend.initialize()

    # Assert protocol compliance
    assert isinstance(backend, MetadataStorage)

    # 1. Fact CRUD
    fact = FactEntity(
        fact_id="fact-pg-1",
        tenant_id="tenant-prod",
        summary="Postgres connection pool max_size=20",
        raw_content="Configured asyncpg pool with min_size=5, max_size=20.",
        source_type="codebase",
        provenance_metadata={"author": "carol", "commit_sha": "1234abcd"},
        created_at=datetime.now(UTC),
    )
    await backend.save_fact(fact)
    retrieved = await backend.get_fact_by_id("fact-pg-1")
    assert retrieved is not None
    assert retrieved.fact_id == "fact-pg-1"
    assert retrieved.summary == fact.summary
    assert retrieved.provenance_metadata["author"] == "carol"

    # 2. Hydration CRUD
    bundle = HydrationBundle(
        tenant_rules=["Tenant rule A"],
        user_preferences={"lang": "en"},
        session_state={"mode": "fast"},
    )
    await backend.save_hydration_profile("tenant-prod", "user-1", "sess-1", bundle)
    fetched_bundle = await backend.get_hydration_data("tenant-prod", "user-1", "sess-1")
    assert fetched_bundle.tenant_rules == ["Tenant rule A"]
    assert fetched_bundle.user_preferences == {"lang": "en"}

    # 3. Tool Traces
    await backend.save_tool_trace("trace-pg-1", "Test error stacktrace in worker")
    trace = await backend.get_tool_trace("trace-pg-1")
    assert trace == "Test error stacktrace in worker"


@pytest.mark.asyncio
async def test_opensearch_backend_hybrid_search_contract() -> None:
    """Verify OpenSearchBackend implements SearchIndex protocol with BM25 + k-NN + RRF."""
    client = MockAsyncOpenSearch()
    backend = OpenSearchBackend(client=client, dimension=384)
    await backend.initialize()

    # Assert protocol compliance
    assert isinstance(backend, SearchIndex)

    # Index two documents
    vec_dummy = [0.1] * 384
    await backend.index_vector_and_tokens(
        fact_id="doc-auth",
        summary="Authentication using OAuth2 PKCE",
        content="All frontend apps must use OAuth2 authorization code with PKCE.",
        vector=vec_dummy,
        source_path="auth/pkce.py",
    )
    await backend.index_vector_and_tokens(
        fact_id="doc-db",
        summary="Database replicas configured with streaming replication",
        content="PostgreSQL read replicas configured in multi-region setup.",
        vector=vec_dummy,
        source_path="db/replica.py",
    )

    # Hybrid search
    hits = await backend.hybrid_search(
        query="OAuth2 PKCE authorization",
        query_vector=vec_dummy,
        top_k=2,
    )
    assert len(hits) > 0
    # doc-auth matched both BM25 and vector, so it should rank first
    assert hits[0].fact_id == "doc-auth"
    assert hits[0].source_path == "auth/pkce.py"
    assert hits[0].score > 0


@pytest.mark.asyncio
async def test_config_factory_switching() -> None:
    """Verify config factory correctly spins up requested storage backend."""
    # 1. SQLite backend (default)
    settings_sqlite = Settings(storage_backend="sqlite", sqlite_db_path=":memory:")
    storage_sqlite, search_sqlite = await get_storage_and_index(settings_sqlite)
    assert isinstance(storage_sqlite, MetadataStorage)
    assert isinstance(search_sqlite, SearchIndex)

    # 2. Invalid backend raises ValueError
    settings_invalid = Settings.model_construct(storage_backend="invalid_choice")
    with pytest.raises(ValueError, match="Unsupported storage backend"):
        await get_storage_and_index(settings_invalid)
