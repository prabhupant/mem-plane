"""Embedding client protocols, FastEmbed provider, and deterministic dummy client."""

from __future__ import annotations

import asyncio
import hashlib
import logging
import math
import os
from collections.abc import Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Protocol

import numpy as np

if TYPE_CHECKING:
    from mem_plane.config import Settings

logger = logging.getLogger(__name__)

_WARNED_FASTEMBED_MISSING = False


@dataclass(frozen=True)
class EmbeddingModelSpec:
    """Specification and query/doc prefixes for an embedding model."""

    name: str
    dimension: int
    query_prefix: str = ""
    document_prefix: str = ""


MODELS: dict[str, EmbeddingModelSpec] = {
    "BAAI/bge-small-en-v1.5": EmbeddingModelSpec(
        name="BAAI/bge-small-en-v1.5",
        dimension=384,
        query_prefix="Represent this sentence for searching relevant passages: ",
    ),
}

SUPPORTED_MODELS: dict[str, int] = {k: v.dimension for k, v in MODELS.items()}


def _normalize(vec: Sequence[float]) -> list[float]:
    """Normalize a vector to unit L2 length."""
    norm = math.sqrt(sum(x * x for x in vec))
    if norm > 0.0:
        return [float(x / norm) for x in vec]
    return [float(x) for x in vec]


class EmbeddingClient(Protocol):
    """Protocol for asynchronous embedding clients."""

    @property
    def dimension(self) -> int: ...

    async def embed_query(self, text: str) -> list[float]: ...

    async def embed_documents(self, texts: list[str]) -> list[list[float]]: ...

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

    async def embed_query(self, text: str) -> list[float]:
        """Embed a search query text."""
        return self._generate_vector(text)

    async def embed_documents(self, texts: list[str]) -> list[list[float]]:
        """Embed a list of document texts."""
        return [self._generate_vector(t) for t in texts]

    async def embed_text(self, text: str) -> list[float]:
        """Deprecated alias: asynchronously compute embedding vector for a single text."""
        return await self.embed_query(text)

    async def embed_batch(self, texts: list[str]) -> list[list[float]]:
        """Deprecated alias: asynchronously compute embeddings for a batch of texts."""
        return await self.embed_documents(texts)


class FastEmbedClient:
    """FastEmbed client loading ONNX models lazily on first use in a worker thread."""

    def __init__(self, model_name: str = "BAAI/bge-small-en-v1.5") -> None:
        if model_name not in MODELS:
            raise ValueError(
                f"Unknown embedding model: '{model_name}'. Registered models: {list(MODELS.keys())}"
            )
        self._spec = MODELS[model_name]
        self._model_name = model_name
        self._model: Any = None
        self._lock = asyncio.Lock()

    @property
    def dimension(self) -> int:
        return self._spec.dimension

    async def _ensure_model(self) -> Any:
        if self._model is not None:
            return self._model
        async with self._lock:
            if self._model is None:
                from fastembed import TextEmbedding

                self._model = await asyncio.to_thread(TextEmbedding, model_name=self._model_name)
            return self._model

    async def embed_query(self, text: str) -> list[float]:
        """Embed a search query using the model's query prefix and L2 normalization."""
        model = await self._ensure_model()
        prompt = self._spec.query_prefix + text

        def _run() -> list[float]:
            vectors = list(model.embed([prompt]))
            return _normalize(vectors[0])

        return await asyncio.to_thread(_run)

    async def embed_documents(self, texts: list[str]) -> list[list[float]]:
        """Embed a batch of document texts using document prefix and L2 normalization."""
        if not texts:
            return []
        model = await self._ensure_model()
        prompts = [self._spec.document_prefix + t for t in texts]

        def _run() -> list[list[float]]:
            vectors = list(model.embed(prompts))
            return [_normalize(v) for v in vectors]

        return await asyncio.to_thread(_run)

    async def embed_text(self, text: str) -> list[float]:
        """Deprecated alias for embedding single text (treated as query)."""
        return await self.embed_query(text)

    async def embed_batch(self, texts: list[str]) -> list[list[float]]:
        """Deprecated alias for embedding documents."""
        return await self.embed_documents(texts)


def build_embedding_client(settings: Settings | None = None) -> EmbeddingClient | None:
    """Build and return configured EmbeddingClient, or None for keyword-only search."""
    global _WARNED_FASTEMBED_MISSING

    if settings is None:
        from mem_plane.config import Settings as ConfigSettings

        settings = ConfigSettings()

    provider = settings.embedding_provider.lower()

    if provider == "none":
        return None

    if provider == "dummy":
        allowed = settings.allow_dummy_embeddings or (
            os.environ.get("MEM_PLANE_ALLOW_DUMMY_EMBEDDINGS") == "1"
        )
        if not allowed:
            raise RuntimeError(
                "Dummy embedding provider requires MEM_PLANE_ALLOW_DUMMY_EMBEDDINGS=1"
            )
        return DummyEmbeddingClient(dimension=settings.embedding_dimension)

    if provider == "fastembed":
        try:
            import fastembed  # noqa: F401
        except ImportError:
            if not _WARNED_FASTEMBED_MISSING:
                logger.warning(
                    "fastembed is not installed; vector search disabled (install mem-plane[embed])"
                )
                _WARNED_FASTEMBED_MISSING = True
            return None
        return FastEmbedClient(model_name=settings.embedding_model)

    raise ValueError(f"Unsupported embedding provider: {provider}")
