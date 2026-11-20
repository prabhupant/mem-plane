"""Example custom agent loop using mem-plane SDK prompt zones and in-process tools (Section 13, M7.5).

Demonstrates:
1. Building Zone 1 from pinned project facts with deterministic sorting.
2. Setting Anthropic prompt-caching headers (cache_control: ephemeral) on the Zone 1 block.
3. Providing the standardized 7 tools called directly in-process without network latency.
4. Using EphemeralToolPruner on tool execution outputs before adding them to Zone 4 dialogue turns.
"""

from __future__ import annotations

import asyncio
import os
from typing import Any

from mem_plane.config import Settings
from mem_plane.core.models import HydrationBundle
from mem_plane.project import resolve_project
from mem_plane.sdk import EphemeralToolPruner
from mem_plane.sdk.prompt_zones import build_zone_1
from mem_plane.server.service import MemPlaneService
from mem_plane.server.tool_text import TOOL_DEFINITIONS
from mem_plane.storage.sqlite.store import SQLiteStore


def build_anthropic_system_prompt(
    base_instructions: str,
    store: SQLiteStore,
    project_id: str,
) -> list[dict[str, Any]]:
    """Build byte-stable Zone 1 system prompt block with Anthropic cache control (Section 13)."""
    # 1. Fetch pinned facts
    pinned_facts = store.list_pinned(project_id)

    # 2. Assemble hydration bundle with tenant rules and deterministic ordering
    rules = [f"{f.kind.upper()}: {f.summary}" for f in pinned_facts]
    bundle = HydrationBundle(
        tenant_rules=rules,
        session_state={"project_id": project_id},
    )

    # 3. Build Zone 1 text with sort_keys=True JSON formatting
    zone_1_text = build_zone_1(base_instructions, bundle)

    # 4. Return as cached block with Anthropic prompt-caching flag
    return [
        {
            "type": "text",
            "text": zone_1_text,
            "cache_control": {"type": "ephemeral"},
        }
    ]


def convert_tool_schemas_for_anthropic() -> list[dict[str, Any]]:
    """Convert mem-plane tool definitions into Anthropic tool schema format."""
    anthropic_tools = []
    for tool_spec in TOOL_DEFINITIONS:
        params = tool_spec.get("parameters", {})
        if isinstance(params, dict):
            properties = {
                param_name: {"type": "string", "description": str(param_desc)}
                for param_name, param_desc in params.items()
            }
        else:
            properties = {}

        anthropic_tools.append(
            {
                "name": tool_spec["name"],
                "description": tool_spec["description"],
                "input_schema": {
                    "type": "object",
                    "properties": properties,
                },
            }
        )
    return anthropic_tools


async def dispatch_in_process_tool(
    service: MemPlaneService,
    name: str,
    args: dict[str, Any],
) -> str:
    """Execute a mem-plane tool call in-process with zero network overhead."""
    if name == "search_memory":
        return await service.search_memory(
            query=args["query"],
            top_k=args.get("top_k", 5),
            include_episodes=args.get("include_episodes", True),
        )
    elif name == "get_memory":
        return await service.get_memory(
            ref=args["ref"],
            grep=args.get("grep"),
            lines=args.get("lines"),
            max_chars=args.get("max_chars", 6000),
        )
    elif name == "record_fact":
        return await service.record_fact(
            summary=args["summary"],
            body=args["body"],
            kind=args["kind"],
            paths=args.get("paths"),
            supersedes=args.get("supersedes"),
            pin=args.get("pin", False),
        )
    elif name == "update_memory":
        return await service.update_memory(
            fact_id=args["fact_id"],
            summary=args.get("summary"),
            body=args.get("body"),
            kind=args.get("kind"),
        )
    elif name == "forget_memory":
        return await service.forget_memory(
            fact_id=args["fact_id"],
            reason=args.get("reason", "Obsolete"),
        )
    elif name == "pin_memory":
        return await service.pin_memory(
            fact_id=args["fact_id"],
            pinned=args["pinned"],
        )
    elif name == "memory_status":
        return await service.memory_status()
    else:
        return f"Unknown tool: {name}"


async def run_demo() -> None:
    print("=== mem-plane Custom Agent Loop Demo ===")

    # 1. Initialize local SQLite storage
    settings = Settings(embedding_provider="none")  # Fast demo without embeddings
    store = SQLiteStore(
        database=settings.db_path,
        dimension=settings.embedding_dimension,
        settings=settings,
    )

    try:
        # 2. Resolve project identity
        project = resolve_project(os.getcwd())
        store.upsert_project(project)
        print(f"Active Project: {project.display_name} ({project.project_id})")

        # 3. Initialize in-process service and pruner
        service = MemPlaneService(store, project.project_id, embedding_client=None)
        pruner = EphemeralToolPruner(max_lines=15, max_chars=600)

        # 4. Construct Zone 1 cached system blocks
        base_system_prompt = (
            "You are an expert autonomous software engineer. "
            "Consult project memories and follow established architectural rules."
        )
        system_blocks = build_anthropic_system_prompt(
            base_instructions=base_system_prompt,
            store=store,
            project_id=project.project_id,
        )
        print("\n--- Zone 1 System Block (Cache Control Ephemeral) ---")
        print(system_blocks[0]["text"][:300] + "\n...")

        # 5. In-process tool schemas
        tools = convert_tool_schemas_for_anthropic()
        print(f"\nRegistered {len(tools)} in-process tools matching MCP service:")
        for t in tools:
            print(f"  - {t['name']}")

        # 6. Simulate in-process tool call (e.g. search_memory)
        print("\n--- In-Process Tool Execution ---")
        search_result = await dispatch_in_process_tool(
            service=service,
            name="search_memory",
            args={"query": "database convention"},
        )
        print(f"Search Result:\n{search_result}")

        # 7. Simulate EphemeralToolPruner compressing a verbose command
        print("\n--- Ephemeral Tool Pruning in Zone 4 ---")
        simulated_large_output = "\n".join(
            [f"test_cases/test_orders.py:{i}: PASSED" for i in range(1, 100)]
            + ["FAILED: test_cases/test_payment.py:12 - AssertionError: balance mismatch"]
        )
        preview, trace_id = pruner.prune(simulated_large_output)
        if trace_id:
            print(f"Pruned verbose output (Trace ID: {trace_id}):")
            print(preview)
        else:
            print(preview)

        print("\n=== Custom loop demo completed successfully! ===")
    finally:
        store.close()


if __name__ == "__main__":
    asyncio.run(run_demo())
