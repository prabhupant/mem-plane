"""Embedding client protocols, OpenAI and FastEmbed providers, and deterministic dummy client."""

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
_WARNED_OPENAI_MISSING = False


@dataclass(frozen=True)
class EmbeddingModelSpec:
    """Specification and query/doc prefixes for an embedding model."""

    name: str
    dimension: int
    query_prefix: str = ""
    document_prefix: str = ""


MODELS: dict[str, EmbeddingModelSpec] = {
    "text-embedding-3-small": EmbeddingModelSpec(
        name="text-embedding-3-small",
        dimension=384,
    ),
    "text-embedding-3-large": EmbeddingModelSpec(
        name="text-embedding-3-large",
        dimension=3072,
    ),
    "text-embedding-ada-002": EmbeddingModelSpec(
        name="text-embedding-ada-002",
        dimension=1536,
    ),
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


class OpenAIEmbeddingClient:
    """OpenAI embedding client using AsyncOpenAI with optional dimension reduction and L2 normalization."""

    def __init__(
        self,
        model_name: str = "text-embedding-3-small",
        dimension: int = 384,
        api_key: str | None = None,
        base_url: str | None = None,
    ) -> None:
        self._model_name = model_name
        self._dimension = dimension
        self._api_key = api_key or os.environ.get("OPENAI_API_KEY")
        self._base_url = base_url or os.environ.get("OPENAI_BASE_URL")
        self._client: Any = None
        self._lock = asyncio.Lock()

    @property
    def dimension(self) -> int:
        return self._dimension

    async def _ensure_client(self) -> Any:
        if self._client is not None:
            return self._client
        async with self._lock:
            if self._client is None:
                from openai import AsyncOpenAI

                self._client = AsyncOpenAI(
                    api_key=self._api_key,
                    base_url=self._base_url,
                )
            return self._client

    async def embed_query(self, text: str) -> list[float]:
        """Embed a search query text."""
        docs = await self.embed_documents([text])
        return docs[0] if docs else []

    async def embed_documents(self, texts: list[str]) -> list[list[float]]:
        """Embed a list of document texts."""
        if not texts:
            return []

        client = await self._ensure_client()
        batch_size = 256
        all_vectors: list[list[float]] = []

        for i in range(0, len(texts), batch_size):
            chunk = texts[i : i + batch_size]
            kwargs: dict[str, Any] = {
                "model": self._model_name,
                "input": chunk,
            }
            if "text-embedding-3" in self._model_name:
                kwargs["dimensions"] = self._dimension

            response = await client.embeddings.create(**kwargs)
            sorted_data = sorted(response.data, key=lambda x: getattr(x, "index", 0))
            for item in sorted_data:
                all_vectors.append(_normalize(item.embedding))

        return all_vectors

    async def embed_text(self, text: str) -> list[float]:
        """Deprecated alias for embed_query."""
        return await self.embed_query(text)

    async def embed_batch(self, texts: list[str]) -> list[list[float]]:
        """Deprecated alias for embed_documents."""
        return await self.embed_documents(texts)


def build_embedding_client(settings: Settings | None = None) -> EmbeddingClient | None:
    """Build and return configured EmbeddingClient, or None for keyword-only search."""
    global _WARNED_FASTEMBED_MISSING, _WARNED_OPENAI_MISSING

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

    if provider == "openai":
        try:
            import openai  # noqa: F401
        except ImportError:
            if not _WARNED_OPENAI_MISSING:
                logger.warning(
                    "openai is not installed; vector search disabled (install openai or mem-plane[embed])"
                )
                _WARNED_OPENAI_MISSING = True
            return None

        api_key = settings.openai_api_key or os.environ.get("OPENAI_API_KEY")
        if not api_key:
            if not _WARNED_OPENAI_MISSING:
                logger.warning(
                    "OPENAI_API_KEY is not set; vector search disabled (falling back to keyword search)"
                )
                _WARNED_OPENAI_MISSING = True
            return None

        return OpenAIEmbeddingClient(
            model_name=settings.embedding_model,
            dimension=settings.embedding_dimension,
            api_key=api_key,
            base_url=settings.openai_base_url,
        )

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
