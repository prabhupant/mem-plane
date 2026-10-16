---
name: memplane-trace
description: Inspect full error traces or bash logs automatically compressed by mem-plane's pruner.
---

# Pruned Trace Inspection Skill

When tool output displays:
`[TOOL OUTPUT TRUNCATED: ... Trace ID: <trace_id>. Call dereference_raw_trace('<trace_id>') for full trace.]`

1. Examine the compressed preview first; root causes are often visible in the head/tail preview.
2. If and only if the full trace is needed, call `dereference_raw_trace(trace_id="<trace_id>")`.
3. Do NOT re-execute heavy build or test commands solely to re-read output logs.
