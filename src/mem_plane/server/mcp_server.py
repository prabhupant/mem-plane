"""MCP Server daemon entrypoint for mem-plane (Section 8.1)."""

from __future__ import annotations

import asyncio
import logging
import os
from typing import TYPE_CHECKING

from mcp.server.mcpserver import MCPServer

from mem_plane.config import Settings
from mem_plane.pipeline.embedder_worker import BackgroundEmbedderWorker
from mem_plane.pipeline.embeddings import build_embedding_client
from mem_plane.project import resolve_project
from mem_plane.server.service import MemPlaneService
from mem_plane.server.tool_text import (
    FORGET_MEMORY_DESCRIPTION,
    FORGET_MEMORY_NAME,
    GET_MEMORY_DESCRIPTION,
    GET_MEMORY_NAME,
    MEMORY_STATUS_DESCRIPTION,
    MEMORY_STATUS_NAME,
    PIN_MEMORY_DESCRIPTION,
    PIN_MEMORY_NAME,
    RECORD_FACT_DESCRIPTION,
    RECORD_FACT_NAME,
    SEARCH_MEMORY_DESCRIPTION,
    SEARCH_MEMORY_NAME,
    UPDATE_MEMORY_DESCRIPTION,
    UPDATE_MEMORY_NAME,
)
from mem_plane.storage.sqlite.store import SQLiteStore

if TYPE_CHECKING:
    from collections.abc import Sequence

logger = logging.getLogger(__name__)


def create_mcp_server(service: MemPlaneService) -> MCPServer:
    """Create and configure MCPServer with static descriptions from tool_text.py."""
    server = MCPServer(
        name="mem-plane",
        description="Persistent memory plane and context governor for AI harnesses",
    )

    @server.tool(name=SEARCH_MEMORY_NAME, description=SEARCH_MEMORY_DESCRIPTION)
    async def search_memory(
        query: str,
        top_k: int = 5,
        include_episodes: bool = True,
    ) -> str:
        return await service.search_memory(
            query=query, top_k=top_k, include_episodes=include_episodes
        )

    @server.tool(name=GET_MEMORY_NAME, description=GET_MEMORY_DESCRIPTION)
    async def get_memory(
        ref: str,
        grep: str | None = None,
        lines: str | None = None,
        max_chars: int = 6000,
    ) -> str:
        return await service.get_memory(ref=ref, grep=grep, lines=lines, max_chars=max_chars)

    @server.tool(name=RECORD_FACT_NAME, description=RECORD_FACT_DESCRIPTION)
    async def record_fact(
        summary: str,
        body: str,
        kind: str,
        paths: list[str] | None = None,
        supersedes: str | None = None,
        pin: bool = False,
    ) -> str:
        return await service.record_fact(
            summary=summary,
            body=body,
            kind=kind,
            paths=paths,
            supersedes=supersedes,
            pin=pin,
        )

    @server.tool(name=UPDATE_MEMORY_NAME, description=UPDATE_MEMORY_DESCRIPTION)
    async def update_memory(
        fact_id: str,
        summary: str | None = None,
        body: str | None = None,
        kind: str | None = None,
    ) -> str:
        return await service.update_memory(
            fact_id=fact_id,
            summary=summary,
            body=body,
            kind=kind,
        )

    @server.tool(name=FORGET_MEMORY_NAME, description=FORGET_MEMORY_DESCRIPTION)
    async def forget_memory(
        fact_id: str,
        reason: str,
    ) -> str:
        return await service.forget_memory(fact_id=fact_id, reason=reason)

    @server.tool(name=PIN_MEMORY_NAME, description=PIN_MEMORY_DESCRIPTION)
    async def pin_memory(
        fact_id: str,
        pinned: bool,
    ) -> str:
        return await service.pin_memory(fact_id=fact_id, pinned=pinned)

    @server.tool(name=MEMORY_STATUS_NAME, description=MEMORY_STATUS_DESCRIPTION)
    async def memory_status() -> str:
        return await service.memory_status()

    return server


async def run_server_stdio(settings: Settings | None = None) -> None:
    """Run MCP server over stdio transport following the Section 8.1 lifecycle."""
    cfg = settings or Settings()

    # 1. Open store
    store = SQLiteStore(
        database=cfg.db_path,
        dimension=cfg.embedding_dimension,
        model_name=cfg.embedding_model,
        settings=cfg,
    )

    # 2. Resolve project (fixed for process lifetime)
    cwd = os.environ.get("CLAUDE_PROJECT_DIR") or os.getcwd()
    proj_info = resolve_project(cwd)
    store.upsert_project(proj_info)
    project_id = proj_info.project_id
    project_root = proj_info.root_path

    # 3. Embedding client
    client = build_embedding_client(cfg)

    # 4. Background tasks: embedder worker & trace purger
    worker = BackgroundEmbedderWorker(
        store=store,
        client=client,
        poll_interval=2.0,
    )
    worker.start()

    # Initial purge of expired traces
    try:
        store.purge_expired_traces()
    except (OSError, RuntimeError) as e:
        logger.warning("Trace purge failed: %s", e)

    async def _periodic_purge() -> None:
        while True:
            try:
                await asyncio.sleep(3600)
                store.purge_expired_traces()
            except asyncio.CancelledError:
                break
            except (OSError, RuntimeError) as e:
                logger.warning("Periodic trace purge error: %s", e)

    purge_task = asyncio.create_task(_periodic_purge())

    # 5. Service & MCP Server
    service = MemPlaneService(
        store=store,
        project_id=project_id,
        embedding_client=client,
        settings=cfg,
        project_root=project_root,
        embedder_worker=worker,
    )
    server = create_mcp_server(service)

    try:
        await server.run_stdio_async()
    finally:
        purge_task.cancel()
        await worker.stop()
        store.close()


def main(argv: Sequence[str] | None = None) -> int:
    """Server daemon CLI entrypoint."""
    try:
        asyncio.run(run_server_stdio())
        return 0
    except Exception:
        logger.exception("Fatal error in MCP server")
        return 1


if __name__ == "__main__":
    main()
