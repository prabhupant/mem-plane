"""SQLite storage engine implementing MetadataStorage and SearchIndex protocols."""

import json
import re
from datetime import UTC, datetime
from pathlib import Path
from types import TracebackType
from typing import Self

import aiosqlite
import sqlite_vec

from mem_plane.core.models import FactEntity, HydrationBundle, SurrogateHit
from mem_plane.core.rrf import reciprocal_rank_fusion
from mem_plane.storage.protocols import MetadataStorage, SearchIndex
from mem_plane.storage.sqlite.connection import create_connection


class SQLiteBackend(MetadataStorage, SearchIndex):
    """Hybrid SQLite backend supporting FTS5 lexical search and sqlite-vec vector k-NN."""

    def __init__(
        self,
        database: str | Path = ":memory:",
        dimension: int = 384,
        connection: aiosqlite.Connection | None = None,
    ) -> None:
        self.database = database
        self.dimension = dimension
        self._conn = connection
        self._owns_connection = connection is None

    async def initialize(self) -> None:
        """Create database tables, FTS5 virtual table, and sqlite-vec virtual table."""
        if self._conn is None:
            self._conn = await create_connection(self.database)

        await self._conn.execute(
            """
            CREATE TABLE IF NOT EXISTS facts (
                fact_id TEXT PRIMARY KEY,
                tenant_id TEXT NOT NULL,
                session_id TEXT,
                summary TEXT NOT NULL,
                raw_content TEXT NOT NULL,
                source_type TEXT NOT NULL,
                provenance_metadata TEXT NOT NULL,
                created_at TEXT NOT NULL
            );
            """
        )

        await self._conn.execute(
            """
            CREATE VIRTUAL TABLE IF NOT EXISTS facts_fts USING fts5(
                fact_id UNINDEXED,
                summary,
                content,
                tokenize = 'porter unicode61'
            );
            """
        )

        await self._conn.execute(
            f"""
            CREATE VIRTUAL TABLE IF NOT EXISTS facts_vec USING vec0(
                fact_id TEXT PRIMARY KEY,
                embedding FLOAT[{self.dimension}] DISTANCE_METRIC=cosine
            );
            """
        )

        await self._conn.execute(
            """
            CREATE TABLE IF NOT EXISTS hydration_profiles (
                profile_id TEXT PRIMARY KEY,
                tenant_id TEXT NOT NULL,
                user_id TEXT NOT NULL,
                session_id TEXT NOT NULL,
                tenant_rules TEXT NOT NULL,
                user_preferences TEXT NOT NULL,
                session_state TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
            """
        )

        await self._conn.execute(
            """
            CREATE TABLE IF NOT EXISTS tool_traces (
                trace_id TEXT PRIMARY KEY,
                raw_trace TEXT NOT NULL,
                created_at TEXT NOT NULL
            );
            """
        )

        await self._conn.commit()

    async def close(self) -> None:
        """Close connection if owned by this backend instance."""
        if self._conn is not None and self._owns_connection:
            await self._conn.close()
            self._conn = None

    async def __aenter__(self) -> Self:
        await self.initialize()
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: TracebackType | None,
    ) -> None:
        await self.close()

    def _get_conn(self) -> aiosqlite.Connection:
        if self._conn is None:
            raise RuntimeError("SQLiteBackend is not initialized. Call initialize() first.")
        return self._conn

    # -------------------------------------------------------------------------
    # MetadataStorage Protocol Methods
    # -------------------------------------------------------------------------

    async def save_fact(self, fact: FactEntity) -> None:
        """Persist a FactEntity including raw content and provenance metadata."""
        conn = self._get_conn()
        created_at_str = fact.created_at.isoformat()
        metadata_str = json.dumps(fact.provenance_metadata)

        await conn.execute(
            """
            INSERT OR REPLACE INTO facts (
                fact_id, tenant_id, session_id, summary, raw_content, source_type,
                provenance_metadata, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?);
            """,
            (
                fact.fact_id,
                fact.tenant_id,
                fact.session_id,
                fact.summary,
                fact.raw_content,
                fact.source_type,
                metadata_str,
                created_at_str,
            ),
        )
        await conn.commit()

    async def get_fact_by_id(self, fact_id: str) -> FactEntity | None:
        """Retrieve a FactEntity by ID with O(1) performance."""
        conn = self._get_conn()
        async with conn.execute(
            """
            SELECT fact_id, tenant_id, session_id, summary, raw_content, source_type,
                   provenance_metadata, created_at
            FROM facts WHERE fact_id = ?;
            """,
            (fact_id,),
        ) as cursor:
            row = await cursor.fetchone()
            if row is None:
                return None

            return FactEntity(
                fact_id=row[0],
                tenant_id=row[1],
                session_id=row[2],
                summary=row[3],
                raw_content=row[4],
                source_type=row[5],
                provenance_metadata=json.loads(row[6]),
                created_at=datetime.fromisoformat(row[7]),
            )

    async def save_hydration_profile(
        self, tenant_id: str, user_id: str, session_id: str, bundle: HydrationBundle
    ) -> None:
        """Persist hydration rules, preferences, and state for a tenant/user/session."""
        conn = self._get_conn()
        profile_id = f"{tenant_id}:{user_id}:{session_id}"
        now_str = datetime.now(UTC).isoformat()

        await conn.execute(
            """
            INSERT OR REPLACE INTO hydration_profiles (
                profile_id, tenant_id, user_id, session_id,
                tenant_rules, user_preferences, session_state, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?);
            """,
            (
                profile_id,
                tenant_id,
                user_id,
                session_id,
                json.dumps(bundle.tenant_rules),
                json.dumps(bundle.user_preferences),
                json.dumps(bundle.session_state),
                now_str,
            ),
        )
        await conn.commit()

    async def get_hydration_data(
        self, tenant_id: str, user_id: str, session_id: str
    ) -> HydrationBundle:
        """Fetch deterministic hydration data."""
        conn = self._get_conn()
        profile_id = f"{tenant_id}:{user_id}:{session_id}"

        async with conn.execute(
            """
            SELECT tenant_rules, user_preferences, session_state
            FROM hydration_profiles WHERE profile_id = ?;
            """,
            (profile_id,),
        ) as cursor:
            row = await cursor.fetchone()
            if row is None:
                return HydrationBundle()

            return HydrationBundle(
                tenant_rules=json.loads(row[0]),
                user_preferences=json.loads(row[1]),
                session_state=json.loads(row[2]),
            )

    async def save_tool_trace(self, trace_id: str, raw_trace: str) -> None:
        """Persist an uncompressed tool trace out-of-band."""
        conn = self._get_conn()
        now_str = datetime.now(UTC).isoformat()

        await conn.execute(
            """
            INSERT OR REPLACE INTO tool_traces (trace_id, raw_trace, created_at)
            VALUES (?, ?, ?);
            """,
            (trace_id, raw_trace, now_str),
        )
        await conn.commit()

    async def get_tool_trace(self, trace_id: str) -> str | None:
        """Retrieve an uncompressed tool trace by ID."""
        conn = self._get_conn()
        async with conn.execute(
            "SELECT raw_trace FROM tool_traces WHERE trace_id = ?;", (trace_id,)
        ) as cursor:
            row = await cursor.fetchone()
            if row is None:
                return None
            return str(row[0])

    # -------------------------------------------------------------------------
    # SearchIndex Protocol Methods
    # -------------------------------------------------------------------------

    async def index_vector_and_tokens(
        self, fact_id: str, summary: str, content: str, vector: list[float]
    ) -> None:
        """Index a fact in both FTS5 lexical index and sqlite-vec vector index."""
        conn = self._get_conn()

        # Update FTS5 virtual table
        await conn.execute("DELETE FROM facts_fts WHERE fact_id = ?;", (fact_id,))
        await conn.execute(
            "INSERT INTO facts_fts (fact_id, summary, content) VALUES (?, ?, ?);",
            (fact_id, summary, content),
        )

        # Update vec0 virtual table
        serialized_vector = sqlite_vec.serialize_float32(vector)
        await conn.execute(
            "INSERT OR REPLACE INTO facts_vec (fact_id, embedding) VALUES (?, ?);",
            (fact_id, serialized_vector),
        )
        await conn.commit()

    async def save_fact_with_embedding(self, fact: FactEntity, vector: list[float]) -> None:
        """Convenience method to save fact metadata and index search tokens/vector atomically."""
        await self.save_fact(fact)
        await self.index_vector_and_tokens(fact.fact_id, fact.summary, fact.raw_content, vector)

    def _sanitize_fts5_query(self, query: str) -> str:
        """Sanitize query into safe FTS5 token clauses joined by OR."""
        clean = query.strip()
        if not clean:
            return ""
        tokens = [t for t in re.findall(r'[^\s"()]+', clean) if t.strip()]
        if not tokens:
            return ""
        return " OR ".join(f'"{t.replace('"', "")}"' for t in tokens)

    async def hybrid_search(
        self, query: str, query_vector: list[float], top_k: int = 5
    ) -> list[SurrogateHit]:
        """Perform hybrid lexical and vector search fused with Reciprocal Rank Fusion."""
        if top_k <= 0:
            return []

        conn = self._get_conn()
        candidate_limit = max(top_k * 3, 20)

        # 1. Lexical retrieval via FTS5
        lexical_ranks: list[str] = []
        sanitized_query = self._sanitize_fts5_query(query)
        if sanitized_query:
            try:
                async with conn.execute(
                    """
                    SELECT fact_id FROM facts_fts
                    WHERE facts_fts MATCH ?
                    ORDER BY rank
                    LIMIT ?;
                    """,
                    (sanitized_query, candidate_limit),
                ) as cursor:
                    rows = await cursor.fetchall()
                    lexical_ranks = [str(r[0]) for r in rows]
            except (aiosqlite.Error, sqlite_vec.Error, RuntimeError):
                lexical_ranks = []

        # 2. Dense retrieval via sqlite-vec
        dense_ranks: list[str] = []
        if query_vector:
            try:
                serialized_query = sqlite_vec.serialize_float32(query_vector)
                async with conn.execute(
                    """
                    SELECT fact_id FROM facts_vec
                    WHERE embedding MATCH ? AND k = ?;
                    """,
                    (serialized_query, candidate_limit),
                ) as cursor:
                    rows = await cursor.fetchall()
                    dense_ranks = [str(r[0]) for r in rows]
            except (aiosqlite.Error, sqlite_vec.Error, RuntimeError):
                dense_ranks = []

        # 3. Reciprocal Rank Fusion
        fused = reciprocal_rank_fusion(
            dense_ranks=dense_ranks,
            lexical_ranks=lexical_ranks,
            k=60,
            top_n=top_k,
        )

        if not fused:
            return []

        # 4. Hydrate surrogate metadata
        fused_ids = [doc_id for doc_id, _ in fused]
        placeholders = ",".join("?" for _ in fused_ids)
        fact_meta: dict[str, tuple[str, str | None]] = {}

        async with conn.execute(
            f"""
            SELECT fact_id, summary, provenance_metadata
            FROM facts
            WHERE fact_id IN ({placeholders});
            """,
            fused_ids,
        ) as cursor:
            rows = await cursor.fetchall()
            for r in rows:
                fid = str(r[0])
                summary = str(r[1])
                try:
                    meta = json.loads(r[2])
                    source_path = meta.get("source_path") or meta.get("path")
                except (json.JSONDecodeError, AttributeError):
                    source_path = None
                fact_meta[fid] = (summary, source_path)

        surrogate_hits: list[SurrogateHit] = []
        for doc_id, score in fused:
            if doc_id in fact_meta:
                summary, source_path = fact_meta[doc_id]
            else:
                summary = f"Fact {doc_id}"
                source_path = None

            surrogate_hits.append(
                SurrogateHit(
                    fact_id=doc_id,
                    summary=summary,
                    score=score,
                    source_path=source_path,
                    token_estimate=30,
                )
            )

        return surrogate_hits
