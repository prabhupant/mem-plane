# Changelog

All notable changes to `mem-plane` will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.1.0] - 2026-11-20

### Breaking Changes
- Replaced initial v0 MCP toolset with the standardized 7-tool suite:
  - Removed `search_episodic_memory` (superseded by `search_memory`).
  - Removed `inspect_fact_provenance` (superseded by `get_memory` for `f_...` facts).
  - Removed `dereference_raw_trace` (superseded by `get_memory` with `grep`, `lines`, or truncation for `t_...` traces).
  - Updated `record_fact` signature to take `summary`, `body`, `kind`, `paths`, `supersedes`, and `pin`.
  - Removed client-side `project_id` / `tenant_id` parameters from all MCP tools. Project scoping is fixed for the MCP server process lifetime.
  - Search tools now return token-efficient plain text rather than indented JSON.

### Added
- Local zero-infrastructure SQLite storage engine (`~/.mem-plane/memory.db`) with FTS5 lexical search and sqlite-vec vector retrieval.
- `FastEmbedClient` embedding provider supporting `BAAI/bge-small-en-v1.5` with lazy worker-thread initialization and L2 normalization.
- Background embedding worker processing pending items every 2 seconds.
- 4-list Reciprocal Rank Fusion (`weighted_rrf`) combining fact keyword, fact vector, episode keyword, and episode vector hits.
- Static MCP tool definitions in `src/mem_plane/server/tool_text.py` with stability hash testing.
- Automatic file staleness tracking for facts linked to workspace file paths.
