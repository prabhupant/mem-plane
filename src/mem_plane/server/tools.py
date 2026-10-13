"""Service and tool handlers implementing the mem-plane MCP contract."""

import json
import uuid
from datetime import UTC, datetime
from typing import Any

from mem_plane.core.models import FactEntity
from mem_plane.pipeline.embeddings import DummyEmbeddingClient, EmbeddingClient
from mem_plane.storage.protocols import MetadataStorage, SearchIndex


class MemPlaneService:
    """Core service encapsulating memory retrieval, dereferencing, and fact recording."""

    def __init__(
        self,
        storage: MetadataStorage,
        search_index: SearchIndex,
        embedding_client: EmbeddingClient | None = None,
    ) -> None:
        self.storage = storage
        self.search_index = search_index
        self.embedding_client = embedding_client or DummyEmbeddingClient()

    async def search_episodic_memory(self, query: str, top_k: int = 5) -> str:
        """Search memory using parallel BM25 and vector search fused with RRF.

        Returns lightweight surrogate summaries (~30 tokens each).
        """
        if not query.strip():
            return "[]"

        query_vector = await self.embedding_client.embed_text(query)
        hits = await self.search_index.hybrid_search(
            query=query,
            query_vector=query_vector,
            top_k=top_k,
        )
        return json.dumps([hit.model_dump() for hit in hits], indent=2)

    async def inspect_fact_provenance(self, fact_id: str) -> str:
        """Perform O(1) relational lookup returning raw document content and audit trail."""
        fact = await self.storage.get_fact_by_id(fact_id)
        if fact is None:
            return json.dumps({"error": f"Fact not found: {fact_id}"})
        return json.dumps(fact.model_dump(mode="json"), indent=2)

    async def dereference_raw_trace(self, trace_id: str) -> str:
        """Retrieve uncompressed tool execution logs or compiler traces."""
        raw_trace = await self.storage.get_tool_trace(trace_id)
        if raw_trace is None:
            return f"Trace not found for ID: {trace_id}"
        return raw_trace

    async def record_fact(
        self,
        summary: str,
        raw_content: str,
        source_type: str = "chat_turn",
        provenance_metadata: dict[str, Any] | None = None,
        tenant_id: str = "default",
        session_id: str | None = None,
        fact_id: str | None = None,
    ) -> str:
        """Record and index a new episodic fact into persistent memory."""
        assigned_id = fact_id or f"fact_{uuid.uuid4().hex[:12]}"
        fact = FactEntity(
            fact_id=assigned_id,
            tenant_id=tenant_id,
            session_id=session_id,
            summary=summary,
            raw_content=raw_content,
            source_type=source_type,
            provenance_metadata=provenance_metadata or {},
            created_at=datetime.now(UTC),
        )

        vector = await self.embedding_client.embed_text(f"{summary} {raw_content}")

        await self.storage.save_fact(fact)
        await self.search_index.index_vector_and_tokens(
            fact_id=assigned_id,
            summary=summary,
            content=raw_content,
            vector=vector,
        )

        return json.dumps(
            {
                "status": "success",
                "fact_id": assigned_id,
                "summary": summary,
            },
            indent=2,
        )
