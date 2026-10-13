# `mem-plane`: Master Implementation Specification & Autonomous Build Plan

## 1. System Vision & Problem Statement

`mem-plane` is an infrastructure-level memory plane and context governor for production AI agents. It bridges the gap between persistent search storage (SQLite locally, OpenSearch/PostgreSQL in production) and the LLM's active execution context window.

### Core Problems Solved
1. **Hardware KV-Cache Invalidation:** Prevents dynamic memory rewrites from busting prompt prefix caches. Guarantees 80–90% caching discounts on Claude, OpenAI, and vLLM by enforcing immutable prompt zones and cache breakpoints.
2. **Technical Retrieval Accuracy:** Pure vector embeddings fail on exact code syntax, CLI flags, compiler errors, and UUIDs. `mem-plane` implements Reciprocal Rank Fusion (RRF) to merge BM25 lexical tokens with dense vectors without brittle manual score normalization.
3. **Context Window Flooding:** Avoids single-stage multi-kilobyte chunk dumps. Implements two-stage progressive disclosure where agents query lightweight metadata surrogates (~30 tokens) and selectively dereference full raw documents ($O(1)$) on demand.
4. **Dev-to-Prod Storage Parity:** Eliminates heavy infrastructure hurdles for local CLI agents. Defaults to embedded SQLite (`FTS5` + `sqlite-vec`) out of the box, with a one-flag config switch to PostgreSQL and OpenSearch in production clusters.

---

## 2. Target Project Layout

```text
mem-plane/
├── .claude/
│   └── skills/
│       ├── memplane-search.md            # Skill: two-stage progressive disclosure
│       ├── memplane-provenance.md        # Skill: auditing fact origins & commit SHAs
│       └── memplane-trace.md             # Skill: dereferencing truncated bash/compiler traces
├── .github/
│   └── workflows/
│       ├── ci.yml                        # Automated testing, ruff lint, mypy type checks
│       └── release.yml                   # PyPI automated release
├── docs/
│   ├── architecture.md                   # Topology, read/write flows, storage contracts
│   ├── kv_cache_economics.md             # Invariance rules and prompt caching guidelines
│   └── mcp_setup.md                      # Host configuration guide for Claude Code & Cursor
├── src/
│   └── mem_plane/
│       ├── __init__.py                   # Core library exports
│       ├── config.py                     # Environment settings (Pydantic BaseSettings)
│       ├── py.typed                      # PEP 561 typing marker
│       ├── core/
│       │   ├── __init__.py
│       │   ├── models.py                 # Pydantic v2 domain schemas & prompt zones
│       │   ├── rrf.py                    # Reciprocal Rank Fusion algorithm
│       │   ├── governor.py               # Context assembler & KV-cache prefix invariant guard
│       │   └── pruner.py                 # Ephemeral tool trace truncation & compressor
│       ├── storage/
│       │   ├── __init__.py
│       │   ├── protocols.py              # MetadataStorage & SearchIndex protocol contracts
│       │   ├── sqlite/
│       │   │   ├── __init__.py
│       │   │   ├── connection.py         # aiosqlite connection lifecycle & sqlite-vec loader
│       │   │   └── backend.py            # SQLite FTS5 (BM25) + sqlite-vec hybrid engine
│       │   └── production/
│       │       ├── __init__.py
│       │       ├── postgres.py           # PostgreSQL transactional document & trace store
│       │       └── opensearch.py         # OpenSearch HNSW knn_vector + BM25 client
│       ├── pipeline/
│       │   ├── __init__.py
│       │   ├── chunker.py                # Contextual chunker with path-ancestry prefixing
│       │   └── embeddings.py             # Async embedding wrapper (Dummy & production models)
│       └── server/
│           ├── __init__.py
│           ├── mcp_server.py             # FastMCP stdio daemon entrypoint
│           └── tools.py                  # Exposed MCP tool handlers
├── tests/
│   ├── __init__.py
│   ├── conftest.py                       # Fixtures for in-memory SQLite and mock embeddings
│   ├── test_rrf.py                       # Mathematical rank fusion verification
│   ├── test_governor.py                  # KV-cache prefix stability & zone isolation tests
│   ├── test_pruner.py                    # Tool output compression & trace retrieval tests
│   └── storage/
│       ├── test_sqlite_backend.py        # SQLite FTS5 + sqlite-vec hybrid integration tests
│       └── test_opensearch_backend.py    # Production hybrid search integration tests
├── .gitignore
├── LICENSE
├── README.md
└── pyproject.toml
```

