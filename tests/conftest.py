"""Pytest fixtures for mem-plane test suite."""

import pytest

from mem_plane.pipeline.embeddings import DummyEmbeddingClient


@pytest.fixture
def dummy_embedding_client() -> DummyEmbeddingClient:
    """Fixture providing a deterministic 384-dimension dummy embedding client."""
    return DummyEmbeddingClient(dimension=384)
