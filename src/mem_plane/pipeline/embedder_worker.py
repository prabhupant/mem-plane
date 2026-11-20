"""Background worker that continuously embeds pending facts and episode chunks."""

from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from mem_plane.pipeline.embeddings import EmbeddingClient
    from mem_plane.storage.protocols import MemoryStore

logger = logging.getLogger(__name__)


def embed_pending_batch_sync(
    store: MemoryStore, client: EmbeddingClient | None, limit: int = 64
) -> int:
    """Synchronously embed pending items using the provided client."""
    if client is None:
        return 0

    pending = store.pending_embeddings(limit=limit)
    if not pending:
        return 0

    texts = [item.text_to_embed for item in pending]
    vectors = asyncio.run(client.embed_documents(texts))
    store.write_vectors(list(zip(pending, vectors, strict=True)))
    return len(pending)


async def embed_pending_batch_async(
    store: MemoryStore, client: EmbeddingClient | None, limit: int = 64
) -> int:
    """Asynchronously embed pending items using the provided client."""
    if client is None:
        return 0

    pending = store.pending_embeddings(limit=limit)
    if not pending:
        return 0

    texts = [item.text_to_embed for item in pending]
    vectors = await client.embed_documents(texts)
    await asyncio.to_thread(store.write_vectors, list(zip(pending, vectors, strict=True)))
    return len(pending)


class BackgroundEmbedderWorker:
    """Background task running in the MCP server.

    Every 2 seconds, and immediately when triggered, it drains pending embeddings.
    """

    def __init__(
        self,
        store: MemoryStore,
        client: EmbeddingClient | None,
        poll_interval: float = 2.0,
        batch_size: int = 64,
    ) -> None:
        self.store = store
        self.client = client
        self.poll_interval = poll_interval
        self.batch_size = batch_size
        self._trigger_event = asyncio.Event()
        self._stopped = False
        self._task: asyncio.Task[None] | None = None

    def trigger(self) -> None:
        """Trigger an immediate embedding pass (e.g. after a local write)."""
        self._trigger_event.set()

    def start(self) -> None:
        """Start the background worker loop task."""
        if self._task is None or self._task.done():
            self._stopped = False
            self._task = asyncio.create_task(self._run_loop())

    async def stop(self) -> None:
        """Stop the background worker."""
        self._stopped = True
        self._trigger_event.set()
        if self._task is not None:
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None

    async def _run_loop(self) -> None:
        while not self._stopped:
            try:
                try:
                    await asyncio.wait_for(self._trigger_event.wait(), timeout=self.poll_interval)
                except TimeoutError:
                    pass
                self._trigger_event.clear()

                if self._stopped:
                    break

                if self.client is None:
                    continue

                while not self._stopped:
                    count = await embed_pending_batch_async(
                        self.store, self.client, limit=self.batch_size
                    )
                    if count == 0:
                        break
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.warning("Error in background embedder worker: %s", e, exc_info=True)
                await asyncio.sleep(self.poll_interval)
