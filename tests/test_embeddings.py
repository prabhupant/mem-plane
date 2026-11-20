"""Unit tests for DummyEmbeddingClient, FastEmbedClient, OpenAIEmbeddingClient, and build_embedding_client."""

import math
import os
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from mem_plane.config import Settings
from mem_plane.pipeline.embeddings import (
    DummyEmbeddingClient,
    FastEmbedClient,
    OpenAIEmbeddingClient,
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


@pytest.mark.asyncio
async def test_openai_embedding_client_mocked() -> None:
    fake_item1 = MagicMock()
    fake_item1.embedding = [3.0, 4.0]
    fake_item1.index = 0

    fake_item2 = MagicMock()
    fake_item2.embedding = [1.0, 0.0]
    fake_item2.index = 1

    fake_resp = MagicMock()
    fake_resp.data = [fake_item2, fake_item1]  # test out-of-order sorting

    mock_openai_client = MagicMock()
    mock_openai_client.embeddings.create = AsyncMock(return_value=fake_resp)

    client = OpenAIEmbeddingClient(
        model_name="text-embedding-3-small",
        dimension=384,
        api_key="test-key",
    )
    client._client = mock_openai_client

    assert client.dimension == 384

    # Test embed_documents
    docs = await client.embed_documents(["doc1", "doc2"])
    assert len(docs) == 2
    assert docs[0] == [0.6, 0.8]  # normalized [3.0, 4.0]
    assert docs[1] == [1.0, 0.0]

    mock_openai_client.embeddings.create.assert_called_once_with(
        model="text-embedding-3-small",
        input=["doc1", "doc2"],
        dimensions=384,
    )

    # Test empty documents
    assert await client.embed_documents([]) == []

    # Test embed_query and aliases
    mock_single_resp = MagicMock()
    mock_item = MagicMock()
    mock_item.embedding = [0.0, 5.0]
    mock_item.index = 0
    mock_single_resp.data = [mock_item]
    mock_openai_client.embeddings.create = AsyncMock(return_value=mock_single_resp)

    q_vec = await client.embed_query("query")
    assert q_vec == [0.0, 1.0]

    alias_single = await client.embed_text("query")
    assert alias_single == [0.0, 1.0]

    alias_batch = await client.embed_batch(["query"])
    assert alias_batch == [[0.0, 1.0]]


@pytest.mark.asyncio
async def test_openai_embedding_client_ada_no_dimensions() -> None:
    fake_item = MagicMock()
    fake_item.embedding = [1.0] * 1536
    fake_item.index = 0
    fake_resp = MagicMock()
    fake_resp.data = [fake_item]

    mock_openai_client = MagicMock()
    mock_openai_client.embeddings.create = AsyncMock(return_value=fake_resp)

    client = OpenAIEmbeddingClient(
        model_name="text-embedding-ada-002",
        dimension=1536,
        api_key="test-key",
    )
    client._client = mock_openai_client

    await client.embed_documents(["ada test"])
    mock_openai_client.embeddings.create.assert_called_once_with(
        model="text-embedding-ada-002",
        input=["ada test"],
    )


def test_build_embedding_client_openai_missing_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    settings = Settings(embedding_provider="openai", openai_api_key=None)
    client = build_embedding_client(settings)
    assert client is None


def test_build_embedding_client_openai_missing_package() -> None:
    settings = Settings(embedding_provider="openai", openai_api_key="sk-test")
    with patch.dict("sys.modules", {"openai": None}):
        client = build_embedding_client(settings)
        assert client is None


def test_build_embedding_client_openai_configured() -> None:
    settings = Settings(
        embedding_provider="openai",
        openai_api_key="sk-test",
        embedding_dimension=384,
    )
    client = build_embedding_client(settings)
    assert isinstance(client, OpenAIEmbeddingClient)
    assert client.dimension == 384


@pytest.mark.asyncio
async def test_openai_embedding_client_live() -> None:
    api_key = os.environ.get("OPENAI_API_KEY")
    if not api_key:
        pytest.skip("OPENAI_API_KEY not set in environment")

    client = OpenAIEmbeddingClient(model_name="text-embedding-3-small", dimension=384)
    assert client.dimension == 384

    query_vec = await client.embed_query("testing live openai embeddings")
    assert len(query_vec) == 384
    norm = math.sqrt(sum(x * x for x in query_vec))
    assert pytest.approx(norm, rel=1e-4) == 1.0

    doc_vecs = await client.embed_documents(["passage alpha", "passage beta"])
    assert len(doc_vecs) == 2
    assert len(doc_vecs[0]) == 384
    assert len(doc_vecs[1]) == 384
    assert pytest.approx(math.sqrt(sum(x * x for x in doc_vecs[0])), rel=1e-4) == 1.0
