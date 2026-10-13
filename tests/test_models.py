"""Unit tests for core models."""

from mem_plane.core.models import FactEntity, HydrationBundle, PromptZone, SurrogateHit


def test_prompt_zone_enum() -> None:
    assert PromptZone.ZONE_1_STATIC.value == "ZONE_1_STATIC"
    assert PromptZone.ZONE_2_BREAKPOINT.value == "ZONE_2_BREAKPOINT"
    assert PromptZone.ZONE_3_SURROGATES.value == "ZONE_3_SURROGATES"
    assert PromptZone.ZONE_4_ACTIVE_TURNS.value == "ZONE_4_ACTIVE_TURNS"


def test_fact_entity_defaults() -> None:
    fact = FactEntity(
        fact_id="fact-123",
        tenant_id="tenant-abc",
        summary="Test summary",
        raw_content="Test raw content",
        source_type="codebase",
    )
    assert fact.fact_id == "fact-123"
    assert fact.tenant_id == "tenant-abc"
    assert fact.session_id is None
    assert fact.summary == "Test summary"
    assert fact.raw_content == "Test raw content"
    assert fact.source_type == "codebase"
    assert fact.provenance_metadata == {}
    assert fact.created_at is not None


def test_surrogate_hit_model() -> None:
    hit = SurrogateHit(
        fact_id="fact-456",
        summary="A compact surrogate summary",
        score=0.032,
        source_path="src/main.py",
    )
    assert hit.fact_id == "fact-456"
    assert hit.summary == "A compact surrogate summary"
    assert hit.score == 0.032
    assert hit.source_path == "src/main.py"
    assert hit.token_estimate == 30


def test_hydration_bundle_defaults() -> None:
    bundle = HydrationBundle()
    assert bundle.tenant_rules == []
    assert bundle.user_preferences == {}
    assert bundle.session_state == {}
