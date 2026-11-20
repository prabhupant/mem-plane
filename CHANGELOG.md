# Changelog

All notable changes to `mem-plane` will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.1.0] - 2026-11-20

### Breaking Changes
- **Tool Suite Standardized**: Replaced legacy v0 MCP tools with the standardized 7-tool suite:
  - Removed `search_episodic_memory` (superseded by `search_memory`).
  - Removed `inspect_fact_provenance` (superseded by `get_memory` for `f_...` facts).
  - Removed `dereference_raw_trace` (superseded by `get_memory` with `grep`, `lines`, or truncation for `t_...` traces).
  - Standardized `record_fact`, `update_memory`, `forget_memory`, `pin_memory`, and `memory_status`.
- **Project Scoping**: Removed client-side `project_id` / `tenant_id` arguments from all tools. Project scoping is fixed to the git repository lifetime.
- **Backend Freeze**: Frozen production backend strictly to SQLite (`FTS5` + `sqlite-vec`). Distributed backends (PostgreSQL, pgvector, OpenSearch) have been removed from the production surface (`docs/storage.md`).
- **SDK Organization**: Moved `PromptZoneGovernor` to `mem_plane.sdk.prompt_zones` and `EphemeralToolPruner` to `mem_plane.sdk.pruner`.
- **Single Skill**: Replaced multiple verbose markdown skills with a single unified, token-disciplined `SKILL.md` (<70 lines).
- **Plain-Text Retrieval Format**: Search responses now return token-efficient, plain-text surrogates instead of indented JSON.

### Added
- **Claude Code Hook Harness**:
  - `PostToolUse`: High-speed tool output pruner generating deduplicated previews and saving full outputs out-of-band to `tool_traces` (`t_` + 10 hex).
  - `SessionStart`: Injects structured memory index (header counts, pinned facts, compactions note, recent facts) within a strict 6,000-character budget.
  - `Stop`: Asynchronous incremental transcript archiver writing chunks to `episodes`.
  - `PreCompact`: Synchronous compaction flush and session compaction counter increment.
  - `SessionEnd`: Finalizes session transcripts and records ended timestamp.
  - Sub-15ms execution time on hot paths with zero heavy imports (`fastembed`, `numpy`, and `mcp` are strictly prohibited on hook execution paths).
  - Fail-open safety: All hooks log errors to `~/.mem-plane/hooks.log` and exit with code 0 without disrupting Claude Code.
- **Transcript Archiving & Redaction**:
  - Automated regex redaction for AWS keys, GitHub tokens, Anthropic API keys, OpenAI keys, Slack tokens, JWTs, PEM private keys, and environment secret assignments before storage.
  - Chunks dialogue at ~4,800 characters while preserving turn boundaries.
- **CLI Commands**:
  - `mem-plane serve`: Launch stdio MCP server.
  - `mem-plane hook <name>`: Execute lifecycle hook.
  - `mem-plane doctor`: Complete environment and health verification (Python version, extensions, permissions, PATH, hooks configuration).
  - `mem-plane status`: Overview of project memory statistics and embedding queue.
  - `mem-plane search`: CLI memory search.
  - `mem-plane embed-pending`: Drain pending embedding queue.
  - `mem-plane reindex`: Clear and rebuild lexical FTS5 and vector indices.
  - `mem-plane gc`: Purge expired tool traces and optimize FTS index.
  - `mem-plane export` / `import`: Project memory backup and migration.
  - `mem-plane bench`: Multi-run token savings and pruning statistics benchmark.
- **Claude Code Plugin Bundle (`plugin/`)**:
  - `.claude-plugin/plugin.json`: Official Claude Code plugin manifest.
  - `.mcp.json`: stdio MCP server registration.
  - `hooks/hooks.json`: Lifecycle hook configurations.
  - `skills/mem-plane/SKILL.md`: Concise agent guide.
- **Hybrid Retrieval Engine**:
  - Reciprocal Rank Fusion over 4 lists (fact vector, fact keyword, episode vector, episode keyword).
  - FastEmbed client with `BAAI/bge-small-en-v1.5` and lazy background worker.
  - Prompt-cache stable static tool declarations in `server/tool_text.py`.
