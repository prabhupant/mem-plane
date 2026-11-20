"""Stability regression test for MCP tool definitions and schemas (Sections 8.2, 15.1).

Prompt caching in Claude Code relies on byte-level invariance of system prompt
and tool definitions. If this test fails, a tool description or parameter was changed.
Only update EXPECTED_TOOL_TEXT_HASH after intentional, reviewed changes.
"""

from __future__ import annotations

import hashlib

from mem_plane.server.tool_text import serialize_tool_definitions

# Expected sha256 hash of serialized tool definitions
EXPECTED_TOOL_TEXT_HASH = "01d23af94154f76d8cfb3048fd7525a83c47e1e596177054608c45efe87c1848"


def test_tool_text_prompt_cache_stability() -> None:
    serialized = serialize_tool_definitions()
    computed_hash = hashlib.sha256(serialized.encode("utf-8")).hexdigest()

    assert computed_hash == EXPECTED_TOOL_TEXT_HASH, (
        f"Tool text definitions changed! Computed hash: {computed_hash}, "
        f"expected: {EXPECTED_TOOL_TEXT_HASH}. "
        "Any change alters Claude Code prompt cache prefix. Update deliberately."
    )
