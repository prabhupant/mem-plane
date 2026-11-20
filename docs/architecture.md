# `mem-plane` Architecture & Systems Design

## 1. Overview

`mem-plane` is an automatic, zero-infrastructure memory harness and context governor designed specifically for **Claude Code**. It operates through two complementary interfaces:
1. **Claude Code Hook Engine**: Intercepts lifecycle events silently in the background (<15 ms latency) to prune verbose tool outputs, archive transcripts, track session compactions, and inject relevant context without model intervention.
2. **Standardized Model Context Protocol (MCP) Server**: Provides a disciplined 7-tool interface allowing the model to recall, inspect, record, pin, update, and manage persistent project memories with strict prompt-cache stability.

```
                         Claude Code Engine
                                 │
     ┌───────────────────────────┴───────────────────────────┐
     │ Lifecycle Hooks                                       │ MCP Protocol
     │ (Fast Path: <15ms, no heavy imports)                  │ (Stdio Service)
     ▼                                                       ▼
Hook Runner (`mem-plane hook <name>`)          MCP Service (`mem-plane serve`)
├── SessionStart (Inject memory index)          ├── search_memory (Hybrid 4-list RRF)
├── PostToolUse (Prune tool spam to traces)     ├── get_memory (Facts & Raw Traces)
├── Stop (Incremental transcript archive)       ├── record_fact (Curated facts)
├── PreCompact (Flush transcript & flag)        ├── update_memory (Edit facts)
└── SessionEnd (Finalize session archive)       ├── forget_memory (Soft delete)
                                                ├── pin_memory (Always-on facts)
                                                └── memory_status (DB health & stats)
     │                                                       │
     └───────────────────────────┬───────────────────────────┘
                                 │
                                 ▼
                     Local SQLite Storage Engine
                     Path: `~/.mem-plane/memory.db`
        ┌─────────────────────────────────────────────────────────┐
        │ WAL Mode + Busy Timeout 5000ms + Strict Project Scoping │
        ├────────────────────────────┬────────────────────────────┤
        │ Curated Relational Tables  │ Hybrid Search Indices      │
        │ • `projects`               │ • `facts_fts` (FTS5 BM25)  │
        │ • `facts` (f_...)          │ • `facts_vec` (sqlite-vec) │
        │ • `fact_history` (audit)   │ • `episodes_fts` (FTS5)    │
        │ • `sessions`               │ • `episodes_vec` (sqlite)  │
        │ • `episodes` (ep_...)      │                            │
        │ • `tool_traces` (t_...)    │                            │
        └────────────────────────────┴────────────────────────────┘
```

---

## 2. The Hook Harness Architecture

Claude Code lifecycle hooks run synchronously or asynchronously during tool execution and turn transitions. Because hooks execute on the hot path of user interaction, `mem-plane` enforces rigorous latency and safety requirements.

### 2.1 Fast-Path Execution Contract
- **Latency Target:** Under 150 milliseconds total execution time (measured median: ~15 ms for tool pruning).
- **Import Guard:** Hook paths **never** import heavy packages (`fastembed`, `numpy`, or `mcp`). All hook logic uses lightweight Python standard library modules and SQLite directly.
- **Fail-Open Design:** If any hook encounters an unhandled exception or corrupt payload, it silently logs the error to `~/.mem-plane/hooks.log`, returns the original unmutated input JSON, and exits with code `0`. A failure in `mem-plane` never breaks Claude Code.

### 2.2 Hook Pipeline Lifecycle

| Hook Event | Trigger Timing | Execution | Responsibility |
| :--- | :--- | :--- | :--- |
| **`SessionStart`** | Session startup, resume, compact, clear | Synchronous | Injects structured memory index (`additionalContext`) containing header counts, pinned facts, session compactions, and recent memories within a strict 6,000-character budget. |
| **`PostToolUse`** | Immediately after tool execution | Synchronous | Evaluates tool output lines and characters. If exceeding budget (default 15 lines or 600 chars), saves raw output to `tool_traces` (`t_` + 10 hex) and returns a deduplicated preview banner with line numbers and matched signals. Only prunes `Bash`, `fetch_web_page`, `WebSearch`; never touches `Read`, `Edit`, or `Write`. |
| **`Stop`** | At turn completion | Asynchronous | Incrementally parses new entries in the transcript JSONL file, redacts sensitive secrets, chunks dialogue at ~4,800 characters, and writes lossless records to `episodes`. |
| **`PreCompact`** | Immediately before context compaction | Synchronous | Flushes any pending transcript chunks up to the compaction boundary and increments the session's compaction counter in SQLite. |
| **`SessionEnd`** | When session terminates | Asynchronous | Flushes remaining transcript buffer and marks `ended_at` timestamp on the session record. |
| **`UserPromptSubmit`** *(Optional)* | User submits a prompt | Synchronous | Fast BM25-only search for high-scoring facts matching prompt keywords, injected into context when score threshold is satisfied. |

