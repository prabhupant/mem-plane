"""Unit tests for DummyEmbeddingClient, FastEmbedClient, and build_embedding_client."""

import math
from unittest.mock import patch

import pytest

from mem_plane.config import Settings
from mem_plane.pipeline.embeddings import (
    DummyEmbeddingClient,
    FastEmbedClient,
    build_embedding_client,
)


@pytest.mark.asyncio
async def test_dummy_embedding_determinism(dummy_embedding_client: DummyEmbeddingClient) -> None:
    text = "SELECT * FROM users WHERE active = 1;"
    vec1 = await dummy_embedding_client.embed_query(text)
    vec2 = await dummy_embedding_client.embed_query(text)

    assert len(vec1) == 384
    assert len(vec2) == 384
    assert vec1 == vec2


@pytest.mark.asyncio
async def test_dummy_embedding_different_texts(
    dummy_embedding_client: DummyEmbeddingClient,
) -> None:
    vec1 = await dummy_embedding_client.embed_query("query alpha")
    vec2 = await dummy_embedding_client.embed_query("query beta")

    assert vec1 != vec2


@pytest.mark.asyncio
async def test_dummy_embedding_normalization(dummy_embedding_client: DummyEmbeddingClient) -> None:
    vec = await dummy_embedding_client.embed_query("normalize me")
    l2_norm = math.sqrt(sum(x * x for x in vec))
    assert pytest.approx(l2_norm, rel=1e-5) == 1.0


@pytest.mark.asyncio
async def test_dummy_embedding_batch(dummy_embedding_client: DummyEmbeddingClient) -> None:
    texts = ["one", "two", "three"]
    vectors = await dummy_embedding_client.embed_documents(texts)

    assert len(vectors) == 3
    for vec in vectors:
        assert len(vec) == 384
        l2_norm = math.sqrt(sum(x * x for x in vec))
        assert pytest.approx(l2_norm, rel=1e-5) == 1.0


@pytest.mark.asyncio
async def test_dummy_embedding_wrappers(dummy_embedding_client: DummyEmbeddingClient) -> None:
    single = await dummy_embedding_client.embed_text("test single")
    batch = await dummy_embedding_client.embed_batch(["test single"])
    assert single == batch[0]


def test_build_embedding_client_none() -> None:
    settings = Settings(embedding_provider="none")
    client = build_embedding_client(settings)
    assert client is None


def test_build_embedding_client_dummy_refused_without_flag(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("MEM_PLANE_ALLOW_DUMMY_EMBEDDINGS", raising=False)
    settings = Settings(embedding_provider="dummy", allow_dummy_embeddings=False)
    with pytest.raises(RuntimeError, match="MEM_PLANE_ALLOW_DUMMY_EMBEDDINGS=1"):
        build_embedding_client(settings)


def test_build_embedding_client_dummy_allowed(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MEM_PLANE_ALLOW_DUMMY_EMBEDDINGS", "1")
    settings = Settings(embedding_provider="dummy")
    client = build_embedding_client(settings)
    assert isinstance(client, DummyEmbeddingClient)
    assert client.dimension == 384


def test_build_embedding_client_fastembed_fallback_warning() -> None:
    settings = Settings(embedding_provider="fastembed")
    with patch.dict("sys.modules", {"fastembed": None}):
        client = build_embedding_client(settings)
        assert client is None


def test_build_embedding_client_unknown_provider() -> None:
    settings = Settings()
    object.__setattr__(settings, "embedding_provider", "unknown_provider")
    with pytest.raises(ValueError, match="Unsupported embedding provider"):
        build_embedding_client(settings)


@pytest.mark.embed
@pytest.mark.asyncio
async def test_fastembed_client_live() -> None:
    fastembed = pytest.importorskip("fastembed")  # noqa: F841
    client = FastEmbedClient(model_name="BAAI/bge-small-en-v1.5")
    assert client.dimension == 384

    query_vec = await client.embed_query("test query")
    assert len(query_vec) == 384
    norm = math.sqrt(sum(x * x for x in query_vec))
    assert pytest.approx(norm, rel=1e-4) == 1.0

    docs = await client.embed_documents(["document one", "document two"])
    assert len(docs) == 2
    for doc_vec in docs:
        assert len(doc_vec) == 384
        dnorm = math.sqrt(sum(x * x for x in doc_vec))
        assert pytest.approx(dnorm, rel=1e-4) == 1.0
