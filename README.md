# mem-plane

**The hybrid retrieval and context governance memory plane for production AI agents.**

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg?style=flat-square)](https://opensource.org/licenses/MIT)
[![Python 3.11+](https://img.shields.io/badge/python-3.11+-blue.svg?style=flat-square)](https://www.python.org/downloads/)
[![Strict Typing](https://img.shields.io/badge/mypy-strict-brightgreen.svg?style=flat-square)](https://mypy-lang.org/)
[![MCP Ready](https://img.shields.io/badge/MCP-Compatible-green.svg?style=flat-square)](https://modelcontextprotocol.io/)

---

## 1. System Vision & Problem Statement

`mem-plane` is an infrastructure-level memory plane and context governor for production AI agents. It bridges the gap between persistent search storage (SQLite locally, OpenSearch/PostgreSQL in production) and the LLM's active execution context window.

### Core Problems Solved
1. **Hardware KV-Cache Invalidation:** Prevents dynamic memory rewrites from busting prompt prefix caches. Guarantees 80–90% caching discounts on Claude, OpenAI, and vLLM by enforcing immutable prompt zones and cache breakpoints (`<!-- MEM_PLANE_CACHE_BREAKPOINT -->`).
2. **Technical Retrieval Accuracy:** Pure vector embeddings fail on exact code syntax, CLI flags, compiler errors, and UUIDs. `mem-plane` implements Reciprocal Rank Fusion (RRF) to merge BM25 lexical tokens with dense vectors without brittle manual score normalization.
3. **Context Window Flooding:** Avoids single-stage multi-kilobyte chunk dumps. Implements two-stage progressive disclosure where agents query lightweight metadata surrogates (~30 tokens) and selectively dereference full raw documents ($O(1)$) on demand.
4. **Dev-to-Prod Storage Parity:** Eliminates heavy infrastructure hurdles for local CLI agents. Defaults to embedded SQLite (`FTS5` + `sqlite-vec`) out of the box, with a one-flag config switch to PostgreSQL and OpenSearch in production clusters.

---

## 2. Architecture & The 4-Zone Context Governor

```
┌─────────────────────────────────────────────────────────────┐
│ ZONE 1: Static System & Pre-Token Hydration                 │
│ • Byte-identical across turns                               │
│ • Static system instructions                                │
│ • Deterministically formatted tenant rules & user state     │
├─────────────────────────────────────────────────────────────┤
│ ZONE 2: Cache Breakpoint Marker                             │
│ • Exact static string:                                      │
│   \n<!-- MEM_PLANE_CACHE_BREAKPOINT -->\n                   │
├─────────────────────────────────────────────────────────────┤
│ ZONE 3: Retrieved Memory Surrogates                         │
│ • Compact token surrogates (~30 tokens each)                │
│ • Deterministic sort by `fact_id` ascending                 │
├─────────────────────────────────────────────────────────────┤
│ ZONE 4: Active Dialogue Turns                               │
│ • Dynamic user messages and assistant responses             │
│ • Pruned/compressed tool outputs (EphemeralToolPruner)      │
└─────────────────────────────────────────────────────────────┘
```

---

## 3. Quickstart

### Installation
```bash
# Core package with local SQLite FTS5 and sqlite-vec
pip install "mem-plane[sqlite]"

# Optional production dependencies (PostgreSQL + OpenSearch)
pip install "mem-plane[production]"

# Model Context Protocol support
pip install "mem-plane[mcp]"
```

### Basic Usage

```python
import asyncio
from mem_plane.storage.sqlite.backend import SQLiteBackend
from mem_plane.pipeline.embeddings import DummyEmbeddingClient
from mem_plane.core.models import FactEntity

async def main():
    async with SQLiteBackend(":memory:") as backend:
        client = DummyEmbeddingClient()
        
        # Save a fact with embedding
        fact = FactEntity(
            fact_id="auth-001",
            tenant_id="default",
            summary="JWT expiry configured to 15m in auth.py",
            raw_content="Set ACCESS_TOKEN_EXPIRE_MINUTES = 15.",
            source_type="codebase",
        )
        vec = await client.embed_text(fact.summary + " " + fact.raw_content)
        await backend.save_fact_with_embedding(fact, vec)

        # Hybrid search using RRF
        query_vec = await client.embed_text("jwt timeout")
        hits = await backend.hybrid_search("auth.py JWT", query_vec, top_k=3)
        for hit in hits:
            print(f"[{hit.fact_id}] {hit.summary} (score: {hit.score:.4f})")

asyncio.run(main())
```

---

## 4. MCP Server & Agent Skills

`mem-plane` provides ready-to-use skills in `.claude/skills/`:
- **`memplane-search.md`**: Two-stage progressive disclosure protocol.
- **`memplane-provenance.md`**: Fact origin, commit SHA, and author verification protocol.
- **`memplane-trace.md`**: Pruned stack trace inspection protocol.
- **`memplane-remember.md`**: Architectural decision and invariant recording protocol.

Run the MCP daemon via stdio:
```bash
python -m mem_plane.server.mcp_server
```

---

## 5. Development & Testing

```bash
# Run tests
pytest

# Strict type checking
mypy src tests

# Lint and format
ruff check src tests
ruff format src tests
```
