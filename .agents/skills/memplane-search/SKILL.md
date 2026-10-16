---
name: memplane-search
description: Search codebase documentation, past design decisions, and episodic memory using two-stage progressive disclosure.
---

# Two-Stage Memory Search Skill

Whenever you need to recall past architecture decisions, bug fixes, or documentation:

1. Stage 1: Query Surrogates First
Do NOT dump entire files. Invoke `search_episodic_memory`:
```json
{"query": "How is authentication handled in the worker pool?", "top_k": 3}
```
2. Stage 2: Selective Dereferencing ($O(1)$)
Inspect the returned surrogates (~30 tokens each):
- If the surrogate summary answers the question, STOP immediately. Do not pull additional tokens.
- If and ONLY if you require raw code or complete implementation text, invoke `inspect_fact_provenance(fact_id="...")`.
