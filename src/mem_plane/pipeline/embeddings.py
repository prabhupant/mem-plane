"""Embedding client protocols and deterministic dummy client for offline testing."""

import hashlib
from typing import Protocol

import numpy as np


class EmbeddingClient(Protocol):
    """Protocol for asynchronous embedding clients."""

    @property
    def dimension(self) -> int: ...

    async def embed_text(self, text: str) -> list[float]: ...

    async def embed_batch(self, texts: list[str]) -> list[list[float]]: ...


class DummyEmbeddingClient:
    """Deterministic embedding generator for offline testing and zero-infra execution."""

    def __init__(self, dimension: int = 384) -> None:
        self._dimension = dimension

    @property
    def dimension(self) -> int:
        return self._dimension

    def _generate_vector(self, text: str) -> list[float]:
        """Generate a deterministic, L2-normalized pseudo-random float vector from text."""
        seed_bytes = hashlib.sha256(text.encode("utf-8")).digest()
        seed = int.from_bytes(seed_bytes[:4], byteorder="big")
        rng = np.random.RandomState(seed)
        vec = rng.randn(self._dimension).astype(np.float32)
        norm = float(np.linalg.norm(vec))
        if norm > 0.0:
            vec = vec / norm
        return [float(x) for x in vec]

    async def embed_text(self, text: str) -> list[float]:
        """Asynchronously compute embedding vector for a single text."""
        return self._generate_vector(text)

    async def embed_batch(self, texts: list[str]) -> list[list[float]]:
        """Asynchronously compute embeddings for a batch of texts."""
        return [self._generate_vector(t) for t in texts]
