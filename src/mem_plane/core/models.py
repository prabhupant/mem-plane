from datetime import UTC, datetime
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field


class PromptZone(str, Enum):
    ZONE_1_STATIC = "ZONE_1_STATIC"
    ZONE_2_BREAKPOINT = "ZONE_2_BREAKPOINT"
    ZONE_3_SURROGATES = "ZONE_3_SURROGATES"
    ZONE_4_ACTIVE_TURNS = "ZONE_4_ACTIVE_TURNS"


class FactEntity(BaseModel):
    fact_id: str
    tenant_id: str
    session_id: str | None = None
    summary: str = Field(description="Compact surrogate summary (~20-40 tokens)")
    raw_content: str = Field(description="Full uncompressed body or trace text")
    source_type: str = Field(description="codebase, chat_turn, tool_output, or rule")
    provenance_metadata: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class SurrogateHit(BaseModel):
    fact_id: str
    summary: str
    score: float
    source_path: str | None = None
    token_estimate: int = 30


class HydrationBundle(BaseModel):
    tenant_rules: list[str] = Field(default_factory=list)
    user_preferences: dict[str, Any] = Field(default_factory=dict)
    session_state: dict[str, Any] = Field(default_factory=dict)
