# Token Benchmarks

This file tracks token consumption, cache performance, and compaction metrics across sessions.
Cost-weighted input tokens are calculated using standard Anthropic pricing ratios:
- `1.0` for regular uncached input tokens
- `1.25` for cache creation (write) tokens
- `0.10` for cache read tokens

Formula:
$$\text{Cost-Weighted Input} = \text{Input} \times 1.0 + \text{Cache Writes} \times 1.25 + \text{Cache Reads} \times 0.10$$

---

## Baseline (no mem-plane)

Baseline metrics measured from real unpruned Claude Code sessions (`2.1.326`) captured during Milestone 0:

| Session | Requests | Input Tokens | Cache Writes | Cache Reads | Output Tokens | Compactions | Cost-Weighted Input |
| :--- | :---: | :---: | :---: | :---: | :---: | :---: | :---: |
| `transcript_simple` (short task) | 3 | 6 | 41,701 | 56,633 | 253 | 0 | 57,795.6 |
| `transcript_compacted` (2,500-line bash + compact) | 3 | 6 | 22,712 | 77,372 | 820 | 1 | 36,133.2 |

---

## mem-plane Enabled

*(To be benchmarked in Milestone 6 across standard 5-task suite)*
