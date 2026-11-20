"""Tests for the background embedder worker and embed-pending functionality."""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from mem_plane.core.models import Fact, FactKind
from mem_plane.pipeline.embedder_worker import (
    BackgroundEmbedderWorker,
    embed_pending_batch_sync,
)
from mem_plane.pipeline.embeddings import DummyEmbeddingClient
from mem_plane.storage.sqlite.store import SQLiteStore


@pytest.mark.asyncio
async def test_embedder_worker_processes_pending(tmp_path: Path) -> None:
    db_file = tmp_path / "embedder.db"
    store = SQLiteStore(database=db_file)
    client = DummyEmbeddingClient(dimension=384)

    try:
        fact = Fact(
            fact_id="f_worker000001",
            project_id="p_test_worker",
            kind=FactKind.DECISION,
            summary="Worker test fact",
            body="Checking background embedder drains pending items.",
        )
        res = store.create_fact(fact)
        assert res.status == "created"

        # Verify fact was created with embedded_at None
        cur = store.conn.execute(
            "SELECT embedded_at FROM facts WHERE fact_id = ?;", (fact.fact_id,)
        )
        row = cur.fetchone()
        assert row is not None
        assert row[0] is None

        # Start worker and trigger embedding
        worker = BackgroundEmbedderWorker(store, client, poll_interval=0.1, batch_size=10)
        worker.start()
        worker.trigger()

        # Wait up to 2 seconds for worker to process
        for _ in range(20):
            await asyncio.sleep(0.05)
            cur = store.conn.execute(
                "SELECT embedded_at FROM facts WHERE fact_id = ?;", (fact.fact_id,)
            )
            row = cur.fetchone()
            if row and row[0] is not None:
                break

        await worker.stop()

        cur = store.conn.execute(
            "SELECT embedded_at FROM facts WHERE fact_id = ?;", (fact.fact_id,)
        )
        row = cur.fetchone()
        assert row is not None
        assert row[0] is not None

        # Verify facts_vec has the vector
        cur_vec = store.conn.execute(
            "SELECT fact_id FROM facts_vec WHERE fact_id = ?;", (fact.fact_id,)
        )
        vec_row = cur_vec.fetchone()
        assert vec_row is not None
        assert vec_row[0] == fact.fact_id

    finally:
        store.close()


def test_embed_pending_sync(tmp_path: Path) -> None:
    db_file = tmp_path / "sync_embed.db"
    store = SQLiteStore(database=db_file)
    client = DummyEmbeddingClient(dimension=384)

    try:
        fact = Fact(
            fact_id="f_sync00000001",
            project_id="p_sync_proj",
            kind=FactKind.RULE,
            summary="Sync rule",
            body="Sync embed batch test.",
        )
        store.create_fact(fact)

        processed = embed_pending_batch_sync(store, client, limit=10)
        assert processed == 1

        # Check embedded_at is now set
        cur = store.conn.execute(
            "SELECT embedded_at FROM facts WHERE fact_id = ?;", (fact.fact_id,)
        )
        assert cur.fetchone()[0] is not None

        # Next call returns 0 since nothing is pending
        assert embed_pending_batch_sync(store, client, limit=10) == 0
    finally:
        store.close()
