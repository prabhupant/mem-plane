# 🧠 `mem-plane`

<div align="center">

### **Zero-Infra Memory Harness & Context Governor for Claude Code**

*Prune terminal spam, preserve prompt cache prefix, lossless transcript archive, and hybrid memory retrieval—embedded directly into Claude Code via lifecycle hooks.*

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg?style=flat-square)](https://opensource.org/licenses/MIT)
[![Python 3.10+](https://img.shields.io/badge/Python-3.10%20%7C%203.11%20%7C%203.12-blue.svg?style=flat-square)](https://www.python.org/downloads/)
[![Strict Typing](https://img.shields.io/badge/mypy-strict-brightgreen.svg?style=flat-square)](https://mypy-lang.org/)
[![Ruff](https://img.shields.io/endpoint?url=https://raw.githubusercontent.com/astral-sh/ruff/main/assets/badge/v2.json&style=flat-square)](https://github.com/astral-sh/ruff)
[![MCP Ready](https://img.shields.io/badge/MCP-2.0%20Compatible-green.svg?style=flat-square)](https://modelcontextprotocol.io/)

[Why mem-plane](#why-mem-plane) • [Installation](#installation) • [How It Works](#how-it-works) • [Benchmarks](#benchmarks) • [MCP Tools](#mcp-tools) • [CLI Reference](#cli-reference) • [Python SDK](#python-sdk)

</div>

---

## Why mem-plane?

When using Claude Code on real projects, three problems routinely waste tokens and degrade reasoning:
1. **Tool-Output Flooding:** Test suites, compiler errors, and build tools dump hundreds of lines into the context window, consuming tokens and pushing valuable context out of the window.
2. **Context Loss on Compaction:** When context window fills and Claude Code compacts, specific decisions, architectural constraints, and numerical parameters are summarized away or forgotten.
3. **Prefix Prompt-Cache Busted:** Unstructured, dynamic prompt rewriting breaks Anthropic's left-to-right prompt caching discounts.

`mem-plane` solves these problems automatically with zero infrastructure:
- **Fast-path tool pruning (<15 ms):** Automatically intercepts verbose tool outputs (`Bash`, web fetches), extracts deduplicated failure signals with line numbers, replaces the output with a compact preview, and stores the raw trace out-of-band in SQLite.
- **Session context rehydration:** Injects pinned architectural rules and recent memories at `SessionStart` (startup, resume, and after `/compact`) within a strict character budget.
- **Lossless transcript archive:** Incrementally archives conversational turns in the background with automated secrets redaction (AWS, GitHub, Slack, OpenAI, Anthropic, private keys).
- **Hybrid RRF retrieval:** Combines exact-token BM25 keyword search (`SQLite FTS5`) and dense vector search (`sqlite-vec`) without external database daemons.
- **Prompt cache stability:** Fixed tool schemas and immutable descriptions in `server/tool_text.py` guarantee prompt cache prefix stability.

---

## Installation

### 1. Install via pip

```bash
# Install with embedding support (recommended)
pip install "mem-plane[embed]"

# Run doctor to verify system requirements and SQLite extensions
mem-plane doctor
```

### 2. Configure Claude Code

You can use `mem-plane` either as a Claude Code Plugin or by manually adding the MCP server and hooks.

#### Option A: Claude Code Plugin (Recommended)

Point Claude Code to the `plugin/` directory in this repository:

```bash
claude plugin add /path/to/mem-plane/plugin
```

#### Option B: Manual Configuration

Add `mem-plane` to your Claude Code MCP settings (`~/.claude/mcp.json` or project `.mcp.json`):

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

Configure hooks in `~/.claude/hooks.json` or your project `.claude/hooks.json`:

```json
{
  "hooks": {
    "SessionStart": [
      {
        "type": "command",
        "command": "mem-plane hook session-start"
      }
    ],
    "PostToolUse": [
      {
        "type": "command",
        "command": "mem-plane hook post-tool-use"
      }
    ],
    "Stop": [
      {
        "type": "command",
        "command": "mem-plane hook stop"
      }
    ],
    "PreCompact": [
      {
        "type": "command",
        "command": "mem-plane hook pre-compact"
      }
    ],
    "SessionEnd": [
      {
        "type": "command",
        "command": "mem-plane hook session-end"
      }
    ]
  }
}
```

Run `mem-plane doctor --print-hooks` to generate the exact JSON configuration for your shell environment.

---

## How It Works

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

1. **Invisible Lifecycle Interception:** Claude Code invokes `mem-plane hook <name>` over stdio. Hook execution is optimized to finish in <15 ms, importing only standard library modules and SQLite.
2. **Tool-Output Pruning:** When a command outputs more than 15 lines or 600 characters, `PostToolUse` stores the unpruned stdout/stderr in SQLite table `tool_traces` (`t_` + 10 hex). Claude Code receives a preview banner containing head lines, extracted failure signals with line numbers, and tail lines. If needed, the model calls `get_memory(id="t_...")` with optional grep or line filters.
3. **Fail-Open Reliability:** Any hook exception is silently appended to `~/.mem-plane/hooks.log` while the original payload is returned unchanged with exit code 0. `mem-plane` will never interrupt an active coding session.
4. **Project Scoping:** Every memory is scoped to a deterministic `project_id` derived from the git remote URL (or repository root path). Memories never leak across projects.

---

## Benchmarks

Empirical evaluation against baseline Claude Code on multi-turn software development tasks (5 tasks × 5 runs each, 50 total runs; see [`docs/benchmarks.md`](docs/benchmarks.md)):

| Metric | Baseline Claude Code | With mem-plane | Change |
| :--- | :--- | :--- | :--- |
| **Weighted Input Tokens** | 236,412 (208,204 - 274,246) | 107,610 (94,221 - 125,352) | **-54.5%** |
| **Compactions Per Run** | 2 (min 2, max 2) | 1 (min 1, max 1) | **-50.0%** |
| **Post-Compaction Recall Quiz** | 45.0% (40.0% - 50.0%) | 97.0% (95.0% - 99.0%) | **+52.0%** |
| **Task Success Rate** | 100.0% | 100.0% | Parity (100%) |

*Cost-weighted multipliers per Anthropic published pricing ratios: 1.0x uncached input, 1.25x cache creation write, 0.10x cache read.*

---

## MCP Tools

The stdio MCP server exposes 7 standardized tools:

| Tool | Parameters | Description |
| :--- | :--- | :--- |
| `search_memory` | `query`, `limit?`, `min_score?` | Hybrid search (FTS5 BM25 + sqlite-vec RRF) over curated facts and past episode transcripts. |
| `get_memory` | `id`, `grep?`, `lines?` | Retrieve full fact details (`f_...`) or view/grep pruned tool traces (`t_...`). |
| `record_fact` | `summary`, `body`, `kind`, `paths?`, `supersedes?`, `pin?` | Store a project decision, rule, bugfix, convention, context, or note. |
| `update_memory` | `fact_id`, `summary?`, `body?`, `paths?` | Update an existing fact while recording audit history in `fact_history`. |
| `forget_memory` | `fact_id`, `reason?` | Soft-delete a fact when it is no longer relevant. |
| `pin_memory` | `fact_id`, `pinned` | Pin or unpin a critical rule so it is always present at session start. |
| `memory_status` | *(none)* | Inspect database health, fact counts, and embedding queue depth. |

---

## CLI Reference

`mem-plane` provides a comprehensive command-line suite:

```bash
# Verify environment and configuration
mem-plane doctor
mem-plane doctor --print-hooks

# Launch MCP server over stdio
mem-plane serve

# Inspect project memory statistics
mem-plane status

# Search memory directly from terminal
mem-plane search "authentication token refresh"

# Process pending vector embeddings
mem-plane embed-pending

# Rebuild search indices
mem-plane reindex

# Purge expired traces (>14 days) and optimize FTS index
mem-plane gc

# Export and import project memories (JSONL)
mem-plane export > memories.jsonl
mem-plane import memories.jsonl

# Run token savings benchmark
mem-plane bench
```

---

## Python SDK

For custom agent loops and prompt engineering pipelines, `mem-plane` provides modular SDK utilities:

### 4-Zone Context Governor

Enforce prompt cache stability by separating static system prompts from dynamic turns:

```python
from mem_plane.sdk import PromptZoneGovernor, SurrogateFact

governor = PromptZoneGovernor()

# Zone 1: Static instructions & tenant hydration
zone1 = "You are a software engineer working on an order processing system."

# Zone 3: Curated memory surrogates (sorted deterministically by fact_id)
zone3 = [
    SurrogateFact(
        fact_id="f_000000000001",
        summary="Always use integer cents for monetary calculations.",
    )
]

# Zone 4: Dialogue turns
zone4 = [{"role": "user", "content": "How should order discounts be applied?"}]

rendered_prompt = governor.render(
    zone1_text=zone1,
    zone3_facts=zone3,
    zone4_turns=zone4,
)
```

### In-Process Tool Pruner

Shorten verbose outputs directly within Python agents:

```python
from mem_plane.sdk import EphemeralToolPruner

pruner = EphemeralToolPruner(line_budget=15, char_budget=600)

preview, raw_trace = pruner.prune(
    tool_name="pytest",
    raw_output=large_test_output,
)

if raw_trace:
    print(f"Pruned! Trace ID: {raw_trace.trace_id}")
    print(f"Preview:\n{preview}")
```

---

## Storage & Privacy

All data is stored locally in `~/.mem-plane/memory.db`. No code, transcripts, or queries are ever sent to external cloud vector databases or third-party servers.

See [`docs/storage.md`](docs/storage.md) for architectural details on the SQLite decision and [`docs/architecture.md`](docs/architecture.md) for the complete systems design.

---

## License

MIT License. See [LICENSE](LICENSE) for details.
