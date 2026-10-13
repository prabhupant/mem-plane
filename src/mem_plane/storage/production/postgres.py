"""PostgreSQL transactional document and trace storage backend."""

import json
from datetime import UTC, datetime
from types import TracebackType
from typing import Any, Self

import asyncpg

from mem_plane.core.models import FactEntity, HydrationBundle
from mem_plane.storage.protocols import MetadataStorage


class PostgresBackend(MetadataStorage):
    """PostgreSQL storage backend implementing MetadataStorage protocol."""

    def __init__(
        self,
        dsn: str = "postgresql://postgres:postgres@localhost:5432/mem_plane",
        pool: Any | None = None,
    ) -> None:
        self.dsn = dsn
        self._pool = pool
        self._owns_pool = pool is None

    async def initialize(self) -> None:
        """Initialize connection pool and create production relational tables."""
        if self._pool is None:
            self._pool = await asyncpg.create_pool(self.dsn)

        async with self._pool.acquire() as conn:
            await conn.execute(
                """
                CREATE TABLE IF NOT EXISTS facts (
                    fact_id TEXT PRIMARY KEY,
                    tenant_id TEXT NOT NULL,
                    session_id TEXT,
                    summary TEXT NOT NULL,
                    raw_content TEXT NOT NULL,
                    source_type TEXT NOT NULL,
                    provenance_metadata JSONB NOT NULL,
                    created_at TIMESTAMPTZ NOT NULL
                );

                CREATE TABLE IF NOT EXISTS hydration_profiles (
                    profile_id TEXT PRIMARY KEY,
                    tenant_id TEXT NOT NULL,
                    user_id TEXT NOT NULL,
                    session_id TEXT NOT NULL,
                    tenant_rules JSONB NOT NULL,
                    user_preferences JSONB NOT NULL,
                    session_state JSONB NOT NULL,
                    updated_at TIMESTAMPTZ NOT NULL
                );

                CREATE TABLE IF NOT EXISTS tool_traces (
                    trace_id TEXT PRIMARY KEY,
                    raw_trace TEXT NOT NULL,
                    created_at TIMESTAMPTZ NOT NULL
                );
                """
            )

    async def close(self) -> None:
        """Close connection pool if owned by this instance."""
        if self._pool is not None and self._owns_pool:
            await self._pool.close()
            self._pool = None

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

    def _get_pool(self) -> Any:
        if self._pool is None:
            raise RuntimeError("PostgresBackend is not initialized. Call initialize() first.")
        return self._pool

    async def save_fact(self, fact: FactEntity) -> None:
        """Persist a FactEntity into the facts table."""
        pool = self._get_pool()
        meta_json = json.dumps(fact.provenance_metadata)

        async with pool.acquire() as conn:
            await conn.execute(
                """
                INSERT INTO facts (
                    fact_id, tenant_id, session_id, summary, raw_content, source_type,
                    provenance_metadata, created_at
                ) VALUES ($1, $2, $3, $4, $5, $6, $7::jsonb, $8)
                ON CONFLICT (fact_id) DO UPDATE SET
                    tenant_id = EXCLUDED.tenant_id,
                    session_id = EXCLUDED.session_id,
                    summary = EXCLUDED.summary,
                    raw_content = EXCLUDED.raw_content,
                    source_type = EXCLUDED.source_type,
                    provenance_metadata = EXCLUDED.provenance_metadata,
                    created_at = EXCLUDED.created_at;
                """,
                fact.fact_id,
                fact.tenant_id,
                fact.session_id,
                fact.summary,
                fact.raw_content,
                fact.source_type,
                meta_json,
                fact.created_at,
            )

    async def get_fact_by_id(self, fact_id: str) -> FactEntity | None:
        """Retrieve a FactEntity by ID with O(1) performance."""
        pool = self._get_pool()

        async with pool.acquire() as conn:
            row = await conn.fetchrow(
                """
                SELECT fact_id, tenant_id, session_id, summary, raw_content, source_type,
                       provenance_metadata, created_at
                FROM facts WHERE fact_id = $1;
                """,
                fact_id,
            )
            if row is None:
                return None

            raw_meta = row["provenance_metadata"]
            meta_dict = json.loads(raw_meta) if isinstance(raw_meta, str) else dict(raw_meta)

            return FactEntity(
                fact_id=row["fact_id"],
                tenant_id=row["tenant_id"],
                session_id=row["session_id"],
                summary=row["summary"],
                raw_content=row["raw_content"],
                source_type=row["source_type"],
                provenance_metadata=meta_dict,
                created_at=row["created_at"],
            )

    async def save_hydration_profile(
        self, tenant_id: str, user_id: str, session_id: str, bundle: HydrationBundle
    ) -> None:
        """Persist hydration profile data."""
        pool = self._get_pool()
        profile_id = f"{tenant_id}:{user_id}:{session_id}"
        now = datetime.now(UTC)

        async with pool.acquire() as conn:
            await conn.execute(
                """
                INSERT INTO hydration_profiles (
                    profile_id, tenant_id, user_id, session_id,
                    tenant_rules, user_preferences, session_state, updated_at
                ) VALUES ($1, $2, $3, $4, $5::jsonb, $6::jsonb, $7::jsonb, $8)
                ON CONFLICT (profile_id) DO UPDATE SET
                    tenant_rules = EXCLUDED.tenant_rules,
                    user_preferences = EXCLUDED.user_preferences,
                    session_state = EXCLUDED.session_state,
                    updated_at = EXCLUDED.updated_at;
                """,
                profile_id,
                tenant_id,
                user_id,
                session_id,
                json.dumps(bundle.tenant_rules),
                json.dumps(bundle.user_preferences),
                json.dumps(bundle.session_state),
                now,
            )

    async def get_hydration_data(
        self, tenant_id: str, user_id: str, session_id: str
    ) -> HydrationBundle:
        """Retrieve hydration data for a given tenant, user, and session."""
        pool = self._get_pool()
        profile_id = f"{tenant_id}:{user_id}:{session_id}"

        async with pool.acquire() as conn:
            row = await conn.fetchrow(
                """
                SELECT tenant_rules, user_preferences, session_state
                FROM hydration_profiles WHERE profile_id = $1;
                """,
                profile_id,
            )
            if row is None:
                return HydrationBundle()

            rules = row["tenant_rules"]
            prefs = row["user_preferences"]
            state = row["session_state"]

            return HydrationBundle(
                tenant_rules=json.loads(rules) if isinstance(rules, str) else list(rules),
                user_preferences=json.loads(prefs) if isinstance(prefs, str) else dict(prefs),
                session_state=json.loads(state) if isinstance(state, str) else dict(state),
            )

    async def save_tool_trace(self, trace_id: str, raw_trace: str) -> None:
        """Persist uncompressed tool trace."""
        pool = self._get_pool()
        now = datetime.now(UTC)

        async with pool.acquire() as conn:
            await conn.execute(
                """
                INSERT INTO tool_traces (trace_id, raw_trace, created_at)
                VALUES ($1, $2, $3)
                ON CONFLICT (trace_id) DO UPDATE SET
                    raw_trace = EXCLUDED.raw_trace,
                    created_at = EXCLUDED.created_at;
                """,
                trace_id,
                raw_trace,
                now,
            )

    async def get_tool_trace(self, trace_id: str) -> str | None:
        """Retrieve tool trace by ID."""
        pool = self._get_pool()

        async with pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT raw_trace FROM tool_traces WHERE trace_id = $1;", trace_id
            )
            if row is None:
                return None
            return str(row["raw_trace"])
