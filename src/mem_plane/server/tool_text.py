"""Static tool text, parameter schemas, and descriptions for mem-plane MCP tools.

Protected by tests/server/test_tool_text_stable.py to preserve prompt cache stability.
"""

from __future__ import annotations

import json

SEARCH_MEMORY_NAME = "search_memory"
SEARCH_MEMORY_DESCRIPTION = (
    "Search persistent memory (curated facts and past session episodes) using hybrid "
    "keyword and semantic search. Returns lightweight one-line summaries in score order."
)

GET_MEMORY_NAME = "get_memory"
GET_MEMORY_DESCRIPTION = (
    "Retrieve full detail for a memory reference: a fact ('f_...'), an episodic transcript chunk "
    "('e_...'), or a tool execution trace ('t_...'). Supports regex grep and line-range slicing."
)

RECORD_FACT_NAME = "record_fact"
RECORD_FACT_DESCRIPTION = (
    "Record a curated architectural decision, rule, bugfix, convention, context, or note into "
    "persistent memory. Computes content hash for deduplication and tracks associated file paths."
)

UPDATE_MEMORY_NAME = "update_memory"
UPDATE_MEMORY_DESCRIPTION = (
    "Update an existing fact's summary, body, or kind in persistent memory. Refreshes file path "
    "hashes and records an audit history entry."
)

FORGET_MEMORY_NAME = "forget_memory"
FORGET_MEMORY_DESCRIPTION = (
    "Soft-delete a fact from persistent memory with a reason. Deleted facts are excluded from "
    "all future searches and get_memory lookups."
)

PIN_MEMORY_NAME = "pin_memory"
PIN_MEMORY_DESCRIPTION = "Pin or unpin a critical fact. Pinned facts are always injected into new sessions at SessionStart."

MEMORY_STATUS_NAME = "memory_status"
MEMORY_STATUS_DESCRIPTION = (
    "Return high-level memory plane metrics: counts of active facts, pinned rules, episode chunks, "
    "and traces, vector search status, pending embeddings, and database path."
)

TOOL_DEFINITIONS: list[dict[str, object]] = [
    {
        "name": SEARCH_MEMORY_NAME,
        "description": SEARCH_MEMORY_DESCRIPTION,
        "parameters": {
            "query": "Search query string",
            "top_k": "Maximum number of results to return (default 5, max 20)",
            "include_episodes": "Whether to include raw episodic transcript chunks (default True)",
        },
    },
    {
        "name": GET_MEMORY_NAME,
        "description": GET_MEMORY_DESCRIPTION,
        "parameters": {
            "ref": "Memory reference ID starting with f_ (fact), e_ (episode), or t_ (trace)",
            "grep": "Optional case-insensitive regex to filter trace lines with 1 line of context",
            "lines": "Optional line range slice (e.g. '120-180' or '50') for traces",
            "max_chars": "Maximum characters to return for unsliced traces (default 6000)",
        },
    },
    {
        "name": RECORD_FACT_NAME,
        "description": RECORD_FACT_DESCRIPTION,
        "parameters": {
            "summary": "Concise 1-2 sentence summary of the fact",
            "body": "Full explanation, rationale, trade-offs, or reproduction steps",
            "kind": "Fact category: decision, rule, bugfix, convention, context, note",
            "paths": "List of relative file paths related to this fact",
            "supersedes": "Optional fact_id that this new fact replaces",
            "pin": "Whether to pin this fact for inclusion in every session start (default False)",
        },
    },
    {
        "name": UPDATE_MEMORY_NAME,
        "description": UPDATE_MEMORY_DESCRIPTION,
        "parameters": {
            "fact_id": "ID of the fact to update (f_...)",
            "summary": "Updated summary text, or None to keep existing",
            "body": "Updated body text, or None to keep existing",
            "kind": "Updated kind, or None to keep existing",
        },
    },
    {
        "name": FORGET_MEMORY_NAME,
        "description": FORGET_MEMORY_DESCRIPTION,
        "parameters": {
            "fact_id": "ID of the fact to forget (f_...)",
            "reason": "Explanation of why this fact is being deleted",
        },
    },
    {
        "name": PIN_MEMORY_NAME,
        "description": PIN_MEMORY_DESCRIPTION,
        "parameters": {
            "fact_id": "ID of the fact to pin or unpin (f_...)",
            "pinned": "True to pin, False to unpin",
        },
    },
    {
        "name": MEMORY_STATUS_NAME,
        "description": MEMORY_STATUS_DESCRIPTION,
        "parameters": {},
    },
]


def serialize_tool_definitions() -> str:
    """Deterministically serialize tool definitions for stability hashing."""
    return json.dumps(TOOL_DEFINITIONS, sort_keys=True)
