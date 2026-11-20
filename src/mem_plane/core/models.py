"""Core data models for mem-plane: Facts, SearchHits, and TraceRecords."""

from __future__ import annotations

from datetime import UTC, datetime
from enum import Enum, StrEnum
from typing import Any, Literal

from pydantic import BaseModel, Field


class FactKind(StrEnum):
    DECISION = "decision"
    RULE = "rule"
    BUGFIX = "bugfix"
    CONVENTION = "convention"
    CONTEXT = "context"
    NOTE = "note"


class FactStatus(StrEnum):
    ACTIVE = "active"
    SUPERSEDED = "superseded"
    DELETED = "deleted"


class FactPath(BaseModel):
    rel_path: str
    content_sha256: str | None = None
    git_commit: str | None = None


class Fact(BaseModel):
    fact_id: str
    project_id: str
    session_id: str | None = None
    kind: FactKind
    summary: str = Field(max_length=300)
    body: str
    status: FactStatus = FactStatus.ACTIVE
    supersedes: str | None = None
    superseded_by: str | None = None
    pinned: bool = False
    source: Literal["agent", "extractor", "user"] = "agent"
    paths: list[FactPath] = Field(default_factory=list)
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class SearchHit(BaseModel):
    ref: str  # fact_id, chunk_id, or trace_id
    ref_type: Literal["fact", "episode"]
    summary: str  # fact summary, or first 200 chars of the chunk
    score: float
    kind: str | None = None  # fact kind, or None for episodes
    stale: bool = False
    stale_reason: str | None = None
    pinned: bool = False
    created_at: datetime


class TraceRecord(BaseModel):
    trace_id: str
    project_id: str
    session_id: str | None = None
    tool_name: str
    command: str | None = None
    stdout: str
    stderr: str
    exit_code: int | None = None
    line_count: int
    char_count: int
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


# ---------------------------------------------------------------------------
# Deprecated shims and backward compatibility aliases (Section 5.4)
# ---------------------------------------------------------------------------


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
