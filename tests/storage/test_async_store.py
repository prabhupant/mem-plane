"""Tests for AsyncSQLiteStore async wrapper."""

from __future__ import annotations

from pathlib import Path

import pytest

from mem_plane.core.models import Fact, FactKind, TraceRecord
from mem_plane.project import ProjectInfo
from mem_plane.storage.protocols import EpisodeChunk
from mem_plane.storage.sqlite.async_store import AsyncSQLiteStore


@pytest.mark.asyncio
async def test_async_sqlite_store_lifecycle(tmp_path: Path) -> None:
    store = AsyncSQLiteStore(database=tmp_path / "async.db")
    try:
        # 1. Project
        info = ProjectInfo(
            project_id="p_async1", display_name="AsyncProj", root_path="/test", remote_url=None
        )
        await store.upsert_project(info)
        proj = await store.get_project("p_async1")
        assert proj is not None
        assert proj.display_name == "AsyncProj"

        # 2. Fact
        fact = Fact(
            fact_id="f_async0000001",
            project_id="p_async1",
            kind=FactKind.DECISION,
            summary="Async wrapper works cleanly.",
            body="Delegates to SQLiteStore via asyncio.to_thread.",
        )
        res = await store.create_fact(fact)
        assert res.status == "created"

        fetched = await store.get_fact("f_async0000001", "p_async1")
        assert fetched is not None
        assert fetched.summary == "Async wrapper works cleanly."

        # 3. Update Fact
        updated = await store.update_fact(
            "f_async0000001", "p_async1", summary="Updated summary.", actor="agent"
        )
        assert updated.summary == "Updated summary."

        # 4. Session & Chunk
        await store.upsert_session("s_async1", "p_async1", "/tmp/t.jsonl")
        chunk = EpisodeChunk(
            chunk_id="e_async0000001",
            project_id="p_async1",
            session_id="s_async1",
            seq=0,
            segment=0,
            start_ts=None,
            end_ts=None,
            text="Conversation chunk text.",
            token_estimate=10,
            created_at=fact.created_at,
        )
        await store.append_chunks([chunk])
        c = await store.get_chunk("e_async0000001", "p_async1")
        assert c is not None
        assert c.text == "Conversation chunk text."

        # 5. Trace
        trace = TraceRecord(
            trace_id="t_async00001",
            project_id="p_async1",
            tool_name="Bash",
            stdout="out",
            stderr="",
            line_count=1,
            char_count=3,
            created_at=fact.created_at,
        )
        await store.save_trace(trace)
        t = await store.get_trace("t_async00001", "p_async1")
        assert t is not None
        assert t.stdout == "out"

        # 6. Delete Fact
        await store.delete_fact("f_async0000001", "p_async1")
        assert await store.get_fact("f_async0000001", "p_async1") is None
    finally:
        await store.close()
