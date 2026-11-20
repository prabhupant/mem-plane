"""Storage protocol definitions for mem-plane."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Literal, Protocol, runtime_checkable

from mem_plane.core.models import (
    Fact,
    FactEntity,
    FactKind,
    HydrationBundle,
    SearchHit,
    SurrogateHit,
    TraceRecord,
)
from mem_plane.project import ProjectInfo


@dataclass(frozen=True)
class SessionRow:
    session_id: str
    project_id: str
    harness: str
    transcript_path: str | None
    transcript_offset: int
    compactions: int
    started_at: datetime
    last_event_at: datetime
    ended_at: datetime | None = None


@dataclass(frozen=True)
class CreateResult:
    status: Literal["created", "duplicate", "superseded"]
    fact: Fact
    superseded_fact: Fact | None = None
    duplicate_of_id: str | None = None


@dataclass(frozen=True)
class HistoryRow:
    history_id: int
    fact_id: str
    event: str
    old_summary: str | None
    new_summary: str | None
    old_body: str | None
    new_body: str | None
    actor: str
    session_id: str | None
    created_at: datetime


@dataclass(frozen=True)
class EpisodeChunk:
    chunk_id: str
    project_id: str
    session_id: str
    seq: int
    segment: int
    start_ts: str | None
    end_ts: str | None
    text: str
    token_estimate: int
    created_at: datetime
    embedded_at: datetime | None = None


@dataclass(frozen=True)
class PendingItem:
    ref: str
    ref_type: Literal["fact", "episode"]
    project_id: str
    text_to_embed: str


@dataclass(frozen=True)
class SearchRequest:
    project_id: str
    query: str
    kind: FactKind | None = None
    include_superseded: bool = False
    limit: int = 10


@runtime_checkable
class MemoryStore(Protocol):
    """Core synchronous memory store protocol (Section 5.5)."""

    # projects / sessions
    def upsert_project(self, info: ProjectInfo) -> None: ...
    def get_project(self, project_id: str) -> ProjectInfo | None: ...
    def upsert_session(
        self, session_id: str, project_id: str, transcript_path: str | None
    ) -> SessionRow: ...
    def advance_transcript_offset(self, session_id: str, new_offset: int) -> None: ...
    def increment_compactions(self, session_id: str) -> int: ...

    # facts
    def create_fact(self, fact: Fact) -> CreateResult: ...  # handles dedup + supersede
    def update_fact(
        self,
        fact_id: str,
        project_id: str,
        *,
        summary: str | None = None,
        body: str | None = None,
        kind: FactKind | None = None,
        actor: str = "agent",
        session_id: str | None = None,
    ) -> Fact: ...
    def delete_fact(
        self, fact_id: str, project_id: str, actor: str = "agent", session_id: str | None = None
    ) -> None: ...  # soft delete
    def set_pinned(
        self, fact_id: str, project_id: str, pinned: bool, actor: str = "agent"
    ) -> None: ...
    def get_fact(self, fact_id: str, project_id: str) -> Fact | None: ...
    def list_pinned(self, project_id: str) -> list[Fact]: ...
    def list_recent_facts(self, project_id: str, limit: int = 8) -> list[Fact]: ...
    def list_session_facts(self, project_id: str, session_id: str) -> list[Fact]: ...
    def fact_history(self, fact_id: str, project_id: str) -> list[HistoryRow]: ...

    # episodes
    def append_chunks(self, chunks: list[EpisodeChunk]) -> None: ...
    def get_chunk(self, chunk_id: str, project_id: str) -> EpisodeChunk | None: ...

    # traces
    def save_trace(self, trace: TraceRecord, retention_days: int = 14) -> None: ...
    def get_trace(self, trace_id: str, project_id: str) -> TraceRecord | None: ...
    def purge_expired_traces(self, now: datetime | None = None) -> int: ...

    # vectors
    def pending_embeddings(self, limit: int = 50) -> list[PendingItem]: ...
    def write_vectors(self, items: list[tuple[PendingItem, list[float]]]) -> None: ...

    # search
    def search(
        self, req: SearchRequest, query_vector: list[float] | None = None
    ) -> list[SearchHit]: ...


# ---------------------------------------------------------------------------
# Legacy protocols kept for compatibility with frozen production backend
# ---------------------------------------------------------------------------


@runtime_checkable
class MetadataStorage(Protocol):
    """Deprecated: Legacy protocol for transactional document persistence."""

    async def save_fact(self, fact: FactEntity) -> None: ...
    async def get_fact_by_id(self, fact_id: str) -> FactEntity | None: ...
    async def get_hydration_data(
        self, tenant_id: str, user_id: str, session_id: str
    ) -> HydrationBundle: ...
    async def save_tool_trace(self, trace_id: str, raw_trace: str) -> None: ...
    async def get_tool_trace(self, trace_id: str) -> str | None: ...


@runtime_checkable
class SearchIndex(Protocol):
    """Deprecated: Legacy protocol for hybrid search indexing and query ranking."""

    async def index_vector_and_tokens(
        self, fact_id: str, summary: str, content: str, vector: list[float]
    ) -> None: ...
    async def hybrid_search(
        self, query: str, query_vector: list[float], top_k: int = 5
    ) -> list[SurrogateHit]: ...
