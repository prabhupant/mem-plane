"""End-to-end integration tests for MCP server and tools."""

import json
from collections.abc import AsyncGenerator
from typing import Any

import pytest

from mem_plane.pipeline.embeddings import DummyEmbeddingClient
from mem_plane.server.mcp_server import create_mcp_server
from mem_plane.server.tools import MemPlaneService
from mem_plane.storage.sqlite.backend import SQLiteBackend


@pytest.fixture
async def mcp_service_and_server() -> AsyncGenerator[tuple[MemPlaneService, Any], None]:
    """Fixture providing initialized SQLiteBackend, MemPlaneService, and MCPServer."""
    backend = SQLiteBackend(database=":memory:", dimension=384)
    await backend.initialize()
    embedding_client = DummyEmbeddingClient(dimension=384)
    service = MemPlaneService(
        storage=backend,
        search_index=backend,
        embedding_client=embedding_client,
    )
    server = create_mcp_server(service)
    try:
        yield service, server
    finally:
        await backend.close()


@pytest.mark.asyncio
async def test_mcp_record_search_and_dereference_lifecycle(
    mcp_service_and_server: tuple[MemPlaneService, Any],
) -> None:
    """End-to-end test recording a fact, searching surrogates, and dereferencing raw provenance."""
    _service, server = mcp_service_and_server

    # 1. Record a new fact via MCP tool call
    record_call = await server.call_tool(
        "record_fact",
        {
            "summary": "Argon2id chosen for password hashing with 64MB memory cost",
            "raw_content": (
                "Implemented in security/crypto.py using argon2-cffi. "
                "Parameters: time_cost=3, memory_cost=65536, parallelism=4."
            ),
            "source_type": "chat_turn",
            "provenance_metadata": {
                "author": "bob",
                "commit_sha": "f9e8d7c6",
                "path": "security/crypto.py",
            },
        },
    )
    assert not record_call.is_error
    record_data = json.loads(record_call.content[0].text)
    assert record_data["status"] == "success"
    fact_id = record_data["fact_id"]
    assert fact_id is not None

    # 2. Stage 1: Query memory for surrogates via search_episodic_memory
    search_call = await server.call_tool(
        "search_episodic_memory",
        {"query": "Argon2id password hashing parameters", "top_k": 3},
    )
    assert not search_call.is_error
    search_results = json.loads(search_call.content[0].text)
    assert len(search_results) == 1
    hit = search_results[0]
    assert hit["fact_id"] == fact_id
    assert "Argon2id chosen" in hit["summary"]
    assert hit["source_path"] == "security/crypto.py"
    # Verify lightweight surrogate footprint
    assert hit["token_estimate"] == 30

    # 3. Stage 2: Dereference full raw document & provenance via inspect_fact_provenance
    provenance_call = await server.call_tool(
        "inspect_fact_provenance",
        {"fact_id": fact_id},
    )
    assert not provenance_call.is_error
    full_fact = json.loads(provenance_call.content[0].text)
    assert full_fact["fact_id"] == fact_id
    assert "time_cost=3" in full_fact["raw_content"]
    assert full_fact["provenance_metadata"]["commit_sha"] == "f9e8d7c6"
    assert full_fact["provenance_metadata"]["author"] == "bob"


@pytest.mark.asyncio
async def test_mcp_dereference_raw_trace(
    mcp_service_and_server: tuple[MemPlaneService, Any],
) -> None:
    """End-to-end test dereferencing a pruned raw execution trace via MCP tool."""
    service, server = mcp_service_and_server

    # Save a mock tool trace out-of-band in storage
    trace_id = "trace-pytest-failure-001"
    raw_stack_trace = (
        "FAILED tests/test_crypto.py::test_argon2 - AssertionError: hash mismatch\n"
        "AssertionError: '$argon2id$v=19$m=65536...' != '$argon2id$v=19$m=32768...'"
    )
    await service.storage.save_tool_trace(trace_id, raw_stack_trace)

    # Call dereference_raw_trace MCP tool
    trace_call = await server.call_tool(
        "dereference_raw_trace",
        {"trace_id": trace_id},
    )
    assert not trace_call.is_error
    retrieved_text = trace_call.content[0].text
    assert retrieved_text == raw_stack_trace


@pytest.mark.asyncio
async def test_mcp_missing_resources_handling(
    mcp_service_and_server: tuple[MemPlaneService, Any],
) -> None:
    """Validate behavior for missing facts, traces, or empty queries."""
    _service, server = mcp_service_and_server

    # Empty search query returns empty array
    empty_search = await server.call_tool(
        "search_episodic_memory",
        {"query": "   ", "top_k": 5},
    )
    assert empty_search.content[0].text == "[]"

    # Non-existent fact returns error JSON
    missing_fact = await server.call_tool(
        "inspect_fact_provenance",
        {"fact_id": "non-existent-fact"},
    )
    res_json = json.loads(missing_fact.content[0].text)
    assert "error" in res_json

    # Non-existent trace returns clear not found message
    missing_trace = await server.call_tool(
        "dereference_raw_trace",
        {"trace_id": "non-existent-trace"},
    )
    assert "Trace not found" in missing_trace.content[0].text
