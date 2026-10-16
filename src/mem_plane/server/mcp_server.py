"""MCP Server daemon entrypoint for mem-plane."""

import asyncio
from typing import Any

from mcp.server.mcpserver import MCPServer

from mem_plane.config import Settings, get_storage_and_index
from mem_plane.pipeline.embeddings import DummyEmbeddingClient
from mem_plane.server.tools import MemPlaneService


def create_mcp_server(service: MemPlaneService) -> MCPServer:
    """Create and configure an MCPServer instance with registered memory plane tools."""
    server = MCPServer(
        name="mem-plane",
        description="Infrastructure-level memory plane and context governor for AI agents",
    )

    @server.tool(
        name="search_episodic_memory",
        description=(
            "Search episodic memory using parallel BM25 and vector search fused with RRF. "
            "Returns lightweight surrogate summaries (~30 tokens each) for two-stage progressive disclosure."
        ),
    )
    async def search_episodic_memory(query: str, top_k: int = 5) -> str:
        return await service.search_episodic_memory(query=query, top_k=top_k)

    @server.tool(
        name="inspect_fact_provenance",
        description=(
            "Perform O(1) relational dereferencing of a fact by ID. "
            "Returns full uncompressed document body, author, commit SHA, timestamp, and audit trail."
        ),
    )
    async def inspect_fact_provenance(fact_id: str) -> str:
        return await service.inspect_fact_provenance(fact_id=fact_id)

    @server.tool(
        name="dereference_raw_trace",
        description=(
            "Retrieve uncompressed tool execution logs, compiler error dumps, or stack traces "
            "by Trace ID when truncated by the ephemeral tool pruner."
        ),
    )
    async def dereference_raw_trace(trace_id: str) -> str:
        return await service.dereference_raw_trace(trace_id=trace_id)

    @server.tool(
        name="record_fact",
        description=(
            "Record and index a new episodic fact, decision, or architectural rule into persistent memory."
        ),
    )
    async def record_fact(
        summary: str,
        raw_content: str,
        source_type: str = "chat_turn",
        provenance_metadata: dict[str, Any] | None = None,
        tenant_id: str = "default",
        session_id: str | None = None,
    ) -> str:
        return await service.record_fact(
            summary=summary,
            raw_content=raw_content,
            source_type=source_type,
            provenance_metadata=provenance_metadata,
            tenant_id=tenant_id,
            session_id=session_id,
        )

    return server


async def run_server_stdio(settings: Settings | None = None) -> None:
    """Run the MCP server over stdio transport using configured storage backend."""
    cfg = settings or Settings()
    storage, search_index = await get_storage_and_index(cfg)
    embedding_client = DummyEmbeddingClient(dimension=cfg.embedding_dimension)
    service = MemPlaneService(
        storage=storage, search_index=search_index, embedding_client=embedding_client
    )
    server = create_mcp_server(service)
    await server.run_stdio_async()


def main() -> None:
    """CLI entrypoint."""
    asyncio.run(run_server_stdio())


if __name__ == "__main__":
    main()
