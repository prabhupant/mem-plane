"""End-to-end integration tests for MCP server daemon and tools (Section 8.1, 8.2)."""

from __future__ import annotations

from pathlib import Path

import pytest

from mem_plane.pipeline.embeddings import DummyEmbeddingClient
from mem_plane.server.mcp_server import create_mcp_server
from mem_plane.server.service import MemPlaneService
from mem_plane.storage.sqlite.store import SQLiteStore


@pytest.fixture
def mcp_server_setup(tmp_path: Path) -> tuple[MemPlaneService, object]:
    db_file = tmp_path / "mcp_test.db"
    store = SQLiteStore(database=db_file)
    client = DummyEmbeddingClient(dimension=384)
    service = MemPlaneService(
        store=store,
        project_id="p_mcp_test",
        embedding_client=client,
        project_root=str(tmp_path),
    )
    server = create_mcp_server(service)
    return service, server


@pytest.mark.asyncio
async def test_mcp_tools_end_to_end_lifecycle(
    mcp_server_setup: tuple[MemPlaneService, object],
) -> None:
    _service, server = mcp_server_setup

    # 1. record_fact
    res_rec = await server.call_tool(  # type: ignore[attr-defined]
        "record_fact",
        {
            "summary": "JWT auth with 15m expiry",
            "body": "Tokens refresh after 7 days.",
            "kind": "decision",
            "pin": True,
        },
    )
    assert not res_rec.is_error
    rec_text = res_rec.content[0].text
    assert rec_text.startswith("recorded f_")
    fact_id = rec_text.split()[1]

    # 2. search_memory
    res_search = await server.call_tool(  # type: ignore[attr-defined]
        "search_memory",
        {"query": "JWT auth tokens", "top_k": 5},
    )
    assert not res_search.is_error
    search_text = res_search.content[0].text
    assert fact_id in search_text
    assert "JWT auth with 15m expiry" in search_text

    # 3. get_memory
    res_get = await server.call_tool(  # type: ignore[attr-defined]
        "get_memory",
        {"ref": fact_id},
    )
    assert not res_get.is_error
    get_text = res_get.content[0].text
    assert fact_id in get_text
    assert "[PINNED]" in get_text

    # 4. update_memory
    res_up = await server.call_tool(  # type: ignore[attr-defined]
        "update_memory",
        {"fact_id": fact_id, "summary": "JWT auth with 30m expiry"},
    )
    assert not res_up.is_error
    assert res_up.content[0].text == f"updated {fact_id}"

    # 5. pin_memory
    res_pin = await server.call_tool(  # type: ignore[attr-defined]
        "pin_memory",
        {"fact_id": fact_id, "pinned": False},
    )
    assert not res_pin.is_error
    assert res_pin.content[0].text == f"unpinned {fact_id}"

    # 6. memory_status
    res_status = await server.call_tool(  # type: ignore[attr-defined]
        "memory_status",
        {},
    )
    assert not res_status.is_error
    status_text = res_status.content[0].text
    assert "Active facts: 1" in status_text

    # 7. forget_memory
    res_forget = await server.call_tool(  # type: ignore[attr-defined]
        "forget_memory",
        {"fact_id": fact_id, "reason": "No longer using JWT"},
    )
    assert not res_forget.is_error
    assert res_forget.content[0].text == f"forgot {fact_id}: No longer using JWT"
