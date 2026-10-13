"""Unit tests for DummyEmbeddingClient."""

import math

import pytest

from mem_plane.pipeline.embeddings import DummyEmbeddingClient


@pytest.mark.asyncio
async def test_dummy_embedding_determinism(dummy_embedding_client: DummyEmbeddingClient) -> None:
    text = "SELECT * FROM users WHERE active = 1;"
    vec1 = await dummy_embedding_client.embed_text(text)
    vec2 = await dummy_embedding_client.embed_text(text)

    assert len(vec1) == 384
    assert len(vec2) == 384
    assert vec1 == vec2


@pytest.mark.asyncio
async def test_dummy_embedding_different_texts(
    dummy_embedding_client: DummyEmbeddingClient,
) -> None:
    vec1 = await dummy_embedding_client.embed_text("query alpha")
    vec2 = await dummy_embedding_client.embed_text("query beta")

    assert vec1 != vec2


@pytest.mark.asyncio
async def test_dummy_embedding_normalization(dummy_embedding_client: DummyEmbeddingClient) -> None:
    vec = await dummy_embedding_client.embed_text("normalize me")
    l2_norm = math.sqrt(sum(x * x for x in vec))
    assert pytest.approx(l2_norm, rel=1e-5) == 1.0


@pytest.mark.asyncio
async def test_dummy_embedding_batch(dummy_embedding_client: DummyEmbeddingClient) -> None:
    texts = ["one", "two", "three"]
    vectors = await dummy_embedding_client.embed_batch(texts)

    assert len(vectors) == 3
    for vec in vectors:
        assert len(vec) == 384
