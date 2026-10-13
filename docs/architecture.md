# `mem-plane` Architecture & Topology

## Overview

`mem-plane` acts as a context governor and systems-level memory plane between persistent storage and the active LLM context window.

```
                  ┌───────────────────────────────┐
                  │    Agent / MCP Host Client    │
                  └───────────────┬───────────────┘
                                  │
                                  ▼
                     MCP Server / FastMCP Protocol
                     ├── search_episodic_memory
                     ├── inspect_fact_provenance
                     ├── dereference_raw_trace
                     └── record_fact
                                  │
         ┌────────────────────────┴────────────────────────┐
         │                                                 │
         ▼                                                 ▼
[Local Development]                              [Production Cluster]
`SQLiteBackend`                                  Postgres + OpenSearch
├── `facts` (Relational)                         ├── PostgreSQL (`asyncpg`)
├── `facts_fts` (FTS5 BM25)                      │   ├── `facts` (JSONB)
├── `facts_vec` (sqlite-vec 384-d)               │   ├── `hydration_profiles`
├── `hydration_profiles`                         │   └── `tool_traces`
└── `tool_traces`                                └── OpenSearch (`opensearch-py`)
                                                     ├── BM25 (summary^2, content)
                                                     └── Lucene HNSW knn_vector
```

## Read Flow: Two-Stage Progressive Disclosure
1. **Stage 1 (Surrogate Query):** The agent runs `search_episodic_memory(query, top_k=3)`.
   - Backend queries lexical BM25 and vector $k$-NN in parallel.
   - Rankings are merged via Reciprocal Rank Fusion:
     $$RRF\_Score(d) = \sum_{m \in M} \frac{1}{60 + \text{rank}_m(d)}$$
   - Returns lightweight surrogate summaries (~30 tokens each).
2. **Stage 2 ($O(1)$ Dereferencing):**
   - If the surrogate is sufficient, no further tokens are used.
   - If full code or audit trail is needed, agent invokes `inspect_fact_provenance(fact_id)`.

## Write Flow: Ephemeral Tool Pruner
1. Noisy tool output (e.g. test runner, compiler trace) exceeds 15 lines or 600 characters.
2. `EphemeralToolPruner` stores the full trace out-of-band in `tool_traces`.
3. Injects preview (first 5 lines + `[TOOL OUTPUT TRUNCATED: ... Trace ID: ...]` + last 5 lines) into active dialogue turn.
