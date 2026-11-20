# Optional Features & Token Cost Analysis

This document describes the three optional features implemented in Milestone 7 (§9.6, §9.8, §13):
1. **`UserPromptSubmit` Auto-Recall Hook**
2. **Background Fact Extractor**
3. **Custom Agent Loop SDK**

All three features are **disabled by default** to minimize token consumption and avoid unintended out-of-band API costs.

---

## 1. `UserPromptSubmit` Automatic Recall Hook

### Overview (§9.8)
When enabled, the `UserPromptSubmit` hook executes a fast, keyword-only search over the project's active facts in SQLite whenever a user submits a prompt to Claude Code. If any facts match with high confidence, concise references are appended to the context.

### Configuration
```bash
# Enable automatic recall (default: 0 / false)
export MEM_PLANE_AUTO_RECALL=1

# Minimum BM25 score for matches (default: 6.2, tuned in docs/benchmarks.md)
export MEM_PLANE_AUTO_RECALL_MIN_SCORE=6.2
```

### Execution Mechanics
- **Zero Heavy Imports:** Runs in <15 ms without loading `fastembed`, `numpy`, or `mcp`.
- **Pure Lexical Search:** Queries `facts_fts` using SQLite FTS5 `bm25(facts_fts, 0.0, 0.0, 2.0, 1.0)`.
- **Threshold Cutoff:** Only facts with `-bm25 >= AUTO_RECALL_MIN_SCORE` (6.2) are retained. If no facts pass, the hook prints nothing.
- **Budget Enforced:** Format is capped at 800 characters:
  `mem-plane: possibly relevant memories: f_1b2c3d4e5f6a <summary>; f_7a8b9c0d1e2f <summary>`

### Token & Cost Impact
- **External API Calls:** `0` (runs completely locally within SQLite).
- **Context Token Cost:**
  - When no match passes threshold (majority of prompts): **0 tokens added**.
  - When matches pass threshold: **~30 to 180 input tokens** added to `additionalContext`.
  - **Cache Impact:** Context is appended at the end of the prompt, preserving prefix prompt cache. However, once injected, these tokens remain in dialogue context until the next compaction.
- **Recommendation:** Keep off unless working in large legacy codebases with hundreds of rules where proactive reminders help prevent regressions.

---

## 2. Background Fact Extractor

### Overview (§9.6)
The fact extractor automatically scans conversational dialogue from completed transcript segments (after a context compaction or session termination), identifies non-obvious engineering decisions, constraints, or bug root causes, and records them to `facts` with `source='extractor'`.

### Configuration
```bash
# Set extractor mode to anthropic (default: 'off')
export MEM_PLANE_EXTRACTOR=anthropic

# Specify the cheap extraction model (default: 'claude-3-5-haiku-latest')
export MEM_PLANE_EXTRACTOR_MODEL=claude-3-5-haiku-latest

# Provide Anthropic API key
export ANTHROPIC_API_KEY="sk-ant-api03-..."
```

### Execution Mechanics
- **Segment Grouping:** Chunks are grouped by `(session_id, segment)` once the segment is closed.
- **Structured Extraction:** A small, fast model extracts up to 8 facts formatted as JSON with `kind` (`decision`, `rule`, `bugfix`, `convention`), `summary`, `body`, and affected file `paths`.
- **Deduplication:** Facts are submitted through `store.create_fact()`, which checks SHA-256 content hashes. Duplicate facts are automatically skipped.
- **Pinning Invariant:** Extracted facts are **never** pinned automatically (`pinned = 0`).

### Token & Cost Impact
- **Out-of-Band API Calls:** Exactly 1 API call per closed transcript segment (only triggered after compaction or session end; never during active turns).
- **Prompt Input Tokens:** ~1,500 to 5,000 tokens per closed segment.
- **Completion Output Tokens:** ~300 to 800 tokens per extraction.
- **Estimated Cost:**
  - Using `claude-3-5-haiku-latest` ($0.80 / MTok input, $4.00 / MTok output):
  - Average cost per compaction event: **~$0.002 to $0.005 USD**.
- **Recommendation:** Recommended for long multi-day development sprints where developers frequently forget to explicitly call `record_fact`.

---

## 3. Custom Agent Loop SDK

### Overview (§13)
For developers building autonomous agents with the Anthropic or OpenAI Python APIs, `mem-plane` provides the `sdk` module to structure prompts into cache-invariant zones and prune tool outputs.

### Architecture
- **`PromptZoneGovernor` (`mem_plane.sdk.prompt_zones`)**:
  - **Zone 1:** Static instructions and sorted hydration rules (`build_zone_1`).
  - **Zone 2:** Breakpoint delimiter (`CACHE_BREAKPOINT`).
  - **Zone 3:** Deterministically sorted memory surrogates (`build_zone_3`).
  - **Zone 4:** Dialogue turns with compressed tool outputs (`build_zone_4`).
- **`EphemeralToolPruner` (`mem_plane.sdk.pruner`)**:
  - Intercepts raw tool outputs and generates compact preview banners with line numbers and failure signals while storing full traces out-of-band.

### Example
See [`examples/custom_loop.py`](../examples/custom_loop.py) for a complete working implementation with in-process tool calling.

### Token & Cost Impact
- **External API Calls:** `0` (runs in-process).
- **Prompt Cache Savings:**
  - Setting `cache_control: {"type": "ephemeral"}` on Zone 1 achieves an **80–90% caching discount** on prefix tokens (Anthropic cache reads cost 0.10x of uncached tokens).
- **Context Pruning Savings:**
  - Saves **500 to 10,000 tokens per verbose tool call** (compiler traces, test outputs) by replacing spam with a high-signal 30-signal preview.
