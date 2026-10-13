# KV-Cache Economics & Invariance Rules

## Prompt Caching Mechanics

Modern frontier LLMs (Claude 3.5, OpenAI GPT-4o, vLLM) cache KV states using strict prefix hashing.
Cache discounts reduce cost and latency by 80% to 90%, but any token mutation in the prompt prefix invalidates the downstream cache.

## The 4-Zone Rule

1. **Zone 1: Static System & Pre-Token Hydration**
   - Must remain 100% byte-identical across turns.
   - Contains static system instructions and deterministically serialized tenant rules & user preferences (`sort_keys=True`).
2. **Zone 2: Cache Breakpoint Delimiter**
   - Exact static string:
     `\n<!-- MEM_PLANE_CACHE_BREAKPOINT -->\n`
   - Maps to provider cache control headers.
3. **Zone 3: Retrieved Memory Surrogates**
   - Compact surrogate list (~30 tokens each).
   - **Always sorted alphabetically by `fact_id` ascending.**
   - Guarantees that any identical subset of recalled memories produces byte-identical token sequences.
4. **Zone 4: Active Dialogue Turns**
   - Dynamic user messages, assistant turns, and pruned tool outputs.