---

## 3. Core Component Specifications

### 3.1 Data Contracts (`src/mem_plane/core/models.py`)
```python
from datetime import datetime
from enum import Enum
from typing import Any, Dict, List, Optional
from pydantic import BaseModel, Field

class PromptZone(str, Enum):
    ZONE_1_STATIC = "ZONE_1_STATIC"
    ZONE_2_BREAKPOINT = "ZONE_2_BREAKPOINT"
    ZONE_3_SURROGATES = "ZONE_3_SURROGATES"
    ZONE_4_ACTIVE_TURNS = "ZONE_4_ACTIVE_TURNS"

class FactEntity(BaseModel):
    fact_id: str
    tenant_id: str
    session_id: Optional[str] = None
    summary: str = Field(description="Compact surrogate summary (~20-40 tokens)")
    raw_content: str = Field(description="Full uncompressed body or trace text")
    source_type: str = Field(description="codebase, chat_turn, tool_output, or rule")
    provenance_metadata: Dict[str, Any] = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=datetime.utcnow)

class SurrogateHit(BaseModel):
    fact_id: str
    summary: str
    score: float
    source_path: Optional[str] = None
    token_estimate: int = 30

class HydrationBundle(BaseModel):
    tenant_rules: List[str] = Field(default_factory=list)
    user_preferences: Dict[str, Any] = Field(default_factory=dict)
    session_state: Dict[str, Any] = Field(default_factory=dict)
```

### 3.2 Reciprocal Rank Fusion (`src/mem_plane/core/rrf.py`)
RRF fuses distinct ranking lists without manual score calibration. Given ranked lists $M$:

$$RRF\_Score(d) = \sum_{m \in M} \frac{1}{k + \text{rank}_m(d)}$$

- Default constant: $k = 60$.
- `rank_m(d)` is the 1-based index position of document $d$ in system $m$.
- Signature:
  ```python
  def reciprocal_rank_fusion(
      dense_ranks: List[str],
      lexical_ranks: List[str],
      k: int = 60,
      top_n: int = 5
  ) -> List[tuple[str, float]]: ...
  ```

### 3.3 Storage Protocols (`src/mem_plane/storage/protocols.py`)
Decouple transactional persistence from hybrid search ranking:
```python
from typing import Protocol, List, Optional, Dict, Any
from mem_plane.core.models import FactEntity, SurrogateHit, HydrationBundle

class MetadataStorage(Protocol):
    async def save_fact(self, fact: FactEntity) -> None: ...
    async def get_fact_by_id(self, fact_id: str) -> Optional[FactEntity]: ...
    async def get_hydration_data(self, tenant_id: str, user_id: str, session_id: str) -> HydrationBundle: ...
    async def save_tool_trace(self, trace_id: str, raw_trace: str) -> None: ...
    async def get_tool_trace(self, trace_id: str) -> Optional[str]: ...

class SearchIndex(Protocol):
    async def index_vector_and_tokens(self, fact_id: str, summary: str, content: str, vector: List[float]) -> None: ...
    async def hybrid_search(self, query: str, query_vector: List[float], top_k: int = 5) -> List[SurrogateHit]: ...
```

### 3.4 Context Governor (`src/mem_plane/core/governor.py`)
Preserves KV-cache prefix invariants:
1. **Zone 1 (Static System & Pre-Token Hydration):** Static system prompt + deterministically formatted SQL hydration data (tenant constraints and user sticky state). Must remain 100% byte-identical across dialogue turns.
2. **Zone 2 (Cache Delimiter Breakpoint):** Exact static marker:
   `\n<!-- MEM_PLANE_CACHE_BREAKPOINT -->\n`
3. **Zone 3 (Retrieved Memory Surrogates):** Compact surrogate list formatted deterministically (sorted alphabetically by `fact_id` to ensure identical retrieved subsets produce identical cache tokens).
4. **Zone 4 (Active Dialogue Turns):** User messages, assistant responses, and pruned tool outputs.

