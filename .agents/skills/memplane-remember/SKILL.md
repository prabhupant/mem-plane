---
name: memplane-remember
description: Extract and record durable architectural decisions, bug resolutions, and invariant rules into episodic memory.
---

# Episodic Memory Recording Skill

Use this skill whenever an important decision, design choice, or bug fix is established:

1. When to Remember:
- Architectural choices (e.g. "Chose SQLite FTS5 + sqlite-vec for dev storage").
- Bug root-causes and fixes (e.g. "Resolved SIGSEGV 139 by increasing worker thread stack size").
- Invariant rules and conventions (e.g. "Always use strict typing and ruff format").
- DO NOT record conversational filler, greetings, or ephemeral progress updates.

2. Structure the Memory:
- `summary`: Compact ~20-40 token surrogate summary capturing the essence.
- `raw_content`: Detailed technical body with code examples or reproduction steps.
- `source_type`: One of `"chat_turn"`, `"codebase"`, or `"rule"`.
- `provenance_metadata`: Include relevant files, commit SHAs, or author info if available.

3. Invoke `record_fact`:
```json
{
  "summary": "JWT expiration reduced to 15 minutes in auth_service.py",
  "raw_content": "Updated ACCESS_TOKEN_EXPIRE_MINUTES = 15 to enforce stricter token turnover.",
  "source_type": "chat_turn",
  "provenance_metadata": {"path": "auth_service.py"}
}
```
