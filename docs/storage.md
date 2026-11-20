# Storage Architecture: The SQLite Decision & Frozen Production Backend

## 1. Overview and Backend Freeze

As specified in `spec.md` §4, the production storage backend for **`mem-plane`** is **SQLite** utilizing `FTS5` (full-text search) and `sqlite-vec` (vector similarity search).

Distributed vector databases and external database clusters (PostgreSQL + pgvector, OpenSearch, Pinecone, Qdrant) are **explicitly frozen and not part of mem-plane's production surface**. They are an architectural anti-pattern for a local developer harness.

---

## 2. Why SQLite for Agent Memory?

### 2.1 Zero-Infrastructure Friction
`mem-plane` is designed to be an invisible, zero-config harness embedded directly into the developer's Claude Code workflow.
- **No Docker containers** or background daemons required.
- **No open network ports**, database credentials, or external connection strings.
- **Single file on disk** at `~/.mem-plane/memory.db` with standard file-permission security (`0700` directory, `0600` database file).
- Instant startup: hooks execute in under 15 milliseconds without network handshake overhead.

### 2.2 Scale Characteristics for Single-Developer Projects
Agent memory for a typical project has the following dimensions:
- **Facts:** 50 to 5,000 facts per codebase (decisions, architectural rules, bugfix root causes, conventions).
- **Episodes:** Lossless session transcript chunks split at ~4,800 characters (~10 to 50 chunks per session).
- **Traces:** Tool output previews with raw text retained for a configurable retention window (default: 14 days).

SQLite easily handles millions of rows and multi-gigabyte databases with single-digit millisecond queries. For `mem-plane`'s workload (a few thousand facts and chunks), SQLite query execution time is typically under **1 to 3 milliseconds**.

### 2.3 Concurrent Multi-Process Safety
Developers frequently run multiple Claude Code terminal sessions or multiple worktree branches simultaneously. `mem-plane` achieves robust multi-process concurrency through:
- **Write-Ahead Logging (WAL):** Set via `PRAGMA journal_mode = WAL;`, allowing concurrent readers and writers without blocking read access.
- **Synchronous Normal:** `PRAGMA synchronous = NORMAL;`, ensuring durability across crashes while avoiding fsync bottlenecks.
- **Busy Timeout:** `PRAGMA busy_timeout = 5000;`, enabling automatic backoff and retry for concurrent writes across processes.
- **Immediate Transactions:** Incremental transcript reads and writes acquire `BEGIN IMMEDIATE` locks to eliminate race conditions between hook invocations.

---

## 3. Storage Engine Components

### 3.1 Relational Core
- **`projects`**: Codebase registry keyed by normalized git remote URL (`p_` + 16 hex chars) or canonical root path. Isolates data completely between repositories.
- **`facts`**: Curated memories (`f_` + 12 hex chars) categorized by `kind` (`decision`, `rule`, `bugfix`, `convention`, `context`, `note`). Stores concise summaries (<= 300 chars) for search results and full markdown bodies for on-demand inspection.
- **`fact_history`**: Immutable audit log tracking additions, updates, supersessions, and deletions.
- **`episodes`**: Lossless transcript archive chunks (`ep_` + 12 hex chars) partitioned by project, session, and compaction segment.
- **`tool_traces`**: Raw outputs of pruned tool runs (`t_` + 10 hex chars) with exit codes, byte sizes, and timestamps, purged automatically by `mem-plane gc`.

### 3.2 Lexical Search (SQLite FTS5)
- Full-text search tables (`facts_fts` and `episodes_fts`) with unicode61 tokenizer and porter stemmer.
- Exact symbol and identifier matching for compiler errors, CLI flags, stack traces, and variable names where dense embeddings fail.

### 3.3 Vector Search (sqlite-vec)
- Dense vector similarity table using `sqlite-vec` virtual tables with 384-dimensional float embeddings (`BAAI/bge-small-en-v1.5`).
- Query-time partition filtering by `project_id` and fact status (`active`).

### 3.4 Hybrid Fusion (Reciprocal Rank Fusion)
Queries execute simultaneously against lexical (FTS5) and semantic (sqlite-vec) indices. The results are merged using Reciprocal Rank Fusion ($k=60$):
$$RRF\_Score(d) = \sum_{m \in M} \frac{w_m}{60 + \text{rank}_m(d)}$$
This delivers the accuracy of dense semantic search alongside the precision of exact symbol matching without requiring brittle score calibration across models.

---

## 4. Why Distributed Backends are Anti-Patterns Here

| Factor | Distributed Vector DB (OpenSearch / pgvector) | Embedded SQLite (`mem-plane`) |
| :--- | :--- | :--- |
| **Latency** | 20–100 ms network roundtrip per query | 0.5–2 ms in-process function call |
| **Hook Budget** | Hook deadline is <150 ms; network drops cause timeouts | Hook runs synchronously in <15 ms |
| **Infrastructure** | Requires Docker, clusters, memory overhead (>2 GB RAM) | 0 background processes, <10 MB RAM |
| **Data Privacy** | Code snippets and logs sent across network / cloud | 100% local on user's disk; never leaves machine |
| **Offline Support** | Fails on planes, trains, or without active internet | Fully functional offline |
| **Failure Surface** | Connection drops, auth expirations, port conflicts | Zero network failure modes |

If multi-user shared team memory is ever introduced in a future specification, it will be designed as an opt-in cloud synchronization layer on top of local storage, preserving the instant local SQLite experience as the core harness foundation.
