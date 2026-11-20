# mem-plane: Harness Memory Implementation Spec

This document tells a coding agent how to change the `mem-plane` repository (https://github.com/prabhupant/mem-plane, commit `6e43bd8`, October 16, 2026) so that it meets one goal. The goal is to give AI coding harnesses, starting with Claude Code, a long-lived memory that survives context compaction and that lowers token use.

Read the whole document before writing code. Section 16 gives the order of work. Every milestone has acceptance criteria, and a milestone is not done until its criteria pass.

---

## 1. Goals and non-goals

### 1.1 Goals

1. **Lower token use in the harness.** The main lever is keeping the conversation small, so that compaction happens less often. Each compaction costs one summarization call, and it also throws away the prompt cache, so the next request pays full price to write the cache again. The biggest source of avoidable tokens is large tool output, such as test logs and build output.
2. **Keep the prompt cache valid.** mem-plane must never cause the harness's system prompt or tool list to change during a session. All memory must reach the model through places that are added to the end of the conversation: tool results and hook context.
3. **Survive compaction.** Nothing that happened in a session may be lost when the harness compacts. After compaction, the model must be told what memory exists and how to fetch it.
4. **Stay correct over time.** A stored memory that has become wrong is worse than no memory. Memories must carry their source, must be replaceable by newer memories, and must be flagged when the code they describe has changed.
5. **Need no infrastructure.** One local SQLite file. No Docker, no servers other than the MCP process the harness starts.

### 1.2 Non-goals

- Shared team memory across machines. The Postgres and OpenSearch backend stays in the repo but is frozen (see Section 4.3).
- Assembling prompts for the harness. A harness builds its own prompt, so the 4-zone governor cannot apply to it. The governor becomes an optional helper for people who build their own agent loop (see Section 13).
- Automatic LLM extraction of facts on every turn. That costs tokens on every turn, which works against Goal 1. An optional background extractor is described in Section 9.6 and is off by default.

---

## 2. How prompt caching works in a harness

The coding agent must understand this, because it explains several rules later in the spec.

A prompt cache only reuses the opening part of a prompt that is byte-for-byte unchanged since the previous request. In Claude Code, the request is ordered like this: system prompt, then tool definitions, then the conversation. Each new turn is added to the end of the conversation.

That ordering has four consequences for mem-plane:

1. Anything added at the end of the conversation keeps the cache valid. On later turns, it becomes part of the cached opening section. Tool results and hook `additionalContext` both land at the end, so they are cache-safe.
2. Changing the MCP tool list or a tool's description during a session invalidates the cache from the tool definitions onward. **Rule: mem-plane's tool names, descriptions, and schemas must be static strings that never depend on runtime state.**
3. Compaction replaces the conversation with a summary, so the cache is gone after it regardless of what mem-plane does. Context injected right after compaction therefore costs no extra cache invalidation.
4. Every token in a tool result is paid for once at full price and then at the cheaper cache-read price on every later request until compaction. A 20,000-token test log is therefore paid for many times. Trimming it saves tokens on every following turn and also delays the next compaction.

---

## 3. Current state of the repository

### 3.1 File map

| Path | Current role | Fate in this spec |
| :- | :- | :- |
| `src/mem_plane/config.py` | `Settings` (env prefix `MEM_PLANE_`) and backend factory | Extend (Section 12) |
| `src/mem_plane/core/models.py` | `FactEntity`, `SurrogateHit`, `HydrationBundle`, `PromptZone` | Replace models (Section 5.4) |
| `src/mem_plane/core/rrf.py` | Unweighted RRF of two lists | Extend to weighted, N lists (Section 7.4) |
| `src/mem_plane/core/pruner.py` | Head/tail truncation, saves trace | Rewrite preview logic (Section 9.2) |
| `src/mem_plane/core/governor.py` | 4-zone prompt assembler | Move to `mem_plane/sdk/` (Section 13) |
| `src/mem_plane/pipeline/embeddings.py` | Protocol plus `DummyEmbeddingClient` (random vectors from SHA-256) | Add real client (Section 6) |
| `src/mem_plane/server/tools.py` | `MemPlaneService` with 4 operations | Rewrite (Section 8) |
| `src/mem_plane/server/mcp_server.py` | FastMCP stdio server, always uses dummy embeddings | Rewrite (Section 8) |
| `src/mem_plane/storage/protocols.py` | `MetadataStorage`, `SearchIndex` | Replace with one `MemoryStore` protocol (Section 5.5) |
| `src/mem_plane/storage/sqlite/backend.py` | Schema, FTS5 plus sqlite-vec search | Rewrite schema and search (Sections 5, 7) |
| `src/mem_plane/storage/sqlite/connection.py` | Loads sqlite-vec, sets WAL | Add `busy_timeout`, file permissions (Section 4.2) |
| `src/mem_plane/storage/production/*` | Postgres plus OpenSearch | Freeze (Section 4.3) |
| `.claude/skills/*.md`, `.agents/skills/*` | Agent guidance | Rewrite (Section 11) |
| `tests/` | 34 tests | Extend (Section 15) |

### 3.2 Problems this spec fixes

1. **Random embeddings.** `run_server_stdio` always builds `DummyEmbeddingClient`, which seeds a random generator from a SHA-256 hash of the text. Vector search therefore returns arbitrary facts, and RRF mixes them into results.
2. **No scoping in search.** Facts store `tenant_id` and `session_id`, but `hybrid_search` in both backends ignores them, so every search covers every fact.
3. **Writes are not atomic.** `record_fact` calls `save_fact` (commits) and then `index_vector_and_tokens` (commits again). If the second step fails, the fact exists but cannot be found. Note: re-indexing does not duplicate FTS rows, because `index_vector_and_tokens` already deletes the old FTS row first.
4. **In-memory default database.** `sqlite_db_path` defaults to `":memory:"`, so memory is lost when the process exits.
5. **Pruner not connected.** Nothing calls `EphemeralToolPruner`, so `tool_traces` stays empty and `dereference_raw_trace` has nothing to return. Its thresholds (15 lines or 600 characters) are also far too low for a harness.
6. **Governor not connected**, and it cannot be connected to a harness (Section 2).
7. **No update, delete, or history.** A changed decision leaves both the old and new facts searchable with nothing marking which is current.
8. **Capture depends on the model.** The only way to write memory is the model calling `record_fact`. If it does not, the information is lost at compaction.
9. **Token-heavy tool outputs.** `search_episodic_memory` returns `json.dumps(..., indent=2)`. Indentation and repeated keys waste tokens on every search.

---

## 4. Storage decision

### 4.1 Use SQLite with FTS5 and sqlite-vec. Do not add another database.

Keep the existing stack: SQLite, the FTS5 extension for keyword search, and sqlite-vec for vector search. Reasons:

- The workload is one developer on one machine. Data volume is small. A heavy user might produce 100,000 transcript chunks a year, and sqlite-vec handles brute-force k-NN over that many 384-dimension vectors in well under 100 ms.
- Hooks are short-lived processes that must start in milliseconds. Opening a local file is instant. Connecting to a server is slower and can fail.
- Zero infrastructure is a stated project goal.
- The existing code already uses this stack, so the change is a schema and logic rewrite, not a new dependency.

Do not use Postgres, OpenSearch, Qdrant, Chroma, or any vector database service for the harness use case.

### 4.2 Database file and connection settings

- **Location.** One database per user at `~/.mem-plane/memory.db`. Override with `MEM_PLANE_DB_PATH`. Use one shared file for all projects and separate projects with a `project_id` column (Section 5.2). One file keeps setup simple and allows an optional cross-project search later.
- **Directory and file permissions.** Create `~/.mem-plane/` with mode `0700` and the database with `0600`. Traces and transcripts can contain secrets.
- **Concurrency.** Several processes write at once: the MCP server, and every hook invocation. Set these PRAGMAs on every connection:
  ```sql
  PRAGMA journal_mode=WAL;
  PRAGMA synchronous=NORMAL;
  PRAGMA busy_timeout=5000;
  PRAGMA foreign_keys=ON;
  ```
  `busy_timeout` makes a writer wait up to 5 seconds for a lock instead of failing immediately. Keep every write transaction short: no network calls and no embedding calls inside a transaction.
- **Sync vs async.** Hooks should use the standard synchronous `sqlite3` module (or `sqlean` where extension loading is missing, as `connection.py` already does). A hook does a few statements and exits, so `aiosqlite` adds overhead for no benefit. The MCP server can keep `aiosqlite`.
- **Remove `:memory:` as the default.** Tests pass an explicit `":memory:"` or a temp path.

### 4.3 Freeze the production backend

Keep `storage/production/` in the repo, but:

- Move it behind `MEM_PLANE_STORAGE_BACKEND=production_experimental`.
- Have every new protocol method it does not implement raise `NotImplementedError("production backend is frozen; see docs/storage.md")`.
- Remove production claims from the README.

Revisit this only if shared team memory becomes a goal.

---

## 5. Data model

### 5.1 Concepts

| Concept | What it is | Who writes it |
| :- | :- | :- |
| **Project** | One codebase. All other records belong to one project | Created on first use by hooks or the server |
| **Fact** | A short, curated memory: a decision, rule, bug cause, convention. Has a summary for search results and a body for full detail | The model, through `record_fact`; optionally the background extractor |
| **Episode** | A lossless archive of one session's transcript, split into chunks for search | Hooks, automatically |
| **Trace** | The full output of a tool call that the pruner shortened | The `PostToolUse` hook |
| **Pin** | A fact marked as always shown at session start | The model, through `pin_fact`, or the user |
| **Session** | One harness session, with its transcript read position | Hooks |

Facts are the main memory. Episodes guarantee that nothing is lost, and search falls back to them when no fact matches. Traces let the model see full output without it entering the conversation.

### 5.2 Project identity

Resolve `project_id` the same way in hooks and in the MCP server, in this order:

1. Find the git top-level directory of the working directory (`git rev-parse --show-toplevel`). Hooks get the directory from the `cwd` field of their input. The MCP server uses the `CLAUDE_PROJECT_DIR` environment variable, which Claude Code sets for stdio MCP servers, falling back to its own working directory.
2. If the repo has an `origin` remote, normalize its URL: lowercase the host, strip `.git`, strip credentials, and convert `git@host:owner/repo` to `host/owner/repo`. `project_id = "p_" + sha1(normalized_url)[:16]`. This keeps the same ID across clones and worktrees.
3. Otherwise use the absolute top-level path: `project_id = "p_" + sha1(path)[:16]`.
4. If not in a git repo, use the absolute working directory path the same way.

Implement this once in `src/mem_plane/project.py` as `resolve_project(cwd: str) -> ProjectInfo` and cache results per path for the life of the process. Allow an override with `MEM_PLANE_PROJECT_ID`.

### 5.3 Schema (version 1)

Put the DDL in `src/mem_plane/storage/sqlite/schema.py` as a list of migration steps. Track the version with `PRAGMA user_version`. Section 5.6 covers migration from the current schema (version 0).

```sql
-- ---------- projects ----------
CREATE TABLE IF NOT EXISTS projects (
    project_id      TEXT PRIMARY KEY,
    display_name    TEXT NOT NULL,          -- repo name or folder name
    root_path       TEXT NOT NULL,          -- last seen absolute path
    remote_url      TEXT,                   -- normalized, may be NULL
    created_at      TEXT NOT NULL,          -- ISO 8601 UTC
    last_seen_at    TEXT NOT NULL
);

-- ---------- facts ----------
CREATE TABLE IF NOT EXISTS facts (
    fact_id         TEXT PRIMARY KEY,       -- "f_" + 12 hex chars
    project_id      TEXT NOT NULL REFERENCES projects(project_id),
    session_id      TEXT,                   -- session that created it, if known
    kind            TEXT NOT NULL CHECK (kind IN
                        ('decision','rule','bugfix','convention','context','note')),
    summary         TEXT NOT NULL,          -- <= 300 chars, shown in search results
    body            TEXT NOT NULL,          -- full detail, fetched on demand
    status          TEXT NOT NULL DEFAULT 'active'
                        CHECK (status IN ('active','superseded','deleted')),
    supersedes      TEXT REFERENCES facts(fact_id),   -- fact this one replaces
    superseded_by   TEXT REFERENCES facts(fact_id),
    pinned          INTEGER NOT NULL DEFAULT 0,       -- 1 = show at session start
    source          TEXT NOT NULL CHECK (source IN ('agent','extractor','user')),
    content_hash    TEXT NOT NULL,          -- sha256 of normalized summary + body
    created_at      TEXT NOT NULL,
    updated_at      TEXT NOT NULL,
    embedded_at     TEXT                    -- NULL until a vector exists
);
CREATE INDEX IF NOT EXISTS idx_facts_project_status ON facts(project_id, status);
CREATE INDEX IF NOT EXISTS idx_facts_project_pinned ON facts(project_id, pinned) WHERE pinned = 1;
CREATE INDEX IF NOT EXISTS idx_facts_project_updated ON facts(project_id, updated_at DESC);
CREATE UNIQUE INDEX IF NOT EXISTS idx_facts_dedup ON facts(project_id, content_hash)
    WHERE status = 'active';

-- Code locations a fact depends on. Used for staleness checks (Section 7.6).
CREATE TABLE IF NOT EXISTS fact_paths (
    fact_id         TEXT NOT NULL REFERENCES facts(fact_id) ON DELETE CASCADE,
    rel_path        TEXT NOT NULL,          -- path relative to project root
    content_sha256  TEXT,                   -- hash of the file when the fact was recorded
    git_commit      TEXT,                   -- HEAD when recorded, may be NULL
    PRIMARY KEY (fact_id, rel_path)
);

-- Audit trail. One row per change.
CREATE TABLE IF NOT EXISTS fact_history (
    history_id      INTEGER PRIMARY KEY AUTOINCREMENT,
    fact_id         TEXT NOT NULL,
    event           TEXT NOT NULL CHECK (event IN
                        ('create','update','supersede','delete','pin','unpin')),
    old_summary     TEXT,
    new_summary     TEXT,
    old_body        TEXT,
    new_body        TEXT,
    actor           TEXT NOT NULL,          -- 'agent', 'user', 'extractor'
    session_id      TEXT,
    created_at      TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_fact_history_fact ON fact_history(fact_id, created_at);

-- Keyword index for facts. Standalone FTS5 table, kept in sync by application code.
CREATE VIRTUAL TABLE IF NOT EXISTS facts_fts USING fts5(
    fact_id UNINDEXED,
    project_id UNINDEXED,
    summary,
    body,
    tokenize = 'porter unicode61'
);

-- Vector index for facts. Requires sqlite-vec >= 0.1.6 (partition keys and metadata columns).
CREATE VIRTUAL TABLE IF NOT EXISTS facts_vec USING vec0(
    fact_id TEXT PRIMARY KEY,
    project_id TEXT PARTITION KEY,
    status TEXT,                             -- metadata column, filterable in KNN
    embedding FLOAT[384] DISTANCE_METRIC=cosine
);

-- ---------- sessions and episodes ----------
CREATE TABLE IF NOT EXISTS sessions (
    session_id          TEXT PRIMARY KEY,   -- harness session id from hook input
    project_id          TEXT NOT NULL REFERENCES projects(project_id),
    harness             TEXT NOT NULL DEFAULT 'claude-code',
    transcript_path     TEXT,
    transcript_offset   INTEGER NOT NULL DEFAULT 0,  -- bytes already ingested
    compactions         INTEGER NOT NULL DEFAULT 0,
    started_at          TEXT NOT NULL,
    last_event_at       TEXT NOT NULL,
    ended_at            TEXT
);
CREATE INDEX IF NOT EXISTS idx_sessions_project ON sessions(project_id, last_event_at DESC);

CREATE TABLE IF NOT EXISTS episode_chunks (
    chunk_id        TEXT PRIMARY KEY,       -- "e_" + 12 hex chars
    project_id      TEXT NOT NULL REFERENCES projects(project_id),
    session_id      TEXT NOT NULL REFERENCES sessions(session_id),
    seq             INTEGER NOT NULL,       -- order within the session
    segment         INTEGER NOT NULL,       -- 0 before first compaction, +1 after each
    start_ts        TEXT,                   -- timestamp of first message in chunk
    end_ts          TEXT,
    text            TEXT NOT NULL,          -- normalized transcript text (Section 9.4)
    token_estimate  INTEGER NOT NULL,
    created_at      TEXT NOT NULL,
    embedded_at     TEXT,
    UNIQUE (session_id, seq)
);
CREATE INDEX IF NOT EXISTS idx_chunks_project ON episode_chunks(project_id, created_at DESC);

CREATE VIRTUAL TABLE IF NOT EXISTS episode_fts USING fts5(
    chunk_id UNINDEXED,
    project_id UNINDEXED,
    text,
    tokenize = 'porter unicode61'
);

CREATE VIRTUAL TABLE IF NOT EXISTS episode_vec USING vec0(
    chunk_id TEXT PRIMARY KEY,
    project_id TEXT PARTITION KEY,
    embedding FLOAT[384] DISTANCE_METRIC=cosine
);

-- ---------- traces ----------
CREATE TABLE IF NOT EXISTS tool_traces (
    trace_id        TEXT PRIMARY KEY,       -- "t_" + 10 hex chars (short: the model types it)
    project_id      TEXT NOT NULL REFERENCES projects(project_id),
    session_id      TEXT,
    tool_name       TEXT NOT NULL,
    tool_use_id     TEXT,
    command         TEXT,                   -- Bash command, if any (first 2,000 chars)
    stdout          TEXT NOT NULL,
    stderr          TEXT NOT NULL,
    exit_code       INTEGER,
    line_count      INTEGER NOT NULL,
    char_count      INTEGER NOT NULL,
    created_at      TEXT NOT NULL,
    expires_at      TEXT NOT NULL           -- created_at + retention (Section 12)
);
CREATE INDEX IF NOT EXISTS idx_traces_project ON tool_traces(project_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_traces_expiry ON tool_traces(expires_at);

-- ---------- metadata ----------
CREATE TABLE IF NOT EXISTS meta (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
-- Required keys: 'embedding_model', 'embedding_dimension', 'schema_created_at'.
```

**sqlite-vec version check.** On startup, run `SELECT vec_version()`. If it is older than `v0.1.6`, partition keys and metadata columns are not available. In that case, create the `vec0` tables without `PARTITION KEY` and `status`, and filter by project and status after the KNN query, with the over-fetch described in Section 7.3. Put this fallback behind one function so the rest of the code does not care.

**Embedding dimension.** The `384` in the DDL must come from the configured model's dimension at table creation time, not a constant. Store the model name and dimension in `meta`. On startup, if the configured model differs from `meta.embedding_model`, refuse to write vectors and log a message telling the user to run `mem-plane reindex` (Section 10).

### 5.4 Pydantic models

Replace `src/mem_plane/core/models.py` with these. Keep `SurrogateHit` as a deprecated alias for one release if anything external imports it.

```python
class FactKind(StrEnum):
    DECISION = "decision"; RULE = "rule"; BUGFIX = "bugfix"
    CONVENTION = "convention"; CONTEXT = "context"; NOTE = "note"

class FactStatus(StrEnum):
    ACTIVE = "active"; SUPERSEDED = "superseded"; DELETED = "deleted"

class FactPath(BaseModel):
    rel_path: str
    content_sha256: str | None = None
    git_commit: str | None = None

class Fact(BaseModel):
    fact_id: str
    project_id: str
    session_id: str | None = None
    kind: FactKind
    summary: str = Field(max_length=300)
    body: str
    status: FactStatus = FactStatus.ACTIVE
    supersedes: str | None = None
    superseded_by: str | None = None
    pinned: bool = False
    source: Literal["agent", "extractor", "user"] = "agent"
    paths: list[FactPath] = Field(default_factory=list)
    created_at: datetime
    updated_at: datetime

class SearchHit(BaseModel):
    ref: str                 # fact_id, chunk_id, or trace_id
    ref_type: Literal["fact", "episode"]
    summary: str             # fact summary, or first 200 chars of the chunk
    score: float
    kind: str | None = None  # fact kind, or None for episodes
    stale: bool = False      # Section 7.6
    pinned: bool = False
    created_at: datetime

class TraceRecord(BaseModel):
    trace_id: str; project_id: str; session_id: str | None
    tool_name: str; command: str | None
    stdout: str; stderr: str; exit_code: int | None
    line_count: int; char_count: int
    created_at: datetime
```

### 5.5 Storage protocol

Replace the two protocols in `storage/protocols.py` with one synchronous core and an async wrapper. Hooks call the sync version. The MCP server calls the async one, which runs the sync methods with `asyncio.to_thread`, so there is a single implementation of the SQL.

```python
class MemoryStore(Protocol):
    # projects / sessions
    def upsert_project(self, info: ProjectInfo) -> None: ...
    def upsert_session(self, session_id: str, project_id: str,
                       transcript_path: str | None) -> SessionRow: ...
    def advance_transcript_offset(self, session_id: str, new_offset: int) -> None: ...
    def increment_compactions(self, session_id: str) -> int: ...

    # facts
    def create_fact(self, fact: Fact) -> CreateResult: ...          # handles dedup + supersede
    def update_fact(self, fact_id: str, project_id: str, *, summary: str | None,
                    body: str | None, kind: FactKind | None, actor: str,
                    session_id: str | None) -> Fact: ...
    def delete_fact(self, fact_id: str, project_id: str, actor: str,
                    session_id: str | None) -> None: ...            # soft delete
    def set_pinned(self, fact_id: str, project_id: str, pinned: bool, actor: str) -> None: ...
    def get_fact(self, fact_id: str, project_id: str) -> Fact | None: ...
    def list_pinned(self, project_id: str) -> list[Fact]: ...
    def list_recent_facts(self, project_id: str, limit: int) -> list[Fact]: ...
    def list_session_facts(self, project_id: str, session_id: str) -> list[Fact]: ...
    def fact_history(self, fact_id: str, project_id: str) -> list[HistoryRow]: ...

    # episodes
    def append_chunks(self, chunks: list[EpisodeChunk]) -> None: ...
    def get_chunk(self, chunk_id: str, project_id: str) -> EpisodeChunk | None: ...

    # traces
    def save_trace(self, trace: TraceRecord, retention_days: int) -> None: ...
    def get_trace(self, trace_id: str, project_id: str) -> TraceRecord | None: ...
    def purge_expired_traces(self, now: datetime) -> int: ...

    # vectors (written later, outside the write transaction; Section 6.3)
    def pending_embeddings(self, limit: int) -> list[PendingItem]: ...
    def write_vectors(self, items: list[tuple[PendingItem, list[float]]]) -> None: ...

    # search
    def search(self, req: SearchRequest, query_vector: list[float] | None) -> list[SearchHit]: ...
```

Every method that takes a `fact_id`, `chunk_id`, or `trace_id` also takes `project_id` and includes it in the `WHERE` clause. This prevents reading another project's records by guessing an ID.

### 5.6 Migration from the current schema

On startup, if `PRAGMA user_version` is `0` and a `facts` table with a `tenant_id` column exists:

1. Begin a transaction.
2. Rename old tables: `facts` to `facts_v0`, `facts_fts` to `facts_fts_v0`, `facts_vec` to `facts_vec_v0`, `tool_traces` to `tool_traces_v0`. Drop `hydration_profiles` after copying any `tenant_rules` into pinned facts (one fact per rule, `kind='rule'`, `pinned=1`).
3. Create the version 1 schema.
4. Copy each `facts_v0` row into `facts` with: `project_id` = a project created from `tenant_id` (display name = tenant ID, `root_path` = `""`), `kind='note'`, `summary`, `body = raw_content`, `source='agent'`, `content_hash` computed, `embedded_at = NULL`. Insert matching `facts_fts` rows. Do not copy vectors, because they were random.
5. Drop the `_v0` tables, set `PRAGMA user_version = 1`, commit.

Because old vectors are dropped, the background embedder (Section 6.3) will embed the migrated facts on the next MCP server start.

---

## 6. Embeddings

### 6.1 Default model

Add `FastEmbedClient` in `src/mem_plane/pipeline/embeddings.py` using the `fastembed` package (ONNX, CPU, no PyTorch).

- Default model: `BAAI/bge-small-en-v1.5`, 384 dimensions. This matches the current schema dimension and is small enough to run on a laptop CPU.
- Load the model lazily on first use, in a worker thread, because loading takes one to several seconds.
- Normalize vectors to unit length before storing. Cosine distance in sqlite-vec then behaves predictably.
- For BGE models, prefix queries (not documents) with `"Represent this sentence for searching relevant passages: "`. Make the query prefix part of a per-model config table in code so other models can be added.

```python
@dataclass(frozen=True)
class EmbeddingModelSpec:
    name: str
    dimension: int
    query_prefix: str = ""
    document_prefix: str = ""

MODELS = {
    "BAAI/bge-small-en-v1.5": EmbeddingModelSpec(
        "BAAI/bge-small-en-v1.5", 384,
        query_prefix="Represent this sentence for searching relevant passages: "),
}
```

Extend the `EmbeddingClient` protocol with `embed_query(text)` and `embed_documents(texts)` so the prefix logic is not spread around callers. Keep `embed_text` and `embed_batch` as thin wrappers for one release.

Add `fastembed>=0.3` to a new optional dependency group `embed`, and add `embed` to a convenience group `harness = ["mem-plane[sqlite,mcp,embed]"]`.

### 6.2 Dummy client is for tests only

`DummyEmbeddingClient` stays, but `build_embedding_client(settings)` returns it only when `MEM_PLANE_EMBEDDING_PROVIDER=dummy`. If the provider is `dummy` and `MEM_PLANE_ALLOW_DUMMY_EMBEDDINGS` is not `1`, raise at startup with a clear error. Tests set both variables in `conftest.py`.

If `fastembed` is not installed and the provider is the default, the server must still start. In that case it runs keyword-only search, logs a warning once, and includes `vector_search: disabled (install mem-plane[embed])` in the `memory_status` tool output (Section 8.2).

### 6.3 Where embedding happens

Hooks must never load the embedding model. A hook runs as a fresh process on every tool call or turn, and model loading would add seconds of delay to the harness.

So writes and embedding are split:

1. Any writer (hook or MCP tool) inserts rows and FTS entries in one transaction, with `embedded_at = NULL`. The record is immediately findable by keyword search.
2. The MCP server runs a background task. Every 2 seconds, and immediately after its own writes, it calls `pending_embeddings(limit=64)`, embeds the batch, and calls `write_vectors`, which inserts into the `*_vec` table and sets `embedded_at` in one short transaction.
3. The CLI command `mem-plane embed-pending` does the same once and exits, for use without the MCP server.

Exception: `record_fact` in the MCP server embeds the new fact before returning, because the server already has the model loaded and the cost is small. It still writes the vector in a separate transaction after the fact row is committed.

---

## 7. Retrieval

### 7.1 Search request

```python
class SearchRequest(BaseModel):
    project_id: str
    query: str
    top_k: int = 5                      # max 20
    include_episodes: bool = True
    kinds: list[FactKind] | None = None
    include_superseded: bool = False
```

### 7.2 Keyword search (FTS5)

Build the FTS5 query from the user query:

1. Split on whitespace. Drop empty tokens. Cap at 24 tokens.
2. Remove FTS5 syntax characters from each token: `"`, `(`, `)`, `*`, `:`, `^`, `{`, `}`. Keep `_`, `-`, `.`, `/` inside tokens.
3. Wrap each token in double quotes so FTS5 treats it as a phrase. With the `unicode61` tokenizer, `"auth_jwt_v2"` becomes the phrase `auth jwt v2`, which only matches those tokens in that order. This is what makes exact identifiers such as `--no-cov` or `auth_jwt_v2` match precisely.
4. Join with ` OR `.

Query facts:

```sql
SELECT f.fact_id, bm25(facts_fts, 0.0, 0.0, 2.0, 1.0) AS rank
FROM facts_fts
JOIN facts f ON f.fact_id = facts_fts.fact_id
WHERE facts_fts MATCH :q
  AND facts_fts.project_id = :project_id
  AND f.status = 'active'
ORDER BY rank
LIMIT :n;
```

The `bm25()` weights are per column in table order. The two `UNINDEXED` columns get `0.0`. `summary` gets `2.0` and `body` `1.0`, so a match in the summary counts more. Lower `bm25` values are better in FTS5, so `ORDER BY rank` ascending is correct.

Query episodes the same way against `episode_fts` with `bm25(episode_fts, 0.0, 0.0, 1.0)`.

Candidate count `n` = `max(top_k * 4, 20)`.

### 7.3 Vector search (sqlite-vec)

Embed the query once with `embed_query`. Then:

```sql
SELECT fact_id, distance
FROM facts_vec
WHERE embedding MATCH :qvec
  AND k = :n
  AND project_id = :project_id
  AND status = 'active';
```

Drop any result with `distance > MAX_COSINE_DISTANCE` (default `0.55`, which is cosine similarity 0.45 or above; tune it with the evaluation set in Section 15.3). This threshold matters. Without it, a vector search always returns `n` results even when none is related, and those unrelated results would take RRF slots.

If the sqlite-vec fallback is active (no partition keys), use `k = n * 5` and filter by joining to `facts` afterward.

If vector search is disabled or the query vector is missing, skip this step.

### 7.4 Fusion

Change `core/rrf.py` to accept any number of ranked lists with weights:

```python
def weighted_rrf(
    ranked_lists: Sequence[tuple[Sequence[str], float]],   # (ids in rank order, weight)
    k: int = 60,
    top_n: int = 5,
) -> list[tuple[str, float]]:
    # score(d) = sum over lists of weight / (k + rank), rank starting at 1
    # ties broken by id ascending, for deterministic output
```

Run fusion with four lists:

| List | Default weight |
| :- | :- |
| Fact keyword hits | 1.0 |
| Fact vector hits | 1.0 |
| Episode keyword hits | 0.5 |
| Episode vector hits | 0.5 |

Fact IDs and chunk IDs have different prefixes (`f_`, `e_`), so they never collide in one fused list. The lower episode weight makes a curated fact rank above a raw transcript chunk that matches equally well.

Make weights configurable (Section 12) but do not expose them as tool parameters.

### 7.5 Result formatting (token-efficient)

Tools return plain text, not indented JSON. Indented JSON repeats key names and whitespace for every hit and can double the token count.

Format:

```
3 results for "jwt expiry" (project: payments-api)
f_3a91c0d2e4b7 [decision] JWT access tokens expire after 15 minutes; refresh tokens after 7 days. (2026-10-02)
f_77b0e1aa9c13 [rule] [STALE: auth/config.py changed] Never read JWT secrets from env in tests. (2026-09-11)
e_0c4d9f2b1a6e [episode] ...discussed moving token TTLs into settings.py, decided against because... (2026-10-02)
Use get_memory(ref) for full detail.
```

Rules for this output:

- One line per hit. The summary is truncated to 200 characters with `…`.
- Results appear in score order. Do not print scores; they cost tokens and the model cannot use them.
- Dates are the creation date only, `YYYY-MM-DD`.
- No timestamps of the current time, random IDs, or other values that would make identical searches produce different text.
- The last line is a fixed string.

### 7.6 Staleness check

When a search or `get_memory` returns a fact with rows in `fact_paths`:

1. For each path, resolve `project_root / rel_path`.
2. If the file does not exist, the fact is stale with reason `"<path> deleted"`.
3. Otherwise compute `sha256` of the file bytes. If it differs from `content_sha256`, the fact is stale with reason `"<path> changed"`.
4. Cache file hashes per process for 10 seconds keyed by `(path, mtime_ns, size)` so repeated searches do not re-read files.
5. Skip files over 5 MB and treat them as not stale.

Staleness only adds a marker. It never hides a fact. The model decides whether to check the code and then call `update_memory`.

`project_root` comes from `projects.root_path`, which hooks refresh on every event.

---

## 8. MCP server

### 8.1 Startup

`src/mem_plane/server/mcp_server.py`:

1. Load `Settings`.
2. Open the store (runs migrations).
3. Resolve the project from `CLAUDE_PROJECT_DIR` or the current directory and `upsert_project`.
4. Build the embedding client (Section 6.2). Load the model in the background so startup is fast.
5. Start the background embedder task (Section 6.3) and a purge task that calls `purge_expired_traces` once at startup and then every hour.
6. Register the tools in Section 8.2 and run over stdio.

The server's project is fixed for its lifetime. Tools never accept a `project_id` argument from the model. This removes a whole class of mistakes and attacks.

### 8.2 Tools

Seven tools. Keep the descriptions exactly as static strings in one module, `server/tool_text.py`, and add a test that fails if any description changes without a version bump. That protects the harness's prompt cache across mem-plane upgrades in the middle of a session as well.

| Tool | Parameters | Returns |
| :- | :- | :- |
| `search_memory` | `query: str`, `top_k: int = 5`, `include_episodes: bool = true` | Text from Section 7.5 |
| `get_memory` | `ref: str` (an `f_`, `e_`, or `t_` ID), `grep: str \| None`, `lines: str \| None` (like `"120-180"`), `max_chars: int = 6000` | Full fact (with paths, history count, stale markers), or a chunk, or a trace slice |
| `record_fact` | `summary: str`, `body: str`, `kind: FactKind`, `paths: list[str] = []`, `supersedes: str \| None`, `pin: bool = false` | One line: `recorded f_... ` or `duplicate of f_...` or `recorded f_... superseding f_...` |
| `update_memory` | `fact_id: str`, `summary: str \| None`, `body: str \| None`, `kind: FactKind \| None` | One line confirming, plus refreshed path hashes |
| `forget_memory` | `fact_id: str`, `reason: str` | One line confirming |
| `pin_memory` | `fact_id: str`, `pinned: bool` | One line confirming |
| `memory_status` | none | Counts of facts, pins, episodes, traces; vector search on or off; pending embeddings; database path |

Behavior details:

**`get_memory` for traces.** This replaces `dereference_raw_trace`. Returning a full 20,000-token trace would put the tokens the pruner saved straight back into the conversation. So:

- With `grep`, return only matching lines (Python regex, case-insensitive, max 200 matches) with their line numbers and one line of context on each side.
- With `lines`, return that range.
- With neither, return the first `max_chars` characters plus a footer stating the total size and suggesting `grep` or `lines`.
- `stdout` and `stderr` are shown under separate headers.

**`record_fact`.**

1. Normalize text (trim, collapse whitespace) and compute `content_hash`.
2. If an active fact in the project has the same hash, return `duplicate of f_...` and write nothing.
3. If the vector model is loaded, embed `summary + "\n" + body` and run a vector search over active facts. If the closest has cosine similarity of `0.92` or more and `supersedes` is not set, still record the fact, but append to the result: `similar to f_... "<summary>". If this replaces it, call update_memory or set supersedes.` Do not block the write.
4. For each `paths` entry, resolve it against the project root, reject paths outside the root, and store `content_sha256` and the current `git rev-parse HEAD` (or `NULL`).
5. In one transaction: insert the fact, FTS row, path rows, and a `create` history row. If `supersedes` is set and that fact is active in this project, set its `status='superseded'` and `superseded_by`, update its `facts_vec.status`, and add a `supersede` history row.
6. Commit, then write the vector in a second short transaction.

**`update_memory`.** Writes a history row with old and new values, updates the FTS row, sets `embedded_at=NULL` so the embedder refreshes the vector, and re-hashes the fact's paths so it is no longer marked stale.

**`forget_memory`.** Soft delete: `status='deleted'`, FTS row removed, vector row removed, history row with the reason in `new_body`. Deleted facts are never returned by search or `get_memory`.

**Error output.** Tools never raise to the harness. On error, return a single line starting with `error:` and a short reason. Log the stack trace to `~/.mem-plane/logs/server.log`.

### 8.3 Removed tools

Remove `search_episodic_memory`, `inspect_fact_provenance`, `dereference_raw_trace`, and the old `record_fact` signature. Since mem-plane is at version 0.1.0, a clean break is acceptable. Note it in `CHANGELOG.md`.

---

## 9. Hooks

Hooks are what make memory automatic. They are the most important part of this spec, because they capture everything without depending on the model, and they deliver the token savings.

### 9.1 Hook runner

Create the package `src/mem_plane/hooks/` with:

- `runner.py`: the entry point for `mem-plane hook <event>`.
- `post_tool_use.py`, `stop.py`, `pre_compact.py`, `session_start.py`, `user_prompt_submit.py`, `session_end.py`: one handler per event.
- `transcript.py`: incremental transcript reading and normalization (Section 9.4).
- `preview.py`: the pruner's preview builder (Section 9.2).

Rules every handler follows:

1. **Read input** as one JSON object from stdin. Validate with a Pydantic model that ignores unknown fields (`model_config = ConfigDict(extra="ignore")`). The common fields Claude Code sends are `session_id`, `transcript_path`, `cwd`, and `hook_event_name`. Tool events add `tool_name`, `tool_input`, `tool_use_id`, and for `PostToolUse` also `tool_response`. `SessionStart` adds `source`. `PreCompact` adds `trigger`.
2. **Never break the harness.** Wrap the handler in `try/except BaseException`. On any error, log it to `~/.mem-plane/logs/hooks.log` and exit `0` with no output. A memory tool must fail open: if mem-plane is broken, the harness must behave as if mem-plane were not installed.
3. **Never block.** Handlers never return `decision: "block"`, never exit `2`, and never return `continue: false`.
4. **Print exactly one JSON object or nothing.** Claude Code only reads stdout as JSON when it is a single object with nothing else around it. Make sure no library prints to stdout. Set logging to a file before importing anything heavy.
5. **Stay fast.** Use the synchronous SQLite store. Do not import `fastembed`, `numpy`, or `mcp` in the hook code path. Check this with a test that imports `mem_plane.hooks.runner` and asserts those modules are not in `sys.modules`. Target under 150 ms for `PostToolUse` and `SessionStart`, measured from process start.
6. **Resolve project and session** on every call: `resolve_project(input.cwd)`, then `upsert_project` and `upsert_session`.
7. **Debug capture.** If `MEM_PLANE_HOOK_DEBUG=1`, write the raw stdin payload to `~/.mem-plane/logs/payloads/<event>-<timestamp>.json`. Section 16 Milestone 0 uses this to collect real fixtures.

### 9.2 `PostToolUse`: shorten large tool output

**Purpose.** Stop large outputs from entering the conversation, while keeping the full output retrievable.

**Mechanism.** A `PostToolUse` hook may return `hookSpecificOutput.updatedToolOutput`, which replaces the tool result that the model reads. Third-party hooks that use this field report that Claude Code only applies the replacement when it has the same shape as the original `tool_response`. For `Bash`, that is an object with `stdout` and `stderr` string fields (and possibly others). **Before implementing, capture real payloads (Milestone 0) and confirm the exact shape. Build the replacement by copying the original object and replacing only the `stdout` and `stderr` strings.** If the shape is not recognized, output nothing.

**Matcher.** Start with `Bash` only. Do not prune `Read`, `Edit`, `Write`, `NotebookEdit`, or `MultiEdit`: the model needs file contents in full to edit correctly. `Grep`, `WebFetch`, and MCP tools can be added later behind config once Bash pruning is proven.

**Algorithm.**

```
input: tool_response (object), tool_input.command
stdout, stderr = tool_response.get("stdout", ""), tool_response.get("stderr", "")
total_lines = count_lines(stdout) + count_lines(stderr)
total_chars = len(stdout) + len(stderr)

if total_lines <= PRUNE_MAX_LINES (default 120) and total_chars <= PRUNE_MAX_CHARS (default 8000):
    output nothing; exit
if command matches PRUNE_EXCLUDE_REGEX (default: commands starting with "cat ", "head ", "tail ",
    "sed -n", "git diff", "git show", or containing "mem-plane"):
    output nothing; exit
    # These commands are usually run to read content in full; shortening them would force a re-read.

trace_id = "t_" + secrets.token_hex(5)
redact secrets (Section 17.2) in stdout and stderr
save TraceRecord(... exit_code = tool_response.get("exitCode") or tool_response.get("exit_code"))
new_stdout = build_preview(stdout, trace_id, stream="stdout") if stdout is large else stdout
new_stderr = build_preview(stderr, trace_id, stream="stderr") if stderr is large else stderr
emit {"hookSpecificOutput": {"hookEventName": "PostToolUse",
      "updatedToolOutput": {**tool_response, "stdout": new_stdout, "stderr": new_stderr}}}
```

A stream counts as "large" if it alone exceeds half of either threshold. This keeps a small stderr intact even when stdout is huge.

**Preview builder (`preview.py`).** The current pruner keeps only the first five and last five lines. Test runners and compilers often print the important failure in the middle, so the new builder keeps three parts:

1. **Head:** the first `PREVIEW_HEAD_LINES` lines (default 15).
2. **Signal lines:** from the middle section, up to `PREVIEW_SIGNAL_LINES` lines (default 30) that match the signal pattern, each prefixed with its 1-based line number. Default pattern, case-insensitive:
   `error|fail|failed|failure|exception|traceback|panic|fatal|assert|warning|denied|not found|undefined|cannot|segmentation|exit code|E\d{3,}|✗|✘`
   Remove duplicate lines (same text after stripping digits and whitespace), keeping the first.
3. **Tail:** the last `PREVIEW_TAIL_LINES` lines (default 25).

Truncate every kept line to 300 characters. Put a banner first:

```
[mem-plane: output shortened. stdout had 1,842 lines (96,310 chars), stored as t_9c1e4a7b2d. Exit code 1.
Search it with get_memory("t_9c1e4a7b2d", grep="pattern") or read a range with lines="400-460".]
```

Between parts, insert `... [lines 16-1,201 omitted, 31 signal lines shown below] ...` and similar markers so the model knows what is missing. The whole preview must stay under `PREVIEW_MAX_CHARS` (default 4,000). If it would exceed that, reduce signal lines first, then tail, then head.

**Why these defaults.** A preview of about 4,000 characters is roughly 1,000 tokens. Typical large test output is 20,000 to 100,000 characters. The savings per pruned call are therefore large, and the preview keeps enough for the model to act in most cases without fetching the trace.

### 9.3 `Stop`: archive each turn

**Purpose.** Keep the episode archive close to up to date, so that very little remains to ingest at compaction.

**Config.** Register with `"async": true` so it never delays the harness.

**Behavior.** Call `ingest_transcript(session)` from Section 9.4. Nothing is printed.

### 9.4 Transcript ingestion

Claude Code passes `transcript_path`, a JSONL file with one JSON object per line. The format is not a formal public contract, so parse it defensively and test it against captured fixtures.

**Incremental read.**

1. `BEGIN IMMEDIATE` (takes the write lock, so two hooks for the same session cannot ingest the same bytes).
2. Read `transcript_offset` for the session.
3. If the file size is less than the offset, the file was replaced. Log it, set the offset to `0`, and continue. Chunks are keyed by `(session_id, seq)` and `seq` keeps increasing, so this can create some duplicated text in rare cases, which is acceptable.
4. Read from the offset to the end. Only process complete lines (ending in `\n`). Set the new offset to the end of the last complete line.
5. Normalize, chunk, redact, and insert chunks and FTS rows.
6. Update the offset. Commit.

**Normalization.** Convert each JSONL entry into zero or more plain-text lines:

| Entry content | Output line |
| :- | :- |
| User message, string or text blocks | `USER: <text>` |
| Assistant text block | `ASSISTANT: <text>` |
| Assistant `tool_use` block | `TOOL CALL <name>: <compact JSON of input, max 300 chars>` |
| `tool_result` block | `TOOL RESULT: <first 500 chars>`; if the text contains a mem-plane banner, keep only the banner line |
| Thinking blocks, images, unknown types | Skip |
| An entry that is a compaction summary | `COMPACTION SUMMARY: <text>`, and start a new `segment` |

Detect message roles and content blocks by checking for keys, not by assuming one structure. For example, accept both `entry["message"]["content"]` and `entry["content"]`, and content that is either a string or a list of blocks. Detect a compaction summary with whatever marker the captured fixtures show. If none is clear, rely on the `PreCompact` hook to increment the segment instead.

**Chunking.** Append normalized lines to a buffer. When the buffer reaches `CHUNK_TARGET_CHARS` (default 4,800, about 1,200 tokens), cut at the end of the current entry. If one entry alone exceeds twice the target, split it at line boundaries. Each chunk stores `start_ts` and `end_ts` from the entries' `timestamp` fields if present, and `token_estimate = len(text) // 4`.

### 9.5 `PreCompact`: capture before compaction

**Purpose.** Guarantee that the conversation that is about to be summarized is fully archived.

**Config.** Synchronous (not async), `timeout: 30`. The hook must finish before compaction starts.

**Behavior.**

1. Call `ingest_transcript(session)`. The transcript is written asynchronously by Claude Code and may lag the in-memory conversation slightly. The `Stop` hook covers most of that gap, because the last completed turn is already archived.
2. Call `increment_compactions(session_id)`. New chunks after this point get the next `segment` number.
3. Print nothing. Never block compaction.

### 9.6 Optional fact extractor (off by default)

When `MEM_PLANE_EXTRACTOR=anthropic` and an API key is configured, the MCP server's background loop also processes newly archived chunks from finished segments:

1. Group chunks by `(session_id, segment)` once that segment is closed by a compaction or session end.
2. Send the text to a small, cheap model with a fixed prompt that asks for up to 8 durable facts as JSON (`kind`, `summary`, `body`, `paths`). The prompt should ask only for decisions, rules, bug causes, and conventions, and should exclude progress updates and small talk.
3. Insert results with `source='extractor'` through the normal `create_fact` path, so duplicate detection applies.
4. Never pin extracted facts automatically.

This costs API tokens outside the harness, so it stays opt-in. Implement it last (Milestone 7).

### 9.7 `SessionStart`: tell the model what memory exists

**Purpose.** After compaction, at startup, on resume, and after `/clear`, put a short, deterministic index of memory into context. The model then knows what it can fetch.

**Mechanism.** Text that a `SessionStart` command hook prints, or returns as `hookSpecificOutput.additionalContext`, is added to the model's context. Claude Code caps this at 10,000 characters per value and moves anything longer to a file with a short preview. Keep the output under `SESSION_CONTEXT_MAX_CHARS` (default 6,000).

Use a `command` hook, not an `mcp_tool` hook. Claude Code skips `mcp_tool` hooks on `SessionStart` at launch, because MCP servers are not connected yet at that point.

**Matcher.** `startup|resume|clear|compact`.

**Output.** Return JSON with `additionalContext`. Build it from these sections, in this order:

```
mem-plane memory for project payments-api (14 facts, 3 pinned, 212 archived transcript chunks).

Pinned:
- f_1b2c... [rule] All money amounts are integers in minor units; never floats.
- f_7d8e... [convention] Tests run with `make test`; never call pytest directly (fixtures need env).
- f_9a0b... [decision] Postgres is the only datastore; Redis was removed in Sept 2026.

Recorded earlier in this session:            <- only when source == "compact"
- f_3a91... [decision] JWT access tokens expire after 15 minutes...
- f_44c2... [bugfix] Flaky test_refund fixed by freezing time in conftest...
This session's earlier conversation is archived and searchable (2 compactions so far).

Recent:
- f_8e7f... [decision] ... (2026-11-18)
- ... up to 8 entries

Memory tools: search_memory finds earlier decisions and conversation, get_memory(id) returns
full detail, record_fact saves durable decisions, rules, and bug causes.
```

Write this block as factual statements, not as commands. Claude Code's documentation warns that text framed like out-of-band system instructions can trigger the model's prompt-injection defenses, which would make it show the text to the user instead of using it.

Determinism rules, which keep this block identical when nothing has changed:

- Sort pinned facts by `kind`, then `created_at`, then `fact_id`.
- Sort session facts by `created_at`, then `fact_id`.
- Recent facts: the 8 most recently updated active, unpinned facts not already listed, sorted by `updated_at` descending, then `fact_id`.
- Truncate each summary to 160 characters.
- Do not include the current time or anything that changes when no memory changed.
- If over budget, drop "Recent" entries from the bottom first, then session facts, then pinned facts beyond the first 20.
- If the project has no facts and no chunks, print only the last line (the usage hint), so new users learn the tools exist.

Also update `projects.root_path` and `sessions.transcript_path` from the input.

### 9.8 `UserPromptSubmit`: optional automatic recall (off by default)

When `MEM_PLANE_AUTO_RECALL=1`:

1. Run a keyword-only search (no embedding model; hooks must not load it) over active facts in the project, using the prompt text as the query, top 3.
2. Keep only hits whose `bm25` score passes `AUTO_RECALL_MIN_SCORE` (tune with the evaluation set; start with the 30th percentile of scores from true matches).
3. If any remain, return `additionalContext` with at most 800 characters:
   `mem-plane: possibly relevant memories: f_... <summary>; f_... <summary>`.
4. Otherwise print nothing.

This adds tokens on prompts that have matches. They are appended to the end, so they do not break the cache, but they are paid for on every later turn until compaction. That is why it is opt-in.

### 9.9 `SessionEnd`: final flush

Register with `"async": true`. Call `ingest_transcript` and set `sessions.ended_at`. Claude Code gives `SessionEnd` hooks a very short time budget, so this must be best-effort. The next `SessionStart` for the same session (on resume) or the next `Stop` covers anything missed.

### 9.10 Hook configuration

Ship this as `hooks/hooks.json` in the plugin (Section 14) and print it from `mem-plane doctor --print-hooks` for users who configure hooks by hand in `.claude/settings.json`:

```json
{
  "description": "mem-plane: long-lived memory and tool-output shortening",
  "hooks": {
    "SessionStart": [
      {
        "matcher": "startup|resume|clear|compact",
        "hooks": [
          { "type": "command", "command": "mem-plane", "args": ["hook", "session-start"], "timeout": 10 }
        ]
      }
    ],
    "PostToolUse": [
      {
        "matcher": "Bash",
        "hooks": [
          { "type": "command", "command": "mem-plane", "args": ["hook", "post-tool-use"], "timeout": 15 }
        ]
      }
    ],
    "Stop": [
      {
        "hooks": [
          { "type": "command", "command": "mem-plane", "args": ["hook", "stop"], "async": true }
        ]
      }
    ],
    "PreCompact": [
      {
        "matcher": "auto|manual",
        "hooks": [
          { "type": "command", "command": "mem-plane", "args": ["hook", "pre-compact"], "timeout": 30 }
        ]
      }
    ],
    "SessionEnd": [
      {
        "hooks": [
          { "type": "command", "command": "mem-plane", "args": ["hook", "session-end"], "async": true }
        ]
      }
    ]
  }
}
```

`args` uses Claude Code's exec form, which runs the program without a shell. Add the `UserPromptSubmit` entry only when the user enables auto recall.

---

## 10. Command-line interface

Add `src/mem_plane/cli.py` and register it in `pyproject.toml`:

```toml
[project.scripts]
mem-plane = "mem_plane.cli:main"
```

Use `argparse` (no new dependency). Commands:

| Command | What it does |
| :- | :- |
| `mem-plane serve` | Runs the MCP server over stdio (Section 8) |
| `mem-plane hook <event>` | Runs a hook handler (Section 9). Events: `session-start`, `post-tool-use`, `stop`, `pre-compact`, `session-end`, `user-prompt-submit` |
| `mem-plane status [--project PATH]` | Same content as the `memory_status` tool |
| `mem-plane search QUERY [--project PATH]` | Prints search results, for debugging retrieval |
| `mem-plane embed-pending` | Embeds all records with `embedded_at IS NULL`, then exits |
| `mem-plane reindex [--model NAME]` | Drops and rebuilds all `*_vec` tables with the given model, updates `meta` |
| `mem-plane gc` | Purges expired traces, runs `PRAGMA optimize` and `INSERT INTO facts_fts(facts_fts) VALUES('optimize')` (same for `episode_fts`) |
| `mem-plane export [--project PATH] > file.jsonl` | Writes facts and history as JSON Lines |
| `mem-plane import file.jsonl` | Imports facts; skips duplicates by `content_hash` |
| `mem-plane doctor [--print-hooks]` | Checks: Python version, sqlite-vec loads and version, fastembed present, DB path and permissions, `mem-plane` on `PATH`, hooks configured. Prints fixes |
| `mem-plane bench TRANSCRIPT...` | Token report from transcripts (Section 15.4) |

---

## 11. Skills

Replace the four skill files with one skill, because the harness loads a skill's full text when it is used, and four overlapping skills cost more tokens than one. Put it at `skills/mem-plane/SKILL.md` in the plugin, and mirror it to `.agents/skills/mem-plane/SKILL.md` for Antigravity.

Content requirements (keep it under 70 lines):

1. **Description line** (the part the harness always sees): "Use when you make a durable decision, find a bug's root cause, learn a project rule, need context from earlier in this or past sessions, or see a mem-plane trace ID."
2. **When to record** with `record_fact`: decisions and their reasons, rules and conventions, bug root causes and fixes, non-obvious environment facts. Not progress updates, not things already in the code or README, not secrets.
3. **How to write a fact:** the summary is one self-contained sentence that makes sense with no surrounding context and names the specific thing (file, function, setting). The body has the reasoning and any code. Always pass `paths` when the fact is about specific files.
4. **Keeping memory correct:** search before recording. If an existing fact is now wrong, use `supersedes` or `update_memory`. If search shows `[STALE: ...]`, check the file before relying on the fact, then update it.
5. **Pinning:** pin only rules that apply to almost every task in the project. Keep pins under about 10.
6. **Traces:** when output says `stored as t_...`, use `get_memory` with `grep` or `lines` instead of re-running the command or fetching the whole trace.
7. **After compaction:** use the session list in the context and `search_memory` to recover details instead of asking the user to repeat them.

---

## 12. Configuration

All settings use the `MEM_PLANE_` prefix and live in `Settings` in `config.py`.

| Variable | Default | Meaning |
| :- | :- | :- |
| `MEM_PLANE_DB_PATH` | `~/.mem-plane/memory.db` | Database file |
| `MEM_PLANE_LOG_DIR` | `~/.mem-plane/logs` | Logs and debug payloads |
| `MEM_PLANE_PROJECT_ID` | unset | Force a project ID |
| `MEM_PLANE_EMBEDDING_PROVIDER` | `fastembed` | `fastembed`, `none` (keyword only), or `dummy` (tests) |
| `MEM_PLANE_EMBEDDING_MODEL` | `BAAI/bge-small-en-v1.5` | Must be in the model table (Section 6.1) |
| `MEM_PLANE_ALLOW_DUMMY_EMBEDDINGS` | `0` | Required to use `dummy` |
| `MEM_PLANE_MAX_COSINE_DISTANCE` | `0.55` | Vector hit cut-off (Section 7.3) |
| `MEM_PLANE_RRF_K` | `60` | RRF constant |
| `MEM_PLANE_WEIGHT_FACT_KEYWORD` / `_FACT_VECTOR` / `_EPISODE_KEYWORD` / `_EPISODE_VECTOR` | `1.0` / `1.0` / `0.5` / `0.5` | Fusion weights |
| `MEM_PLANE_PRUNE_ENABLED` | `1` | Turn the `PostToolUse` pruner on or off |
| `MEM_PLANE_PRUNE_MAX_LINES` | `120` | Threshold (Section 9.2) |
| `MEM_PLANE_PRUNE_MAX_CHARS` | `8000` | Threshold |
| `MEM_PLANE_PRUNE_EXCLUDE_REGEX` | see Section 9.2 | Commands never pruned |
| `MEM_PLANE_PREVIEW_HEAD_LINES` / `_TAIL_LINES` / `_SIGNAL_LINES` / `_MAX_CHARS` | `15` / `25` / `30` / `4000` | Preview shape |
| `MEM_PLANE_TRACE_RETENTION_DAYS` | `14` | Trace expiry |
| `MEM_PLANE_SESSION_CONTEXT_MAX_CHARS` | `6000` | `SessionStart` budget |
| `MEM_PLANE_RECENT_FACTS` | `8` | Recent facts in `SessionStart` |
| `MEM_PLANE_CHUNK_TARGET_CHARS` | `4800` | Episode chunk size |
| `MEM_PLANE_AUTO_RECALL` | `0` | Enable Section 9.8 |
| `MEM_PLANE_EXTRACTOR` | `off` | `off` or `anthropic` (Section 9.6) |
| `MEM_PLANE_REDACT` | `1` | Secret redaction (Section 17.2) |
| `MEM_PLANE_HOOK_DEBUG` | `0` | Save raw hook payloads |
| `MEM_PLANE_STORAGE_BACKEND` | `sqlite` | `sqlite` or `production_experimental` |

Remove `opensearch_*` and `postgres_dsn` from the default settings documentation, but keep them in the class for the frozen backend.

---

## 13. Governor and the SDK

Move `core/governor.py` to `src/mem_plane/sdk/prompt_zones.py`. Keep its behavior and tests. Export it from `mem_plane.sdk`, not from `mem_plane.core`. Document it as an optional helper for developers who build their own agent loop with the Anthropic or OpenAI APIs, and add one example in `examples/custom_loop.py` that:

1. Builds Zone 1 from pinned facts (using `list_pinned`) with `sort_keys` JSON, exactly as `build_zone_1` does now.
2. Sends it as the system prompt with `cache_control` set on that block.
3. Gives the model the same tools as the MCP server, called in-process.

Remove the "80 to 90 percent caching discount" claim from the README unless Section 15.4's benchmark measures it.

Also move `EphemeralToolPruner` to `sdk/` as a thin wrapper over `hooks/preview.py`, so custom loops can reuse the same preview logic.

---

## 14. Packaging as a Claude Code plugin

Ship a plugin so users install hooks, the MCP server, and the skill together.

### 14.1 Install path

1. The user installs the Python package once: `uv tool install "mem-plane[harness]"` (or `pipx install "mem-plane[harness]"`). This puts `mem-plane` on `PATH`.
2. The user installs the plugin from a marketplace or local directory in Claude Code.
3. The user runs `mem-plane doctor` to confirm everything works.

Do not have the plugin download Python dependencies by itself. Keeping the Python install separate avoids slow first runs and version conflicts.

### 14.2 Plugin layout

Create a top-level `plugin/` directory in the repo:

```
plugin/
├── .claude-plugin/
│   └── plugin.json          # name, version, description (check current schema in Claude Code docs)
├── .mcp.json                # MCP server definition
├── hooks/
│   └── hooks.json           # Section 9.10
└── skills/
    └── mem-plane/
        └── SKILL.md         # Section 11
```

`.mcp.json`:

```json
{
  "mcpServers": {
    "mem-plane": {
      "command": "mem-plane",
      "args": ["serve"]
    }
  }
}
```

Note that tools from a plugin-bundled MCP server get a scoped name in Claude Code, of the form `mcp__plugin_<plugin>_<server>__<tool>`. Nothing in mem-plane depends on the full tool name, but the skill text should refer to tools by their short names (`search_memory` and so on).

Before finalizing `plugin.json`, the coding agent must read the current Claude Code plugin documentation, because the manifest schema may have changed. Keep the version in `plugin.json` equal to the Python package version, and add a CI check for this.

### 14.3 Non-plugin setup

Keep the README's `claude mcp add` instructions as an alternative, and add `mem-plane doctor --print-hooks` output for `.claude/settings.json`. Remove the `MEM_PLANE_SQLITE_DB_PATH` variable from those instructions, since the default path is now persistent.

---

## 15. Testing

### 15.1 Unit and integration tests

Keep `pytest` with `asyncio_mode = "auto"`. Add:

| Test file | Covers |
| :- | :- |
| `tests/test_project.py` | Remote URL normalization (SSH, HTTPS, credentials, `.git`), fallback to path, non-git directory, override variable |
| `tests/storage/test_schema.py` | Fresh create, idempotent re-run, `user_version`, vec fallback path when `vec_version()` is old (mock it) |
| `tests/storage/test_migration_v0.py` | Build a version 0 database with the current DDL, run migration, check facts copied, vectors dropped, tenant rules turned into pinned facts |
| `tests/storage/test_facts.py` | Create, dedup by hash, supersede chain, update writes history, soft delete hides from search and get, pin and unpin, project isolation (a fact from project A is never returned for project B, by search or by ID) |
| `tests/storage/test_atomicity.py` | Inject a failure after the fact insert and before the FTS insert; assert nothing was written |
| `tests/storage/test_search.py` | FTS query sanitization (quotes, parentheses, `*`, empty query), identifier matching (`auth_jwt_v2`, `--no-cov`), cosine cut-off drops unrelated vectors, weighted RRF order, superseded excluded by default, deterministic output for identical input |
| `tests/test_rrf.py` | Extend for N lists, weights, tie-breaking |
| `tests/test_staleness.py` | Unchanged file, changed file, deleted file, large file skipped, cache invalidation by mtime |
| `tests/hooks/test_runner.py` | Malformed stdin exits 0 with no output; exception in handler exits 0; stdout is one JSON object or empty; heavy modules not imported |
| `tests/hooks/test_post_tool_use.py` | Below thresholds: no output. Above: trace saved, replacement keeps every original key, only `stdout`/`stderr` changed. Unknown shape: no output. Exclusion regex. Preview contains banner, head, signal lines with numbers, tail, and stays under max chars |
| `tests/hooks/test_transcript.py` | Use captured fixtures: incremental offsets, partial last line ignored, file shrink resets, chunk sizes, compaction segment increments, unknown entry types skipped, redaction applied |
| `tests/hooks/test_session_start.py` | Empty project prints only the tool line; pinned ordering; compact source adds session section; output under budget with 500 facts; identical output on two calls with no changes |
| `tests/hooks/test_concurrency.py` | Spawn 8 processes that each run `post-tool-use` and `stop` hooks against one temp DB 20 times; assert no errors and no duplicate chunks |
| `tests/server/test_tools.py` | Each tool through the service layer, including `get_memory` trace `grep`/`lines`/default truncation, and error lines instead of exceptions |
| `tests/server/test_tool_text_stable.py` | Hash of all tool names, descriptions, and schemas equals a stored constant. Changing tool text requires updating the constant on purpose |

Mark tests that need `fastembed` with `@pytest.mark.embed` and skip them when it is not installed. CI runs them in one job with the model cached.

### 15.2 Fixtures

Store captured hook payloads and transcript files under `tests/fixtures/claude_code/`. Before committing, scrub them of personal paths and any secrets. Record the Claude Code version they came from in `tests/fixtures/claude_code/VERSION`. When Claude Code changes its formats, capture new fixtures next to the old ones and keep tests for both.

### 15.3 Retrieval evaluation

Create `eval/retrieval/` with:

- `facts.jsonl`: about 150 facts written in the style the skill asks for, across 3 fake projects, including near-duplicates and superseded pairs.
- `queries.jsonl`: about 150 queries, each with `project`, `query`, and `relevant` (list of fact IDs). Mix three types evenly: exact identifier queries (`"--no-cov flag"`), paraphrased queries ("why do tests need special env setup"), and questions about replaced decisions (where only the newer fact is relevant).
- `run.py`: loads facts into a temp DB, runs each query, and reports recall@1, recall@5, and MRR for: keyword only, vector only, fused. It also reports the rate at which superseded facts appear in results.

Use this to tune `MAX_COSINE_DISTANCE`, the fusion weights, and `AUTO_RECALL_MIN_SCORE`. Targets for the defaults: fused recall@5 at least 0.85, fused recall@5 higher than both single methods, superseded rate 0 with default settings.

### 15.4 Token benchmark

This is how to show that mem-plane meets Goal 1.

`mem-plane bench` reads Claude Code transcript files and reports, per session: number of model requests, total `input_tokens`, `cache_creation_input_tokens`, `cache_read_input_tokens`, `output_tokens` (from each assistant entry's usage data; confirm field locations with fixtures), number of compactions, number of pruned tool outputs, and characters removed by pruning. It also reports a cost-weighted input total using configurable multipliers. Default multipliers are 1.0 for uncached input, 1.25 for cache writes, and 0.1 for cache reads, which matches Anthropic's published pricing ratios for 5-minute cache writes at the time of writing; check current pricing before reporting numbers.

Benchmark protocol:

1. Make a fixture repository with a test suite that prints a lot of output and has a few planted bugs.
2. Write 5 task scripts that each take 30 or more turns (for example, fix three failing tests, then refactor a module).
3. Run each task 5 times with mem-plane and 5 times without it, in headless mode, with the same model and settings.
4. After each run's first compaction, ask 5 fixed questions about decisions made before the compaction. Score the answers against a key.
5. Report the median and spread of: weighted input tokens, compactions per run, task success, and recall-quiz score.

Publish the results in `docs/benchmarks.md`. The README should quote only numbers from this file.

---

## 16. Implementation order and acceptance criteria

Do the milestones in order. Each ends with all tests passing, `mypy --strict` clean, and `ruff` clean.

**Milestone 0: Fixtures and baseline.**
- Add the minimal hook runner with debug payload capture only (it prints nothing).
- Install it locally, run real Claude Code sessions that include a large Bash output, a manual `/compact`, an automatic compaction, `/clear`, and a resume.
- Save fixtures (Section 15.2) and write down the observed shapes of `tool_response` for Bash and of transcript entries, including how a compaction appears.
- Run `mem-plane bench` (a minimal version) on the baseline sessions.
- *Done when:* fixtures for every event in Section 9 are committed, and a short `docs/claude-code-formats.md` describes the observed shapes.

**Milestone 1: Storage version 1.**
- `project.py`, new schema, migration, sync `SQLiteStore` implementing `MemoryStore`, async wrapper, atomic writes, persistent default path, permissions, PRAGMAs.
- *Done when:* tests in Section 15.1 for project, schema, migration, facts, atomicity, and concurrency pass.

**Milestone 2: Embeddings, retrieval, and MCP tools.**
- `FastEmbedClient`, provider selection, background embedder, search (Section 7), staleness, the 7 tools, text output format, removal of old tools.
- *Done when:* search and tool tests pass, and the retrieval evaluation meets its targets.

**Milestone 3: Tool-output shortening.**
- `preview.py`, `post_tool_use.py`, trace retention and `gc`.
- *Done when:* hook tests pass using the Milestone 0 fixtures, and a manual Claude Code session shows the shortened output and successful `get_memory` with `grep`.

**Milestone 4: Transcript archive.**
- `transcript.py`, `stop.py`, `pre_compact.py`, `session_end.py`, redaction.
- *Done when:* after a real session with two compactions, every user and assistant message from the session is found in `episode_chunks` (check with a script that compares against the transcript file), and no chunk is duplicated.

**Milestone 5: Session context.**
- `session_start.py`.
- *Done when:* tests pass, and in a real session the model, right after compaction, can answer a question about a decision recorded before compaction by using the listed fact ID.

**Milestone 6: Packaging and documentation.**
- CLI commands, `doctor`, plugin directory, single skill, README rewrite (remove unproven claims), `docs/storage.md`, `CHANGELOG.md`, the token benchmark.
- *Done when:* a clean machine can go from nothing to a working setup with the README's steps, `mem-plane doctor` reports no problems, and `docs/benchmarks.md` has real numbers.

**Milestone 7: Optional features.**
- Auto recall (Section 9.8), fact extractor (Section 9.6), SDK example (Section 13).
- *Done when:* each is off by default, documented, and covered by tests; auto recall is evaluated on the retrieval set before being recommended.

---

## 17. Risks, safeguards, and open questions

### 17.1 Format changes in the harness

The transcript format and the `tool_response` shape are observed behavior, not a stable public contract. Safeguards:

- All parsing is key-based and skips anything unknown.
- The pruner outputs nothing when it does not recognize the shape, so the worst case is no savings, never a broken tool result.
- `mem-plane doctor` runs a self-test that feeds the newest fixtures through the parsers.
- Hook errors are logged with the Claude Code version if the input includes it.

### 17.2 Secrets

Traces and transcripts can contain credentials. With `MEM_PLANE_REDACT=1` (the default), replace matches with `[REDACTED:<type>]` before storing. Patterns to include at minimum:

- AWS access key IDs: `AKIA[0-9A-Z]{16}`
- GitHub tokens: `gh[pousr]_[A-Za-z0-9]{36,}`
- Anthropic keys: `sk-ant-[A-Za-z0-9_\-]{20,}`
- OpenAI-style keys: `sk-[A-Za-z0-9]{20,}`
- Slack tokens: `xox[abpors]-[A-Za-z0-9-]{10,}`
- JWTs: `eyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}`
- PEM private key blocks: from `-----BEGIN [A-Z ]*PRIVATE KEY-----` to the matching `END` line
- Assignments: `(?i)\b(password|passwd|secret|token|api[_-]?key)\b\s*[=:]\s*['"]?[^\s'"]{8,}`

Apply redaction to traces, episode chunks, and facts. The database file is `0600`. Document that the database should be excluded from cloud backup tools if the user works with sensitive data.

### 17.3 Pruning hides something the model needed

If the preview drops an important line, the model may decide wrongly. Safeguards:

- Signal-line extraction keeps error-like lines from the middle.
- The banner always says how much was removed and how to search it.
- Read-style commands are excluded.
- The benchmark tracks how often the model calls `get_memory` on a trace and how often it re-runs the same command. If re-runs are frequent, raise thresholds or improve signal patterns.

### 17.4 Stale or wrong memory

Mitigated by supersede chains, staleness markers, history, and skill guidance. It cannot be fully prevented, because the model may record a wrong fact. `mem-plane export` and a future review command let the user inspect and correct memory.

### 17.5 Prompt injection through stored memory

Facts and archived text can contain text that came from web pages, issues, or files. The session context block and tool outputs label memory clearly as stored notes. The skill tells the model that memories are notes to verify, not instructions. mem-plane must never place memory text in the system prompt.

### 17.6 Disk growth

Rough sizes: one heavy day can produce about 2 MB of chunk text plus about 0.6 MB of vectors (400 chunks of 384 float32 values), and traces can add 10 to 50 MB a day. Trace retention (14 days) and `gc` bound most of the growth. Episode chunks are kept forever by default. Add `MEM_PLANE_EPISODE_RETENTION_DAYS` (default unset, meaning forever) if users ask.

### 17.7 Other harnesses

Everything under `hooks/` is specific to Claude Code. Keep `storage/`, `pipeline/`, `server/`, and `hooks/preview.py` free of Claude Code assumptions, so that adapters for other harnesses (Antigravity, Codex CLI, Cursor) can be added as new modules under `hooks/adapters/` later.

### 17.8 Open questions for the maintainer

1. Should facts be shareable across projects (for example, personal coding preferences)? This spec scopes everything by project. A user-level scope could be added as `project_id = "user"` and included in every search.
2. Should the extractor (Section 9.6) be allowed to use the harness's own subscription instead of an API key? This depends on what the harness permits.
3. What is the right default for auto recall once the evaluation set exists?

---

## Appendix A: Target source layout

```
src/mem_plane/
├── __init__.py
├── cli.py
├── config.py
├── project.py
├── core/
│   ├── models.py
│   ├── rrf.py                 # weighted_rrf
│   ├── redact.py
│   └── staleness.py
├── pipeline/
│   ├── embeddings.py          # protocol, FastEmbedClient, DummyEmbeddingClient, factory
│   └── embedder_worker.py     # background embedding loop
├── storage/
│   ├── protocols.py           # MemoryStore
│   ├── sqlite/
│   │   ├── connection.py
│   │   ├── schema.py
│   │   ├── migrations.py
│   │   ├── store.py           # sync implementation
│   │   ├── async_store.py     # asyncio.to_thread wrapper
│   │   └── search.py
│   └── production/            # frozen
├── server/
│   ├── mcp_server.py
│   ├── service.py             # tool logic
│   ├── formatting.py          # text output (Section 7.5)
│   └── tool_text.py           # static tool names and descriptions
├── hooks/
│   ├── runner.py
│   ├── inputs.py              # Pydantic models for hook payloads
│   ├── post_tool_use.py
│   ├── preview.py
│   ├── transcript.py
│   ├── stop.py
│   ├── pre_compact.py
│   ├── session_start.py
│   ├── session_end.py
│   └── user_prompt_submit.py
└── sdk/
    ├── prompt_zones.py        # former governor
    └── pruner.py              # wrapper over hooks/preview.py
plugin/                         # Section 14.2
eval/retrieval/                 # Section 15.3
tests/fixtures/claude_code/     # Section 15.2
```

## Appendix B: Dependency changes in `pyproject.toml`

```toml
[project.optional-dependencies]
sqlite = ["aiosqlite>=0.20.0", "sqlite-vec>=0.1.6", "sqlean.py>=3.45.0"]
embed = ["fastembed>=0.3.0"]
mcp = ["mcp>=1.0.0"]
harness = ["mem-plane[sqlite,embed,mcp]"]
production = ["asyncpg>=0.29.0", "opensearch-py>=2.6.0", "aiohttp>=3.9.0"]  # frozen

[project.scripts]
mem-plane = "mem_plane.cli:main"
```

`numpy` stays a core dependency only if `DummyEmbeddingClient` needs it. Otherwise move it to the test extras, so hooks stay light. Pin exact versions in a lock file (`uv.lock`) for CI.
