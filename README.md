# 🧠 mem-plane

<div align="center">

### **Zero-Infra Memory Harness & Context Governor for Claude Code**

*Prune terminal spam, eliminate context loss on compaction, and preserve prompt cache prefixes.*  
*Runs 100% locally on embedded SQLite with sub-15ms hook latency.*

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg?style=flat-square)](https://opensource.org/licenses/MIT)
[![Python 3.10+](https://img.shields.io/badge/Python-3.10%20%7C%203.11%20%7C%203.12-blue.svg?style=flat-square)](https://www.python.org/downloads/)
[![Strict Typing](https://img.shields.io/badge/mypy-strict-brightgreen.svg?style=flat-square)](https://mypy-lang.org/)
[![Ruff](https://img.shields.io/endpoint?url=https://raw.githubusercontent.com/astral-sh/ruff/main/assets/badge/v2.json&style=flat-square)](https://github.com/astral-sh/ruff)
[![MCP Ready](https://img.shields.io/badge/MCP-2.0%20Compatible-green.svg?style=flat-square)](https://modelcontextprotocol.io/)
[![Tests](https://img.shields.io/badge/tests-153%20passed-success.svg?style=flat-square)](https://github.com/prabhupant/mem-plane)

[Quickstart](#quickstart-60-second-setup) • [Why mem-plane](#the-problem-claude-code-without-mem-plane) • [How It Works](#how-it-works) • [Benchmarks](#empirical-benchmarks) • [MCP Tools](#mcp-tool-suite) • [CLI](#cli-reference) • [Python SDK](#python-sdk) • [Documentation](#documentation)

</div>

---

## The Problem: Claude Code on Real Projects

When developing complex software with Claude Code, three context inefficiencies degrade reasoning and inflate costs:

```
WITHOUT MEM-PLANE:
┌────────────────────────────────────────────────────────────────────────┐
│ Turn 1..10:   Test runner & git diffs dump 3,000 lines of noise into    │
│               the context window (~25,000 characters per call).        │
│                                                                        │
│ Turn 11:      Prompt context exceeds token limit.                      │
│               Claude Code triggers /compact.                           │
│                                                                        │
│ Turn 12+:     Compaction summary summarizes away critical architectural│
│               constraints, numerical constants, and root causes.       │
│               Model hallucinates or re-introduces solved bugs.         │
└────────────────────────────────────────────────────────────────────────┘

WITH MEM-PLANE:
┌────────────────────────────────────────────────────────────────────────┐
│ Turn 1..10:   PostToolUse hook intercepts noisy output in <15ms.        │
│               Extracts 30 failure signals with line numbers.           │
│               Saves full 25KB trace out-of-band to SQLite (t_...).     │
│                                                                        │
│ Turn 11:      Lean context delays compaction by 50%.                   │
│                                                                        │
│ Turn 12+:     SessionStart hook re-injects pinned rules & recent facts │
│               within a strict 6,000-character budget.                  │
│               Recall quiz accuracy jumps from 45% to 97%.              │
└────────────────────────────────────────────────────────────────────────┘
```

---

## Key Features

- ⚡ **Sub-15ms Fast-Path Execution:** Lifecycle hooks run locally over standard I/O with zero heavy imports (`fastembed`, `numpy`, and `mcp` are strictly isolated from hook hot paths).
- 📉 **54.5% Cost-Weighted Token Reduction:** Eliminates repetitive terminal spam from prompt history across multi-turn sessions.
- 🧠 **Context Loss Immunity:** Re-injects pinned project decisions, architectural constraints, and recent memories at `SessionStart` (startup, resume, `/clear`, and `/compact`).
- 🔒 **Zero External Infrastructure:** 100% local embedded SQLite with `FTS5` (BM25 lexical search) and `sqlite-vec` (384-d cosine similarity). No Docker, no open ports, no credentials.
- 🛡️ **Fail-Open Safety:** Hook errors log silently to `~/.mem-plane/logs/hooks.log` and exit `0` with the original unmutated payload. A failure in `mem-plane` will never interrupt an active coding session.
- 📜 **Lossless Transcript Archive:** Turns are archived incrementally in the background with automated secrets redaction (AWS, GitHub, Slack, OpenAI, Anthropic, JWTs, PEM keys).
- 🎯 **Prompt-Cache Prefix Stability:** Immutable MCP tool schemas and bit-stable descriptions in `server/tool_text.py` lock in Anthropic prompt-caching discounts.

---

## Quickstart (60-Second Setup)

### 1. Install

```bash
# Install with local embedding support (recommended)
pip install "mem-plane[embed]"

# Verify system compatibility and SQLite extensions
mem-plane doctor
```

### 2. Connect to Claude Code

#### Option A: Claude Code Plugin (Recommended)

```bash
claude plugin add /path/to/mem-plane/plugin
```

#### Option B: Direct Configuration

Add `mem-plane` to your MCP servers (`~/.claude/mcp.json` or `.mcp.json`):

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

Add lifecycle hooks to your settings (`~/.claude/settings.json` or `.claude/settings.json`):

```bash
# Print ready-to-paste hook configuration:
mem-plane doctor --print-hooks
```

---

## How It Works

`mem-plane` combines a silent hook harness with a standardized stdio MCP server:

```
                         Claude Code Engine
                                 │
     ┌───────────────────────────┴───────────────────────────┐
     │ Lifecycle Hooks                                       │ MCP Protocol
     │ (Fast Path: <15ms, zero heavy imports)                │ (Stdio Service)
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

### 1. Tool-Output Pruning (`PostToolUse`)
When a command outputs more than 15 lines or 600 characters, `PostToolUse` stores the raw stdout/stderr in the SQLite `tool_traces` table (`t_` + 10 hex) and returns a deduplicated preview banner:
- First 15 head lines + last 25 tail lines.
- Extracted compiler errors, test failures, and stack traces with exact original line numbers.
- The model can dereference the full raw output on demand using `get_memory(ref="t_...")` with regex `grep` or `lines` range filtering.
- **Protected Tools:** `Read`, `Edit`, and `Write` are **never** pruned; only command outputs (`Bash`, `fetch_web_page`, `WebSearch`) are shortened.

### 2. Session Context Rehydration (`SessionStart`)
At session start and immediately after `/compact`, `mem-plane` builds a compact memory index:
- **Pinned Facts:** High-priority invariants (coding conventions, DB constraints).
- **Session Facts:** Facts recorded earlier in the current session.
- **Recent Facts:** Up to 8 recently updated memories for the active repository.
- Strictly budgeted under 6,000 characters and emitted via `hookSpecificOutput.additionalContext`.

### 3. Lossless Transcript Archive (`Stop`, `PreCompact`, `SessionEnd`)
- Conversational dialogue is read incrementally and chunked into ~4,800-character records in `episodes`.
- Secrets (AWS keys, GitHub tokens, Slack credentials, private keys, API keys) are redacted with regex masks before writing to disk.

---

## Empirical Benchmarks

Evaluated against baseline Claude Code across 5 multi-turn coding tasks (30+ turns each, 5 runs per task, 50 total runs; see [`docs/benchmarks.md`](docs/benchmarks.md)):

| Metric | Baseline Claude Code | With mem-plane | Change |
| :--- | :--- | :--- | :--- |
| **Weighted Input Tokens** | 236,412 (208,204 - 274,246) | 107,610 (94,221 - 125,352) | **-54.5%** |
| **Compactions Per Run** | 2 (min 2, max 2) | 1 (min 1, max 1) | **-50.0%** |
| **Post-Compaction Recall Quiz** | 45.0% (40.0% - 50.0%) | 97.0% (95.0% - 99.0%) | **+52.0%** |
| **Task Success Rate** | 100.0% | 100.0% | Parity (100%) |

*Cost-weighted multipliers per Anthropic pricing ratios: 1.0x uncached input, 1.25x cache creation write, 0.10x cache read.*

### Tool-Output Pruning Savings
- **Characters saved per pruned command:** ~21,000 characters median (~5,250 tokens per pruned command).
- **Trace dereference rate:** < 5% of runs needed `get_memory` on full traces because the 30-signal preview retained essential error context.

---

## MCP Tool Suite

The stdio MCP server exposes 7 standardized tools with bit-stable descriptions:

| Tool | Parameters | Description |
| :--- | :--- | :--- |
| `search_memory` | `query`, `top_k?`, `include_episodes?` | Hybrid search (FTS5 BM25 + sqlite-vec RRF) over curated facts and past episode transcripts. |
| `get_memory` | `ref`, `grep?`, `lines?`, `max_chars?` | Retrieve full fact details (`f_...`), episode chunks (`e_...`), or view/grep tool traces (`t_...`). |
| `record_fact` | `summary`, `body`, `kind`, `paths?`, `supersedes?`, `pin?` | Store an architectural decision, rule, bugfix, convention, context, or note. |
| `update_memory` | `fact_id`, `summary?`, `body?`, `kind?` | Update an existing fact while recording audit history in `fact_history`. |
| `forget_memory` | `fact_id`, `reason?` | Soft-delete a fact when it is no longer relevant. |
| `pin_memory` | `fact_id`, `pinned` | Pin or unpin a critical rule so it is always present at session start. |
| `memory_status` | *(none)* | Inspect database health, active fact counts, and embedding queue depth. |

---

## CLI Reference

`mem-plane` provides a complete command-line toolkit:

```bash
# Run environment diagnostics and check permissions
mem-plane doctor

# Print ready-to-paste hook configuration for .claude/settings.json
mem-plane doctor --print-hooks

# Launch stdio MCP server
mem-plane serve

# Inspect memory statistics for the current repository
mem-plane status

# Search memory directly from the terminal
mem-plane search "order discount calculation"

# Process pending vector embeddings
mem-plane embed-pending

# Rebuild FTS5 and vector indices
mem-plane reindex

# Purge expired tool traces (>14 days) and optimize FTS index
mem-plane gc

# Backup and restore project memories (JSONL)
mem-plane export > memories.jsonl
mem-plane import memories.jsonl

# Run token savings benchmark
mem-plane bench
```

---

## Python SDK

For custom agent loops and LLM orchestration pipelines, `mem-plane` provides modular SDK utilities:

### 4-Zone Prompt Governor

Enforce prompt cache invariance by structuring prompts into 4 deterministic zones:

```python
from mem_plane.core.models import HydrationBundle
from mem_plane.sdk import PromptZoneGovernor

governor = PromptZoneGovernor()

# Zone 1: Static instructions & tenant hydration (byte-identical across turns)
bundle = HydrationBundle(
    tenant_rules=["All monetary amounts must be integers in cents; never floats."]
)
zone1 = governor.build_zone_1("You are an autonomous order engineer.", bundle)

# Zone 2: Cache delimiter breakpoint (<!-- MEM_PLANE_CACHE_BREAKPOINT -->)
zone2 = governor.build_zone_2()

print(f"{zone1}{zone2}")
```

### In-Process Ephemeral Tool Pruner

Prune verbose outputs directly within custom Python loops:

```python
from mem_plane.sdk import EphemeralToolPruner

pruner = EphemeralToolPruner(max_lines=15, max_chars=600)

preview, trace_id = pruner.prune(large_test_output)
if trace_id:
    print(f"Output shortened! Trace ID: {trace_id}")
    print(preview)
```

See [`examples/custom_loop.py`](examples/custom_loop.py) for a complete working implementation with Anthropic prompt-caching headers and in-process tool dispatch.

---

## Configuration Reference

Configure `mem-plane` via environment variables or a `.env` file:

| Variable | Default | Description |
| :--- | :--- | :--- |
| `MEM_PLANE_DB_PATH` | `~/.mem-plane/memory.db` | Local SQLite database file path. |
| `MEM_PLANE_LOG_DIR` | `~/.mem-plane/logs` | Directory for logs and debug payloads. |
| `MEM_PLANE_EMBEDDING_PROVIDER` | `openai` | Embedding provider (`openai`, `fastembed`, `none`, `dummy`). |
| `MEM_PLANE_EMBEDDING_MODEL` | `text-embedding-3-small` | Dense vector embedding model name (`text-embedding-3-small`, `BAAI/bge-small-en-v1.5`). |
| `OPENAI_API_KEY` | *(env)* | OpenAI API key for vector embeddings. |
| `MEM_PLANE_PRUNE_ENABLED` | `1` | Enable or disable `PostToolUse` pruner. |
| `MEM_PLANE_PRUNE_MAX_LINES` | `120` | Output line threshold before pruning. |
| `MEM_PLANE_PRUNE_MAX_CHARS` | `8000` | Output character threshold before pruning. |
| `MEM_PLANE_TRACE_RETENTION_DAYS` | `14` | Expiration window for raw tool traces. |
| `MEM_PLANE_SESSION_CONTEXT_MAX_CHARS` | `6000` | Maximum character budget for `SessionStart` context. |
| `MEM_PLANE_AUTO_RECALL` | `0` | Enable prompt-time keyword memory recall (off by default). |
| `MEM_PLANE_AUTO_RECALL_MIN_SCORE` | `6.2` | Minimum BM25 score for auto-recall hits. |
| `MEM_PLANE_EXTRACTOR` | `off` | Background fact extractor mode (`off`, `anthropic`). |

---

## Documentation

- 📐 [Architecture & Systems Design](docs/architecture.md) — Comprehensive guide to hooks, MCP, and data models.
- 💾 [Storage Architecture & SQLite Decision](docs/storage.md) — Rationale for embedded SQLite over distributed vector stores.
- 📊 [Empirical Benchmarks](docs/benchmarks.md) — Full 50-run benchmark protocol, medians, and recall quiz results.
- ⚡ [Optional Features & Cost Analysis](docs/optional_features.md) — Auto-recall hook, fact extractor, and token cost breakdown.
- 📋 [Implementation Checklist](checklist.md) — Milestone completion log, test statuses, and git commit history.

---

## Contributing & License

Contributions are welcome! Please ensure all pull requests pass `pytest`, `mypy --strict`, and `ruff check`.

Distributed under the MIT License. See [LICENSE](LICENSE) for details.
