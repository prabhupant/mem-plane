"""Abstract protocol definitions decoupling metadata persistence from search indexes."""

from typing import Protocol, runtime_checkable

from mem_plane.core.models import FactEntity, HydrationBundle, SurrogateHit


@runtime_checkable
class MetadataStorage(Protocol):
    """Protocol for transactional document and metadata persistence."""

    async def save_fact(self, fact: FactEntity) -> None:
        """Persist a FactEntity including raw content and provenance metadata."""
        ...

    async def get_fact_by_id(self, fact_id: str) -> FactEntity | None:
        """Retrieve a FactEntity by its ID with O(1) performance."""
        ...

    async def get_hydration_data(
        self, tenant_id: str, user_id: str, session_id: str
    ) -> HydrationBundle:
        """Fetch deterministic hydration data (tenant rules, user preferences, session state)."""
        ...

    async def save_tool_trace(self, trace_id: str, raw_trace: str) -> None:
        """Persist an uncompressed tool execution trace out-of-band."""
        ...

    async def get_tool_trace(self, trace_id: str) -> str | None:
        """Retrieve an uncompressed tool execution trace by ID."""
        ...


@runtime_checkable
class SearchIndex(Protocol):
    """Protocol for hybrid search indexing and query ranking."""

    async def index_vector_and_tokens(
        self, fact_id: str, summary: str, content: str, vector: list[float]
    ) -> None:
        """Index a fact in both the lexical inverted index and vector index."""
        ...

    async def hybrid_search(
        self, query: str, query_vector: list[float], top_k: int = 5
    ) -> list[SurrogateHit]:
        """Perform hybrid lexical and vector search fused with Reciprocal Rank Fusion."""
        ...
