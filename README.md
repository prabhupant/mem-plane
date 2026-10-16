# 🧠 `mem-plane`

<div align="center">

### **The Hybrid Retrieval & Context Governance Memory Plane for Production AI Agents**

*Stop busting your hardware KV-cache with unstructured prompt dumping. Build on deterministic retrieval contracts, hybrid RRF search, and zero-infra local-to-production storage parity.*

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg?style=flat-square)](https://opensource.org/licenses/MIT)
[![Python 3.11+](https://img.shields.io/badge/Python-3.11%20%7C%203.12-blue.svg?style=flat-square)](https://www.python.org/downloads/)
[![Strict Typing](https://img.shields.io/badge/mypy-strict-brightgreen.svg?style=flat-square)](https://mypy-lang.org/)
[![Ruff](https://img.shields.io/endpoint?url=https://raw.githubusercontent.com/astral-sh/ruff/main/assets/badge/v2.json&style=flat-square)](https://github.com/astral-sh/ruff)
[![MCP Ready](https://img.shields.io/badge/MCP-2.0%20Compatible-green.svg?style=flat-square)](https://modelcontextprotocol.io/)
[![Tests](https://img.shields.io/badge/tests-34%20passed-success.svg?style=flat-square)](https://github.com/prabhupant/mem-plane)

[Key Problems Solved](#1-key-problems-solved) • [The 4-Zone Governor](#2-the-4-zone-context-governor) • [How It Works](#3-core-architecture--mechanics) • [Claude Code & AGY Setup](#4-claude-code--antigravity-agy-setup) • [Python SDK](#5-python-sdk--quickstart) • [Inspecting the Database](#6-inspecting-the-database) • [Dev-to-Prod Parity](#7-dev-to-prod-storage-parity)

</div>

---

## 1. Key Problems Solved

Most agent memory frameworks treat context like an unstructured fact dumper. They pull top-$k$ conversational chunks and inject them into dynamic prompt sections—mutating prefix tokens on every turn, destroying provider prompt-caching discounts, and failing on exact syntactic code identifiers.

`mem-plane` solves the four fundamental failure modes of production agent memory:

| # | The Failure Mode in Naive Agents | How `mem-plane` Solves It |
| :--- | :--- | :--- |
| **1** | **Hardware KV-Cache Invalidation:** Dynamic memory rewrites bust prompt prefix caches on every turn. | Enforces **4 immutable prompt zones** with static cache breakpoints (`<!-- MEM_PLANE_CACHE_BREAKPOINT -->`), locking in **80–90% caching discounts** on Claude, OpenAI, and vLLM. |
| **2** | **Technical Retrieval Blindness:** Pure vectors fail on exact symbols, compiler errors, and CLI flags. | Implements **Reciprocal Rank Fusion (RRF)** to fuse BM25 lexical tokens with dense vectors without fragile manual score normalization. |
| **3** | **Context Window Flooding:** Single-stage RAG dumps multi-kilobyte chunks into the prompt. | Implements **Two-Stage Progressive Disclosure**: agents query ~30-token surrogate summaries and selectively dereference raw bodies ($O(1)$) only when needed. |
| **4** | **Heavy Infrastructure Friction:** Local agents require complex Docker setups for vector stores. | **Zero-Infra Storage Parity:** Embedded SQLite (`FTS5` + `sqlite-vec`) out-of-the-box locally, with a one-flag config switch to PostgreSQL & OpenSearch in production. |

---

## 2. The 4-Zone Context Governor

Prompt caching in frontier models works strictly **left-to-right**. Any character change near the top forces the hardware to recompute the entire prompt from that point onward. 

`mem-plane` structures every prompt into 4 mathematically disciplined zones:

```text
┌─────────────────────────────────────────────────────────────┐
│ ZONE 1: Static System & Pre-Token Hydration                 │
│ • Byte-identical across turns                               │
│ • Static system instructions                                │
│ • Deterministically formatted tenant rules & user state     │
├─────────────────────────────────────────────────────────────┤
│ ZONE 2: Cache Breakpoint Delimiter Marker                   │
│ • Exact static string:                                      │
│   \n<!-- MEM_PLANE_CACHE_BREAKPOINT -->\n                   │
├─────────────────────────────────────────────────────────────┤
│ ZONE 3: Retrieved Memory Surrogates                         │
│ • Compact token surrogates (~30 tokens each)                │
│ • Deterministic sort by `fact_id` ascending                 │
│ • Identical recall subsets produce 100% identical tokens   │
├─────────────────────────────────────────────────────────────┤
│ ZONE 4: Active Dialogue Turns                               │
│ • Dynamic user messages and assistant responses             │
│ • Pruned/compressed tool outputs (EphemeralToolPruner)      │
└─────────────────────────────────────────────────────────────┘
```

---

## 3. Core Architecture & Mechanics

```
                       User Prompt / Agent Request
                                    │
                                    ▼
                      Model Context Protocol (MCP)
                      ├── search_episodic_memory (Hybrid RRF)
                      ├── inspect_fact_provenance (O(1) Raw Text)
                      ├── dereference_raw_trace (Unpruned Logs)
                      └── record_fact (Episodic Ingestion)
                                    │
            ┌───────────────────────┴───────────────────────┐
            ▼                                               ▼
   [Local Development]                            [Production Cluster]
   `SQLiteBackend`                                Postgres + OpenSearch
   ├── `facts` (Relational JSON)                  ├── PostgreSQL (`asyncpg`)
   ├── `facts_fts` (FTS5 BM25)                    │   ├── `facts` (JSONB)
   ├── `facts_vec` (sqlite-vec 384-d)             │   ├── `hydration_profiles`
   ├── `hydration_profiles`                       │   └── `tool_traces`
   └── `tool_traces`                              └── OpenSearch (`opensearch-py`)
                                                      ├── BM25 (`summary^2`, `content`)
                                                      └── Lucene HNSW knn_vector
```

### Reciprocal Rank Fusion (RRF)
Combines dense semantic vectors with lexical BM25 token frequencies without brittle score normalization:
$$RRF\_Score(d) = \sum_{m \in M} \frac{1}{60 + \text{rank}_m(d)}$$
- **Semantic Queries** (*"how to refresh auth sessions"*): Dense vector search floats relevant concepts to the top.
- **Syntactic Queries** (*"--no-cov"*, *"SIGSEGV 139"*, *"auth_jwt_v2"*): BM25 lexical search finds exact tokens.
- **Both Match:** The document is scored as the undeniable top result.

### Ephemeral Tool Pruning
Noisy test suites (`pytest`), compiler dumps, and terminal outputs can easily spam 200+ lines into the context window.
1. Any tool output exceeding **15 lines** or **600 characters** is automatically intercepted.
2. The full raw trace is saved out-of-band to database storage.
3. The active conversation turn receives a high-signal 10-line preview with an actionable `Trace ID`:
```text
[TOOL OUTPUT TRUNCATED: 184 lines. Trace ID: trc-a1b2c3. Call dereference_raw_trace('trc-a1b2c3') for full trace.]
tests/test_crypto.py:12 ...
... [truncated 174 lines] ...
FAILED tests/test_crypto.py::test_argon2 - AssertionError: hash mismatch
```
4. The agent can examine the preview, dereference the full trace via `dereference_raw_trace` if needed, and **never re-executes heavy build/test commands** just to re-read output.

---

## 4. Claude Code & Antigravity (AGY) Setup

`mem-plane` provides ready-to-use skills and configurations for both **Claude Code** and **Google Antigravity (AGY)** out of the box.

### A. Claude Code Setup

Run this single command in your project directory:
```bash
claude mcp add mem-plane \
  -e MEM_PLANE_STORAGE_BACKEND=sqlite \
  -e MEM_PLANE_SQLITE_DB_PATH=/path/to/mem-plane/agent_memory.db \
  -- /path/to/mem-plane/.venv/bin/python -m mem_plane.server.mcp_server
```

Claude Code will automatically detect the server and activate the skills in [`.claude/skills/`](./.claude/skills):
* **`memplane-search.md`**: Guides the agent to query ~30-token surrogates first.
* **`memplane-provenance.md`**: Teaches the agent to audit fact origins and commit SHAs.
* **`memplane-trace.md`**: Instructs the agent how to inspect truncated outputs without re-running commands.
* **`memplane-remember.md`**: Teaches the agent to extract and record durable architectural decisions and rules.

### B. Google Antigravity (AGY) Setup

Antigravity automatically discovers project customizations in [`.agents/`](./.agents):
* **MCP Server Config:** [`.agents/mcp_config.json`](./.agents/mcp_config.json)
* **Antigravity Skills:** [`.agents/skills/`](./.agents/skills)

Launch `agy` or Antigravity IDE in this directory. The 4 tools and memory skills will be loaded automatically into your session!

---

## 5. Python SDK & Quickstart

### Installation
```bash
# Core package with local SQLite FTS5 and sqlite-vec
pip install "mem-plane[sqlite]"

# Optional production cluster dependencies (PostgreSQL + OpenSearch)
pip install "mem-plane[production]"

# Model Context Protocol support
pip install "mem-plane[mcp]"
```

### Basic Usage

```python
import asyncio
from mem_plane.core.models import FactEntity
from mem_plane.pipeline.embeddings import DummyEmbeddingClient
from mem_plane.storage.sqlite.backend import SQLiteBackend

async def main():
    async with SQLiteBackend("agent_memory.db") as backend:
        client = DummyEmbeddingClient()

        # 1. Store an architectural fact with dense embeddings
        fact = FactEntity(
            fact_id="auth-001",
            tenant_id="default",
            summary="JWT expiry configured to 15m in auth_service.py",
            raw_content="Set ACCESS_TOKEN_EXPIRE_MINUTES = 15 to enforce stricter token turnover.",
            source_type="codebase",
            provenance_metadata={"author": "alice", "path": "auth_service.py"},
        )
        vec = await client.embed_text(fact.summary + " " + fact.raw_content)
        await backend.save_fact_with_embedding(fact, vec)

        # 2. Hybrid search (Lexical BM25 + Vector k-NN fused with RRF)
        query_vec = await client.embed_text("jwt expiration timeout")
        hits = await backend.hybrid_search("auth_service.py JWT", query_vec, top_k=3)

        for hit in hits:
            print(f"[{hit.fact_id}] {hit.summary} (score: {hit.score:.4f}, path: {hit.source_path})")

asyncio.run(main())
```

---

## 6. Inspecting the Database

The local database lives in `agent_memory.db` in your project root.

### Quick Terminal Inspection
```bash
.venv/bin/python -c "
import sqlean as sqlite3
conn = sqlite3.connect('agent_memory.db')
cursor = conn.cursor()
print('=== 🧠 STORED FACTS ===')
for row in cursor.execute('SELECT fact_id, summary, source_type, created_at FROM facts').fetchall():
    print(f'[{row[0]}] ({row[2]} @ {row[3]}):\n  {row[1]}\n')
"
```

### Interactive SQLite Shell
```bash
sqlite3 agent_memory.db
```
```sql
.mode column
.headers on
SELECT fact_id, summary, source_type, created_at FROM facts;
SELECT trace_id, length(raw_trace) AS bytes, created_at FROM tool_traces;
.quit
```

*(Note: SQLite WAL sidecar files `agent_memory.db-wal` and `agent_memory.db-shm` are enabled for high-concurrency read/writes and are automatically ignored in Git.)*

---

## 7. Dev-to-Prod Storage Parity

To scale from local SQLite to a production cluster, **change zero lines of code**. Simply switch your environment variables:

```bash
# Enable production mode
export MEM_PLANE_STORAGE_BACKEND=production

# Point to your production infrastructure
export MEM_PLANE_OPENSEARCH_URL=https://opensearch.internal:9200
export MEM_PLANE_OPENSEARCH_INDEX=mem_plane_facts
export MEM_PLANE_POSTGRES_DSN=postgresql://user:pass@postgres.internal:5432/mem_plane
```

* **OpenSearch** handles distributed Lucene HNSW vector search (`knn_vector`) and multi-field BM25 keyword matching.
* **PostgreSQL** stores transactional document bodies, JSONB audit trails, and execution traces.
* **Your Agent** connects to the exact same MCP endpoints without noticing any difference.

---

## 8. Repository Layout

```text
mem-plane/
├── .agents/                          # Google Antigravity (AGY) Integration
│   ├── mcp_config.json               # Project MCP configuration (git-ignored)
│   └── skills/                       # On-demand AGY workflow skills
│       ├── memplane-search/SKILL.md
│       ├── memplane-provenance/SKILL.md
│       ├── memplane-trace/SKILL.md
│       └── memplane-remember/SKILL.md
├── .claude/                          # Claude Code Integration
│   └── skills/                       # On-demand Claude Code workflow skills
│       ├── memplane-search.md
│       ├── memplane-provenance.md
│       ├── memplane-trace.md
│       └── memplane-remember.md
├── docs/                             # Architecture & Economic Documentation
│   ├── architecture.md               # Topology and read/write flow diagrams
│   ├── kv_cache_economics.md         # 4-zone prompt caching breakdown
│   └── mcp_setup.md                  # Host configuration guide
├── src/mem_plane/
│   ├── config.py                     # Pydantic BaseSettings & backend factory
│   ├── core/
│   │   ├── governor.py               # 4-zone prompt assembler & cache guard
│   │   ├── models.py                 # Pydantic v2 schemas (FactEntity, SurrogateHit)
│   │   ├── pruner.py                 # Ephemeral tool trace truncator
│   │   └── rrf.py                    # Reciprocal Rank Fusion algorithm
│   ├── pipeline/
│   │   └── embeddings.py             # Deterministic offline & neural embeddings
│   ├── server/
│   │   ├── mcp_server.py             # FastMCP stdio server daemon
│   │   └── tools.py                  # Exposed MCP tool handlers
│   └── storage/
│       ├── protocols.py              # MetadataStorage & SearchIndex contracts
│       ├── production/               # Postgres (asyncpg) & OpenSearch clients
│       └── sqlite/                   # FTS5 + sqlite-vec hybrid storage
├── tests/                            # Comprehensive zero-infra test suite
│   ├── server/                       # End-to-end MCP tool integration tests
│   └── storage/                      # SQLite & OpenSearch hybrid search tests
└── pyproject.toml                    # Hatchling build specification
```

---

## 9. Development & Testing

```bash
# Run full unit and integration test suite (34 tests passing in <1s)
pytest

# Strict type checking (100% compliant)
mypy src tests

# Fast linting and formatting
ruff check src tests
ruff format src tests
```

---

## 📄 License

MIT Licensed. Free for personal and commercial production use.
