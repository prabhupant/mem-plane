"""Async wrapper for SQLiteStore using asyncio.to_thread."""

from __future__ import annotations

import asyncio
from datetime import datetime
from pathlib import Path

from mem_plane.core.models import Fact, FactKind, SearchHit, TraceRecord
from mem_plane.project import ProjectInfo
from mem_plane.storage.protocols import (
    CreateResult,
    EpisodeChunk,
    HistoryRow,
    PendingItem,
    SearchRequest,
    SessionRow,
)
from mem_plane.storage.sqlite.store import SQLiteStore


class AsyncSQLiteStore:
    """Async wrapper delegating to synchronous SQLiteStore via asyncio.to_thread."""

    def __init__(
        self,
        database: str | Path | None = None,
        dimension: int = 384,
        model_name: str = "BAAI/bge-small-en-v1.5",
    ) -> None:
        self._sync_store = SQLiteStore(
            database=database, dimension=dimension, model_name=model_name
        )

    async def close(self) -> None:
        await asyncio.to_thread(self._sync_store.close)

    async def upsert_project(self, info: ProjectInfo) -> None:
        await asyncio.to_thread(self._sync_store.upsert_project, info)

    async def get_project(self, project_id: str) -> ProjectInfo | None:
        return await asyncio.to_thread(self._sync_store.get_project, project_id)

    async def upsert_session(
        self, session_id: str, project_id: str, transcript_path: str | None
    ) -> SessionRow:
        return await asyncio.to_thread(
            self._sync_store.upsert_session, session_id, project_id, transcript_path
        )

    async def advance_transcript_offset(self, session_id: str, new_offset: int) -> None:
        await asyncio.to_thread(self._sync_store.advance_transcript_offset, session_id, new_offset)

    async def increment_compactions(self, session_id: str) -> int:
        return await asyncio.to_thread(self._sync_store.increment_compactions, session_id)

    async def end_session(self, session_id: str) -> None:
        await asyncio.to_thread(self._sync_store.end_session, session_id)

    async def create_fact(self, fact: Fact) -> CreateResult:
        return await asyncio.to_thread(self._sync_store.create_fact, fact)

    async def update_fact(
        self,
        fact_id: str,
        project_id: str,
        *,
        summary: str | None = None,
        body: str | None = None,
        kind: FactKind | None = None,
        actor: str = "agent",
        session_id: str | None = None,
    ) -> Fact:
        return await asyncio.to_thread(
            self._sync_store.update_fact,
            fact_id,
            project_id,
            summary=summary,
            body=body,
            kind=kind,
            actor=actor,
            session_id=session_id,
        )

    async def delete_fact(
        self, fact_id: str, project_id: str, actor: str = "agent", session_id: str | None = None
    ) -> None:
        await asyncio.to_thread(
            self._sync_store.delete_fact, fact_id, project_id, actor, session_id
        )

    async def set_pinned(
        self, fact_id: str, project_id: str, pinned: bool, actor: str = "agent"
    ) -> None:
        await asyncio.to_thread(self._sync_store.set_pinned, fact_id, project_id, pinned, actor)

    async def get_fact(self, fact_id: str, project_id: str) -> Fact | None:
        return await asyncio.to_thread(self._sync_store.get_fact, fact_id, project_id)

    async def list_pinned(self, project_id: str) -> list[Fact]:
        return await asyncio.to_thread(self._sync_store.list_pinned, project_id)

    async def list_recent_facts(self, project_id: str, limit: int = 8) -> list[Fact]:
        return await asyncio.to_thread(self._sync_store.list_recent_facts, project_id, limit)

    async def list_session_facts(self, project_id: str, session_id: str) -> list[Fact]:
        return await asyncio.to_thread(self._sync_store.list_session_facts, project_id, session_id)

    async def fact_history(self, fact_id: str, project_id: str) -> list[HistoryRow]:
        return await asyncio.to_thread(self._sync_store.fact_history, fact_id, project_id)

    async def append_chunks(self, chunks: list[EpisodeChunk]) -> None:
        await asyncio.to_thread(self._sync_store.append_chunks, chunks)

    async def get_chunk(self, chunk_id: str, project_id: str) -> EpisodeChunk | None:
        return await asyncio.to_thread(self._sync_store.get_chunk, chunk_id, project_id)

    async def save_trace(self, trace: TraceRecord, retention_days: int = 14) -> None:
        await asyncio.to_thread(self._sync_store.save_trace, trace, retention_days)

    async def get_trace(self, trace_id: str, project_id: str) -> TraceRecord | None:
        return await asyncio.to_thread(self._sync_store.get_trace, trace_id, project_id)

    async def purge_expired_traces(self, now: datetime | None = None) -> int:
        return await asyncio.to_thread(self._sync_store.purge_expired_traces, now)

    async def pending_embeddings(self, limit: int = 50) -> list[PendingItem]:
        return await asyncio.to_thread(self._sync_store.pending_embeddings, limit)

    async def write_vectors(self, items: list[tuple[PendingItem, list[float]]]) -> None:
        await asyncio.to_thread(self._sync_store.write_vectors, items)

    async def search(
        self, req: SearchRequest, query_vector: list[float] | None = None
    ) -> list[SearchHit]:
        return await asyncio.to_thread(self._sync_store.search, req, query_vector)