### 3.5 Ephemeral Tool Pruner (`src/mem_plane/core/pruner.py`)
Intercepts noisy tool returns (terminal output, compiler traces, test runs):
- Trigger: Output exceeds `max_lines=15` or `max_chars=600`.
- Action:
  1. Generate UUID `trace_id`.
  2. Persist raw trace out-of-band via `MetadataStorage.save_tool_trace()`.
  3. Replace active prompt turn text with:
     ```text
     [TOOL OUTPUT TRUNCATED: {total_lines} lines. Trace ID: {trace_id}. Call dereference_raw_trace('{trace_id}') for full trace.]
     {first_5_lines}
     ... [truncated] ...
     {last_5_lines}
     ```

### 3.6 MCP Server & Tools Contract (`src/mem_plane/server/`)
Exposes three standardized MCP endpoints:
1. `search_episodic_memory(query: str, top_k: int = 5) -> str`: Runs parallel BM25 and dense $k$-NN search, fuses candidates via RRF, and returns a lightweight JSON array of surrogate metadata (~30 tokens each).
2. `inspect_fact_provenance(fact_id: str) -> str`: Performs an $O(1)$ relational lookup returning raw document text, commit SHAs, author, timestamp, and audit trail.
3. `dereference_raw_trace(trace_id: str) -> str`: Retrieves unpruned tool execution logs or compiler traces.

---

## 4. Repository Agent Skills (`.claude/skills/`)

### `.claude/skills/memplane-search.md`
```markdown
---
name: memplane-search
description: Search codebase documentation, past design decisions, and episodic memory using two-stage progressive disclosure.
---

# Two-Stage Memory Search Skill

Whenever you need to recall past architecture decisions, bug fixes, or documentation:

1. Stage 1: Query Surrogates First
Do NOT dump entire files. Invoke `search_episodic_memory`:
```json
{"query": "How is authentication handled in the worker pool?", "top_k": 3}
```
2. Stage 2: Selective Dereferencing ($O(1)$)
Inspect the returned surrogates (~30 tokens each):
- If the surrogate summary answers the question, STOP immediately. Do not pull additional tokens.
- If and ONLY if you require raw code or complete implementation text, invoke `inspect_fact_provenance(fact_id="...")`.
```

### `.claude/skills/memplane-provenance.md`
```markdown
---
name: memplane-provenance
description: Trace the origin, commit history, and source conversation behind a stored fact.
---

# Fact Provenance Skill

When asked "Why do you know that?", "Who decided this?", or verifying architectural constraints:
1. Extract candidate `fact_id` from memory.
2. Call `inspect_fact_provenance(fact_id="...")`.
3. Report the author, commit SHA, timestamp, and originating dialogue turn to the user.
```

### `.claude/skills/memplane-trace.md`
```markdown
---
name: memplane-trace
description: Inspect full error traces or bash logs automatically compressed by mem-plane's pruner.
---

# Pruned Trace Inspection Skill

When tool output displays:
`[TOOL OUTPUT TRUNCATED: ... Trace ID: <trace_id>. Call dereference_raw_trace('<trace_id>') for full trace.]`

1. Examine the compressed preview first; root causes are often visible in the head/tail preview.
2. If and only if the full trace is needed, call `dereference_raw_trace(trace_id="<trace_id>")`.
3. Do NOT re-execute heavy build or test commands solely to re-read output logs.
```

---

## 5. Staged Implementation Roadmap

Follow this sequential execution order. Each stage must pass its unit/integration tests before advancing to the next.

```
┌────────────────────────────────────────────────────────┐
│ Stage 1: Foundation, Data Models & Pure RRF Algorithm  │
└───────────────────────────┬────────────────────────────┘
                            │
┌───────────────────────────▼────────────────────────────┐
│ Stage 2: Local SQLite + FTS5 + sqlite-vec Storage      │
└───────────────────────────┬────────────────────────────┘
                            │
┌───────────────────────────▼────────────────────────────┐
│ Stage 3: Context Governor, Cache Guard & Pruner        │
└───────────────────────────┬────────────────────────────┘
                            │
┌───────────────────────────▼────────────────────────────┐
│ Stage 4: MCP Server & Agent Skills Integration         │
└───────────────────────────┬────────────────────────────┘
                            │
┌───────────────────────────▼────────────────────────────┐
│ Stage 5: Production Storage (PostgreSQL & OpenSearch)  │
└────────────────────────────────────────────────────────┘
```

