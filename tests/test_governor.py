"""Unit tests for Context Governor and KV-cache prefix invariance."""

from mem_plane.core.governor import (
    CACHE_BREAKPOINT,
    assemble_prompt,
    build_zone_1,
    build_zone_3,
    extract_zone_1_prefix,
)
from mem_plane.core.models import HydrationBundle, SurrogateHit


def test_zone_1_prefix_byte_invariance_when_downstream_changes() -> None:
    """Validate that Zone 1 prefix is 100% byte-identical across dialogue turns and retrieved memories."""
    static_system_prompt = (
        "You are a senior software engineering assistant for the mem-plane system."
    )
    hydration = HydrationBundle(
        tenant_rules=["Enforce PEP 561 compliance", "Run pytest before committing"],
        user_preferences={"verbosity": "concise", "editor": "neovim"},
        session_state={"current_stage": 3},
    )

    surrogates_turn_1 = [
        SurrogateHit(fact_id="fact-100", summary="First memory", score=0.03),
        SurrogateHit(fact_id="fact-200", summary="Second memory", score=0.02),
    ]
    turns_turn_1 = ["User: What is Stage 3?", "Assistant: Stage 3 is Context Governor."]

    surrogates_turn_2 = [
        SurrogateHit(fact_id="fact-300", summary="Third memory", score=0.035),
        SurrogateHit(fact_id="fact-400", summary="Fourth memory", score=0.015),
    ]
    turns_turn_2 = [
        "User: What is Stage 3?",
        "Assistant: Stage 3 is Context Governor.",
        "User: How does caching work?",
        "Assistant: By keeping prefix tokens immutable.",
    ]

    prompt_1 = assemble_prompt(static_system_prompt, hydration, surrogates_turn_1, turns_turn_1)
    prompt_2 = assemble_prompt(static_system_prompt, hydration, surrogates_turn_2, turns_turn_2)

    prefix_1 = extract_zone_1_prefix(prompt_1)
    prefix_2 = extract_zone_1_prefix(prompt_2)

    # Zone 1 prefix MUST be 100% byte-identical
    assert prefix_1 == prefix_2
    assert prefix_1.encode("utf-8") == prefix_2.encode("utf-8")
    assert CACHE_BREAKPOINT in prompt_1
    assert CACHE_BREAKPOINT in prompt_2


def test_zone_3_deterministic_sorting() -> None:
    """Validate that surrogates are sorted by fact_id to ensure token invariance regardless of query order."""
    hit_a = SurrogateHit(fact_id="fact-alpha", summary="Alpha summary", score=0.01)
    hit_b = SurrogateHit(fact_id="fact-beta", summary="Beta summary", score=0.09)
    hit_g = SurrogateHit(fact_id="fact-gamma", summary="Gamma summary", score=0.05)

    # Order 1: b, a, g
    zone_3_first = build_zone_3([hit_b, hit_a, hit_g])
    # Order 2: g, b, a
    zone_3_second = build_zone_3([hit_g, hit_b, hit_a])

    assert zone_3_first == zone_3_second
    # Check alphabetical ordering in rendered string
    alpha_idx = zone_3_first.index("fact-alpha")
    beta_idx = zone_3_first.index("fact-beta")
    gamma_idx = zone_3_first.index("fact-gamma")
    assert alpha_idx < beta_idx < gamma_idx


def test_hydration_empty_bundle() -> None:
    """Empty hydration bundle should cleanly render only the static system prompt."""
    static_prompt = "You are an agent."
    zone_1 = build_zone_1(static_prompt, HydrationBundle())
    assert zone_1 == "You are an agent."


def test_prompt_assembly_structure() -> None:
    """Verify that assembled prompt contains all four zones and correct delimiter."""
    static_prompt = "System base"
    hydration = HydrationBundle(tenant_rules=["Rule 1"])
    surrogates = [SurrogateHit(fact_id="f1", summary="Sum 1", score=0.1)]
    turns = ["User: Hello"]

    assembled = assemble_prompt(static_prompt, hydration, surrogates, turns)

    assert "System base" in assembled
    assert "Rule 1" in assembled
    assert CACHE_BREAKPOINT in assembled
    assert "[f1] Sum 1" in assembled
    assert "User: Hello" in assembled
