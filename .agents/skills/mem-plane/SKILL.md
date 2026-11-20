---
name: mem-plane
description: Use when you make a durable decision, find a bug's root cause, learn a project rule, need context from earlier in this or past sessions, or see a mem-plane trace ID.
---

# mem-plane: Persistent Memory for Claude Code

Automatic memory harness providing cross-session search, durable facts, and output compression.

## 1. When to Record
Use `record_fact` for durable decisions and their reasons, project rules and conventions, bug root causes and fixes, and non-obvious environment setup.
Do NOT record transient progress updates, details already documented in README/code, or credentials/secrets.

## 2. Writing Facts
- **Summary**: Exactly one self-contained sentence naming the specific file, function, or setting, understandable without surrounding context.
- **Body**: Detailed rationale, architectural context, and minimal code.
- **Paths**: Always supply `paths` when the fact governs specific files to enable automatic staleness checking.

## 3. Keeping Memory Correct
- Always call `search_memory` before recording to prevent duplication.
- If an existing fact is outdated, use `update_memory` or set `supersedes="f_..."`.
- When search results show `[STALE: ...]`, verify against source files before relying on the memory, then update or forget it.

## 4. Pinning Rules
Pin only project-wide invariants that apply to almost every conversation (e.g. integer money units, build scripts). Keep pinned facts under 10.

## 5. Pruned Traces
When command output shows `[mem-plane: output shortened ... stored as t_...]`, use `get_memory(ref="t_...", grep="pattern")` or `lines="10-30"` to inspect details instead of re-running the command.

## 6. After Compaction
When context is compacted, consult the memory index in context or use `search_memory` to recover earlier conversation context without asking the user to repeat themselves.
