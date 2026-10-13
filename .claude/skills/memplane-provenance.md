---
name: memplane-provenance
description: Trace the origin, commit history, and source conversation behind a stored fact.
---

# Fact Provenance Skill

When asked "Why do you know that?", "Who decided this?", or verifying architectural constraints:
1. Extract candidate `fact_id` from memory.
2. Call `inspect_fact_provenance(fact_id="...")`.
3. Report the author, commit SHA, timestamp, and originating dialogue turn to the user.
