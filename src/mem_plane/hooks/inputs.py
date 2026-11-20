"""Pydantic input models for Claude Code hook events (Section 9.1)."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class BaseHookInput(BaseModel):
    """Common fields provided in Claude Code hook payloads."""

    model_config = ConfigDict(extra="ignore")

    session_id: str
    cwd: str
    transcript_path: str | None = None
    hook_event_name: str = ""
    prompt_id: str | None = None
    permission_mode: str | None = None


class PostToolUseInput(BaseHookInput):
    """Payload for PostToolUse hook events."""

    tool_name: str
    tool_use_id: str | None = None
    tool_input: dict[str, Any] = Field(default_factory=dict)
    tool_response: Any = None
    duration_ms: float | None = None


class SessionStartInput(BaseHookInput):
    """Payload for SessionStart hook events."""

    source: str = "startup"  # startup, resume, compact, clear


class PreCompactInput(BaseHookInput):
    """Payload for PreCompact hook events."""

    trigger: str | None = None  # manual, auto


class StopInput(BaseHookInput):
    """Payload for Stop hook events."""

    last_assistant_message: str | None = None


class SessionEndInput(BaseHookInput):
    """Payload for SessionEnd hook events."""

    reason: str | None = None


class UserPromptSubmitInput(BaseHookInput):
    """Payload for UserPromptSubmit hook events."""

    prompt: str = ""
