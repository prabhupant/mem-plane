"""OpenSearch HNSW knn_vector and BM25 hybrid search client."""

from types import TracebackType
from typing import Any, Self

from opensearchpy import AsyncOpenSearch, OpenSearchException

from mem_plane.core.models import SurrogateHit
from mem_plane.core.rrf import reciprocal_rank_fusion
from mem_plane.storage.protocols import SearchIndex


class OpenSearchBackend(SearchIndex):
    """OpenSearch hybrid search backend combining BM25 and Lucene HNSW k-NN with RRF."""

    def __init__(
        self,
        opensearch_url: str = "http://localhost:9200",
        index_name: str = "mem_plane_facts",
        dimension: int = 384,
        client: Any | None = None,
    ) -> None:
        self.opensearch_url = opensearch_url
        self.index_name = index_name
        self.dimension = dimension
        self._client = client
        self._owns_client = client is None

    async def initialize(self) -> None:
        """Create OpenSearch index with k-NN vector and text mappings if it does not exist."""
        if self._client is None:
            self._client = AsyncOpenSearch(hosts=[self.opensearch_url])

        exists = await self._client.indices.exists(index=self.index_name)
        if not exists:
            mapping = {
                "settings": {
                    "index": {
                        "knn": True,
                    }
                },
                "mappings": {
                    "properties": {
                        "fact_id": {"type": "keyword"},
                        "summary": {"type": "text"},
                        "content": {"type": "text"},
                        "embedding": {
                            "type": "knn_vector",
                            "dimension": self.dimension,
                            "method": {
                                "name": "hnsw",
                                "space_type": "cosinesimil",
                                "engine": "lucene",
                            },
                        },
                        "source_path": {"type": "keyword"},
                    }
                },
            }
            await self._client.indices.create(index=self.index_name, body=mapping)

    async def close(self) -> None:
        """Close OpenSearch client if owned by this instance."""
        if self._client is not None and self._owns_client:
            await self._client.close()
            self._client = None

    async def __aenter__(self) -> Self:
        await self.initialize()
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: TracebackType | None,
    ) -> None:
        await self.close()

    def _get_client(self) -> Any:
        if self._client is None:
            raise RuntimeError("OpenSearchBackend is not initialized. Call initialize() first.")
        return self._client

    async def index_vector_and_tokens(
        self,
        fact_id: str,
        summary: str,
        content: str,
        vector: list[float],
        source_path: str | None = None,
    ) -> None:
        """Index fact text and embedding vector into OpenSearch."""
        client = self._get_client()
        doc = {
            "fact_id": fact_id,
            "summary": summary,
            "content": content,
            "embedding": vector,
            "source_path": source_path,
        }
        await client.index(
            index=self.index_name,
            id=fact_id,
            body=doc,
            refresh=True,
        )

    async def hybrid_search(
        self, query: str, query_vector: list[float], top_k: int = 5
    ) -> list[SurrogateHit]:
        """Execute parallel lexical and vector search in OpenSearch, fusing via RRF."""
        if top_k <= 0:
            return []

        client = self._get_client()
        candidate_limit = max(top_k * 3, 20)
        cached_meta: dict[str, dict[str, Any]] = {}

        # 1. Lexical BM25 retrieval
        lexical_ranks: list[str] = []
        if query.strip():
            bm25_body = {
                "query": {
                    "multi_match": {
                        "query": query,
                        "fields": ["summary^2", "content"],
                    }
                },
                "size": candidate_limit,
            }
            try:
                bm25_res = await client.search(index=self.index_name, body=bm25_body)
                for hit in bm25_res.get("hits", {}).get("hits", []):
                    doc_id = str(hit.get("_id") or hit.get("_source", {}).get("fact_id"))
                    lexical_ranks.append(doc_id)
                    if doc_id not in cached_meta:
                        cached_meta[doc_id] = hit.get("_source", {})
            except (OpenSearchException, KeyError, ValueError, RuntimeError):
                lexical_ranks = []

        # 2. Dense k-NN retrieval
        dense_ranks: list[str] = []
        if query_vector:
            knn_body = {
                "query": {
                    "knn": {
                        "embedding": {
                            "vector": query_vector,
                            "k": candidate_limit,
                        }
                    }
                },
                "size": candidate_limit,
            }
            try:
                knn_res = await client.search(index=self.index_name, body=knn_body)
                for hit in knn_res.get("hits", {}).get("hits", []):
                    doc_id = str(hit.get("_id") or hit.get("_source", {}).get("fact_id"))
                    dense_ranks.append(doc_id)
                    if doc_id not in cached_meta:
                        cached_meta[doc_id] = hit.get("_source", {})
            except (OpenSearchException, KeyError, ValueError, RuntimeError):
                dense_ranks = []

        # 3. Reciprocal Rank Fusion
        fused = reciprocal_rank_fusion(
            dense_ranks=dense_ranks,
            lexical_ranks=lexical_ranks,
            k=60,
            top_n=top_k,
        )

        # 4. Format SurrogateHit items
        surrogate_hits: list[SurrogateHit] = []
        for doc_id, score in fused:
            meta = cached_meta.get(doc_id, {})
            summary = meta.get("summary", f"Fact {doc_id}")
            source_path = meta.get("source_path")

            surrogate_hits.append(
                SurrogateHit(
                    fact_id=doc_id,
                    summary=summary,
                    score=score,
                    source_path=source_path,
                    token_estimate=30,
                )
            )

        return surrogate_hits