### Stage 1: Foundation, Data Models & Pure RRF Algorithm
- **Files to build:**
  - `pyproject.toml` (Poetry/Hatchling packaging, Python `>=3.11,<3.13`, dependencies: `pydantic>=2.7`, `numpy>=1.26`, `pytest`, `pytest-asyncio`).
  - `src/mem_plane/core/models.py` (`FactEntity`, `SurrogateHit`, `HydrationBundle`, `PromptZone`).
  - `src/mem_plane/core/rrf.py` (`reciprocal_rank_fusion` math implementation).
  - `src/mem_plane/pipeline/embeddings.py` (`DummyEmbeddingClient` producing deterministic pseudo-vectors for offline testing).
- **Testing:**
  - `tests/test_rrf.py`: Validate rank ordering, empty lists, completely disjoint candidate lists, and ranking tiebreaks. Ensure 100% test pass rate.

### Stage 2: Local Storage Engine (SQLite + FTS5 + `sqlite-vec`)
- **Files to build:**
  - `src/mem_plane/storage/protocols.py` (`MetadataStorage`, `SearchIndex`).
  - `src/mem_plane/storage/sqlite/connection.py` (Async connection lifecycle, load `sqlite-vec`, WAL mode).
  - `src/mem_plane/storage/sqlite/backend.py`:
    - Relational `facts` table (raw payloads, provenance JSON).
    - `facts_fts` virtual table (`fts5` with Porter stemmer).
    - `facts_vec` virtual table (`vec0` with 384-dimension embeddings).
    - Relational `hydration_profiles` and `tool_traces` tables.
    - `hybrid_search()` method running FTS5 and vector queries, passing IDs to `reciprocal_rank_fusion`.
- **Testing:**
  - `tests/storage/test_sqlite_backend.py`: Spin up an in-memory database, insert facts containing technical keywords (e.g., `--no-cov`, `SIGSEGV 139`, `auth_jwt_v2`), and verify that both keyword-exact and semantic queries rank correctly.

### Stage 3: Context Governor, Cache Guard & Ephemeral Pruner
- **Files to build:**
  - `src/mem_plane/core/governor.py`:
    - `assemble_prompt(static_system_prompt, hydration, surrogates, turns)`.
    - Zero-tool pre-token hydration builder.
    - Deterministic surrogate sorting (by ID) to guarantee prompt string invariance.
  - `src/mem_plane/core/pruner.py`:
    - `EphemeralToolPruner.process_tool_output()`.
    - Truncation formatting and out-of-band trace persistence.
- **Testing:**
  - `tests/test_governor.py`: Assert that Zone 1 prefix tokens remain 100% byte-identical when surrogates in Zone 3 change.
  - `tests/test_pruner.py`: Process a 200-line mock stack trace, verify the preview format, and ensure the full trace is retrievable by `trace_id`.

### Stage 4: MCP Server & Agent Skills Integration
- **Files to build:**
  - `src/mem_plane/server/tools.py` (`search_episodic_memory`, `inspect_fact_provenance`, `dereference_raw_trace`).
  - `src/mem_plane/server/mcp_server.py` (FastMCP server with stdio transport).
  - `.claude/skills/memplane-search.md`.
  - `.claude/skills/memplane-provenance.md`.
  - `.claude/skills/memplane-trace.md`.
- **Testing:**
  - End-to-end integration test querying the MCP server, receiving surrogates, and dereferencing raw content through simulated MCP tool calls.

### Stage 5: Production Storage Backends (PostgreSQL & OpenSearch)
- **Files to build:**
  - `src/mem_plane/storage/production/postgres.py` (`asyncpg` transactional document and audit log store).
  - `src/mem_plane/storage/production/opensearch.py` (Async client with Lucene HNSW `knn_vector` and BM25 index mapping).
  - `src/mem_plane/config.py` (Runtime factory switching between `sqlite` and `production` via configuration strings).
- **Testing:**
  - `tests/storage/test_opensearch_backend.py`: Verify that OpenSearch and Postgres implement identical interfaces to the SQLite backend and produce equivalent rankings.

---

## 6. Execution Directives for Antigravity

When executing this build:
1. Run all commands inside a virtual environment.
2. Maintain strict type annotations (`mypy --strict`).
3. Never use raw floating-point score weighting when combining vector and keyword results; use pure ordinal Reciprocal Rank Fusion.
4. Ensure zero-infrastructure execution is preserved: running the local test suite must never require external Docker containers or remote API keys.