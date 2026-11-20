"""Retrieval evaluation runner (Section 15.3).

Evaluates keyword-only, vector-only, and fused retrieval on:
- Recall@1
- Recall@5
- MRR (Mean Reciprocal Rank)
- Superseded result rate
"""

from __future__ import annotations

import asyncio
import json
import os
import tempfile
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from mem_plane.config import Settings
from mem_plane.core.models import Fact, FactKind, FactStatus
from mem_plane.pipeline.embeddings import DummyEmbeddingClient, FastEmbedClient
from mem_plane.storage.protocols import SearchRequest
from mem_plane.storage.sqlite.store import SQLiteStore

EVAL_DIR = Path(__file__).parent


class Metrics:
    def __init__(self, name: str) -> None:
        self.name = name
        self.r1_scores: list[float] = []
        self.r5_scores: list[float] = []
        self.mrr_scores: list[float] = []
        self.superseded_count = 0
        self.total_returned = 0

    def add(
        self, retrieved_refs: list[str], relevant_refs: set[str], superseded_ids: set[str]
    ) -> None:
        self.total_returned += len(retrieved_refs)
        self.superseded_count += sum(1 for r in retrieved_refs if r in superseded_ids)

        # Recall@1
        top1 = retrieved_refs[:1]
        self.r1_scores.append(1.0 if any(r in relevant_refs for r in top1) else 0.0)

        # Recall@5
        top5 = retrieved_refs[:5]
        self.r5_scores.append(1.0 if any(r in relevant_refs for r in top5) else 0.0)

        # MRR
        rr = 0.0
        for rank, ref in enumerate(top5, start=1):
            if ref in relevant_refs:
                rr = 1.0 / rank
                break
        self.mrr_scores.append(rr)

    @property
    def recall_1(self) -> float:
        return sum(self.r1_scores) / len(self.r1_scores) if self.r1_scores else 0.0

    @property
    def recall_5(self) -> float:
        return sum(self.r5_scores) / len(self.r5_scores) if self.r5_scores else 0.0

    @property
    def mrr(self) -> float:
        return sum(self.mrr_scores) / len(self.mrr_scores) if self.mrr_scores else 0.0

    @property
    def superseded_rate(self) -> float:
        return self.superseded_count / self.total_returned if self.total_returned else 0.0