---

## 3. Storage & Retrieval Architecture

### 3.1 Relational Schema & Project Isolation
All operations are scoped to a deterministic `project_id` resolved from the git repository remote URL (or canonical root path). Codebases cannot read or write memories across project boundaries.
- **`facts`**: Persistent curated knowledge created via `record_fact` or background extraction. Contains short summaries (`<= 300` chars) and full markdown bodies.
- **`episodes`**: Complete, lossless conversational dialogue broken into structured segments separated by compaction events.
- **`tool_traces`**: Out-of-band ephemeral storage for shortened terminal logs and test outputs. Traces are automatically purged after expiration via `mem-plane gc`.

### 3.2 Hybrid Reciprocal Rank Fusion (RRF)
Search combines keyword and semantic signals across both facts and episode archives:
1. Lexical retrieval via SQLite `FTS5` with unicode61 tokenizer and porter stemmer.
2. Vector retrieval via `sqlite-vec` cosine similarity over 384-dimensional embeddings generated with `BAAI/bge-small-en-v1.5`.
3. 4-list Reciprocal Rank Fusion ($k=60$):
   $$RRF\_Score(d) = \sum_{m \in M} \frac{w_m}{60 + \text{rank}_m(d)}$$
   Weights: Fact Vector (`1.0`), Fact Keyword (`0.8`), Episode Vector (`0.5`), Episode Keyword (`0.4`).
4. Output formatting: Compact, token-disciplined plain text format with no markdown embellishments or JSON nesting.

---

## 4. MCP Interface & Prompt Cache Stability

Anthropic frontier models employ strict prefix prompt-caching. Any byte change in tool schema definitions or system instructions invalidates the entire KV-cache down the sequence.

### 4.1 Static Tool Declarations (`server/tool_text.py`)
- Tool names, parameter schemas, and descriptions are declared as immutable constants.
- Dynamic attributes (current timestamps, project paths, database counts) are strictly banned from tool descriptions.
- Automated tests assert that the SHA-256 hash of all tool definitions remains bit-for-bit identical across releases.

### 4.2 Standardized 7-Tool Interface
1. **`search_memory`**: Hybrid search over facts and archived episodes.
2. **`get_memory`**: Retrieve full bodies of facts (`f_...`) or grep/slice tool traces (`t_...`).
3. **`record_fact`**: Store decisions, architectural conventions, bugfix root causes, or context.
4. **`update_memory`**: Modify an existing fact, creating a revision history entry.
5. **`forget_memory`**: Soft-delete a fact when it becomes obsolete.
6. **`pin_memory`**: Mark a critical architectural rule to always appear at session startup.
7. **`memory_status`**: Report storage statistics, embedding queue status, and health metrics.

---

## 5. SDK & Context Governance

For standalone Python agents and custom agentic loops, `mem-plane` provides the `sdk` module:
- **`PromptZoneGovernor` (`sdk/prompt_zones.py`)**: Partitions context into 4 mathematically disciplined zones:
  - **Zone 1**: Static system instructions and hydrated tenant rules (byte-identical across turns).
  - **Zone 2**: Cache breakpoint delimiter marker (`<!-- MEM_PLANE_CACHE_BREAKPOINT -->`).
  - **Zone 3**: Retrieved memory surrogates sorted deterministically by `fact_id`.
  - **Zone 4**: Dynamic dialogue turns and pruned tool outputs.
- **`EphemeralToolPruner` (`sdk/pruner.py`)**: In-process tool output pruner for custom agent loops.
