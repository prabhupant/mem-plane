"""Context governor and KV-cache prefix invariance manager (Section 13).

Optional helper for developers building their own agent loop with the Anthropic or OpenAI APIs.
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from typing import Final

from mem_plane.core.models import HydrationBundle, SurrogateHit

CACHE_BREAKPOINT: Final[str] = "\n<!-- MEM_PLANE_CACHE_BREAKPOINT -->\n"


def build_zone_1(static_system_prompt: str, hydration: HydrationBundle) -> str:
    """Build Zone 1: Static system prompt + deterministically formatted hydration data.

    Must remain 100% byte-identical across dialogue turns when tenant rules
    and sticky state do not change.
    """
    sections: list[str] = [static_system_prompt.strip()]

    has_rules = bool(hydration.tenant_rules)
    has_prefs = bool(hydration.user_preferences)
    has_state = bool(hydration.session_state)

    if has_rules or has_prefs or has_state:
        sections.append("### Context Hydration")

        if has_rules:
            rules_section = "Tenant Rules:\n" + "\n".join(
                f"- {rule.strip()}" for rule in hydration.tenant_rules
            )
            sections.append(rules_section)

        if has_prefs:
            prefs_json = json.dumps(hydration.user_preferences, sort_keys=True, indent=2)
            sections.append(f"User Preferences:\n```json\n{prefs_json}\n```")

        if has_state:
            state_json = json.dumps(hydration.session_state, sort_keys=True, indent=2)
            sections.append(f"Session State:\n```json\n{state_json}\n```")

    return "\n\n".join(sections)


def build_zone_2() -> str:
    """Build Zone 2: Cache delimiter breakpoint."""
    return CACHE_BREAKPOINT


def build_zone_3(surrogates: Sequence[SurrogateHit]) -> str:
    """Build Zone 3: Deterministically sorted memory surrogates (~30 tokens each).

    Surrogates are sorted alphabetically by fact_id to ensure that any identical
    subset of retrieved facts produces byte-identical prompt tokens.
    """
    if not surrogates:
        return "### Retrieved Memories\nNo relevant memories."

    # Sort deterministically by fact_id ascending
    sorted_surrogates = sorted(surrogates, key=lambda s: s.fact_id)

    lines: list[str] = ["### Retrieved Memories"]
    for s in sorted_surrogates:
        source_suffix = f" (source: {s.source_path})" if s.source_path else ""
        lines.append(f"- [{s.fact_id}] {s.summary}{source_suffix}")

    return "\n".join(lines)


def build_zone_4(turns: Sequence[str]) -> str:
    """Build Zone 4: Active dialogue turns (user messages, responses, pruned outputs)."""
    if not turns:
        return ""
    return "\n\n".join(t.strip() for t in turns if t.strip())


def assemble_prompt(
    static_system_prompt: str,
    hydration: HydrationBundle,
    surrogates: Sequence[SurrogateHit],
    turns: Sequence[str],
) -> str:
    """Assemble all 4 zones into a single prompt string enforcing cache prefix invariance."""
    zone_1 = build_zone_1(static_system_prompt, hydration)
    zone_2 = build_zone_2()
    zone_3 = build_zone_3(surrogates)
    zone_4 = build_zone_4(turns)

    if zone_4:
        return f"{zone_1}{zone_2}{zone_3}\n\n{zone_4}"
    return f"{zone_1}{zone_2}{zone_3}"


def extract_zone_1_prefix(assembled_prompt: str) -> str:
    """Extract Zone 1 prefix up to the cache breakpoint delimiter."""
    if CACHE_BREAKPOINT in assembled_prompt:
        return assembled_prompt.split(CACHE_BREAKPOINT)[0]
    return assembled_prompt


class PromptZoneGovernor:
    """Convenience class for managing prompt zones and cache breakpoints."""

    def build_zone_1(self, static_system_prompt: str, hydration: HydrationBundle) -> str:
        return build_zone_1(static_system_prompt, hydration)

    def build_zone_2(self) -> str:
        return build_zone_2()

    def build_zone_3(self, hits: Sequence[SurrogateHit]) -> str:
        return build_zone_3(hits)

    def build_zone_4(self, turns: Sequence[str]) -> str:
        return build_zone_4(turns)

    def assemble(
        self,
        static_system_prompt: str,
        hydration: HydrationBundle,
        surrogates: Sequence[SurrogateHit],
        turns: Sequence[str],
    ) -> str:
        return assemble_prompt(static_system_prompt, hydration, surrogates, turns)