def load_dataset(
    facts_file: Path, queries_file: Path
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    facts: list[dict[str, Any]] = []
    with open(facts_file, "r", encoding="utf-8") as file_in:
        for line in file_in:
            if line.strip():
                facts.append(json.loads(line))

    queries: list[dict[str, Any]] = []
    with open(queries_file, "r", encoding="utf-8") as file_in:
        for line in file_in:
            if line.strip():
                queries.append(json.loads(line))
    return facts, queries


async def run_evaluation() -> int:
    facts_file = EVAL_DIR / "facts.jsonl"
    queries_file = EVAL_DIR / "queries.jsonl"

    if not facts_file.exists() or not queries_file.exists():
        print("Missing dataset files. Run generate_dataset.py first.")
        return 1

    facts, queries = load_dataset(facts_file, queries_file)
    print(f"Loaded {len(facts)} facts and {len(queries)} queries.")

    superseded_ids = {f["fact_id"] for f in facts if f.get("status") == "superseded"}

    # Use FastEmbedClient if available, otherwise DummyEmbeddingClient with warning
    try:
        import fastembed  # noqa: F401

        client: FastEmbedClient | DummyEmbeddingClient = FastEmbedClient(
            model_name="BAAI/bge-small-en-v1.5"
        )
        print("Using FastEmbedClient with model BAAI/bge-small-en-v1.5.")
    except ImportError:
        os.environ["MEM_PLANE_ALLOW_DUMMY_EMBEDDINGS"] = "1"
        client = DummyEmbeddingClient(dimension=384)
        print("Warning: fastembed not installed. Using DummyEmbeddingClient.")

    with tempfile.TemporaryDirectory() as tmp_dir:
        db_path = Path(tmp_dir) / "eval.db"
        store = SQLiteStore(database=db_path)

        try:
            print("Indexing facts...")
            t0 = time.time()
            for fact_dict in facts:
                fact_obj = Fact(
                    fact_id=fact_dict["fact_id"],
                    project_id=fact_dict["project_id"],
                    kind=FactKind(fact_dict["kind"]),
                    summary=fact_dict["summary"],
                    body=fact_dict["body"],
                    status=FactStatus(fact_dict["status"]),
                    supersedes=fact_dict.get("supersedes"),
                    superseded_by=fact_dict.get("superseded_by"),
                    created_at=datetime.now(UTC),
                )
                store.create_fact(fact_obj)

            # Compute embeddings in batches of 64
            pending = store.pending_embeddings(limit=500)
            texts = [item.text_to_embed for item in pending]
            if texts:
                vectors = await client.embed_documents(texts)
                store.write_vectors(list(zip(pending, vectors, strict=True)))

            print(f"Indexed {len(facts)} facts in {time.time() - t0:.2f}s.")

            print("Pre-computing query vectors...")
            t1 = time.time()
            query_texts = [q["query"] for q in queries]
            # Embed queries with embed_query prefix
            query_vectors: list[list[float]] = []
            for qt in query_texts:
                qv = await client.embed_query(qt)
                query_vectors.append(qv)
            print(f"Computed {len(queries)} query vectors in {time.time() - t1:.2f}s.")

            # Evaluation metrics
            kw_metrics = Metrics("Keyword Only")
            vec_metrics = Metrics("Vector Only")
            fused_metrics = Metrics("Fused (Default)")

            # Store configurations
            kw_settings = Settings(weight_fact_keyword=1.0, weight_fact_vector=0.0)
            vec_settings = Settings(weight_fact_keyword=0.0, weight_fact_vector=1.0)
            fused_settings = Settings(weight_fact_keyword=1.0, weight_fact_vector=1.0)

            for i, q in enumerate(queries):
                proj = q["project"]
                q_text = q["query"]
                relevant = set(q["relevant"])
                q_vec = query_vectors[i]

                # 1. Keyword only
                store.settings = kw_settings
                req_kw = SearchRequest(project_id=proj, query=q_text, top_k=5)
                hits_kw = store.search(req_kw, query_vector=None)
                kw_metrics.add([h.ref for h in hits_kw], relevant, superseded_ids)

                # 2. Vector only
                store.settings = vec_settings
                req_vec = SearchRequest(project_id=proj, query=q_text, top_k=5)
                hits_vec = store.search(req_vec, query_vector=q_vec)
                vec_metrics.add([h.ref for h in hits_vec], relevant, superseded_ids)

                # 3. Fused (Default)
                store.settings = fused_settings
                req_fused = SearchRequest(project_id=proj, query=q_text, top_k=5)
                hits_fused = store.search(req_fused, query_vector=q_vec)
                fused_metrics.add([h.ref for h in hits_fused], relevant, superseded_ids)

            # Print report
            print("\n" + "=" * 65)
            print("                 RETRIEVAL EVALUATION REPORT")
            print("=" * 65)
            header = f"{'Method':<18} | {'Recall@1':<10} | {'Recall@5':<10} | {'MRR':<10} | {'Superseded Rate':<15}"
            print(header)
            print("-" * 65)

            for m in [kw_metrics, vec_metrics, fused_metrics]:
                print(
                    f"{m.name:<18} | {m.recall_1:<10.3f} | {m.recall_5:<10.3f} | {m.mrr:<10.3f} | {m.superseded_rate:<15.3f}"
                )
            print("=" * 65)

            # Targets check (§15.3)
            passed = True
            print("\nTarget Verification:")
            # Target 1: Fused Recall@5 >= 0.85
            t1 = fused_metrics.recall_5 >= 0.85
            print(
                f"  [1] Fused Recall@5 >= 0.85: {'PASS' if t1 else 'FAIL'} ({fused_metrics.recall_5:.3f})"
            )
            if not t1:
                passed = False

            # Target 2: Fused Recall@5 higher than keyword only
            t2 = fused_metrics.recall_5 >= kw_metrics.recall_5
            print(
                f"  [2] Fused Recall@5 >= Keyword Recall@5: {'PASS' if t2 else 'FAIL'} ({fused_metrics.recall_5:.3f} vs {kw_metrics.recall_5:.3f})"
            )
            if not t2:
                passed = False

            # Target 3: Fused Recall@5 higher than vector only
            t3 = fused_metrics.recall_5 >= vec_metrics.recall_5
            print(
                f"  [3] Fused Recall@5 >= Vector Recall@5: {'PASS' if t3 else 'FAIL'} ({fused_metrics.recall_5:.3f} vs {vec_metrics.recall_5:.3f})"
            )
            if not t3:
                passed = False

            # Target 4: Superseded rate 0.0 with default settings
            t4 = fused_metrics.superseded_rate == 0.0
            print(
                f"  [4] Superseded Rate == 0.0: {'PASS' if t4 else 'FAIL'} ({fused_metrics.superseded_rate:.3f})"
            )
            if not t4:
                passed = False

            return 0 if passed else 1

        finally:
            store.close()


def main() -> None:
    code = asyncio.run(run_evaluation())
    raise SystemExit(code)


if __name__ == "__main__":
    main()
