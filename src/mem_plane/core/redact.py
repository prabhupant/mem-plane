"""Secret redaction for traces, transcripts, and facts (Section 17.2)."""

from __future__ import annotations

import re

# PEM private key blocks: from -----BEGIN [A-Z ]*PRIVATE KEY----- to the matching END line
PEM_PATTERN = re.compile(
    r"-----BEGIN [A-Z ]*PRIVATE KEY-----[\s\S]*?-----END [A-Z ]*PRIVATE KEY-----"
)

# Anthropic keys: sk-ant-[A-Za-z0-9_\-]{20,} (must precede OpenAI key pattern)
ANTHROPIC_PATTERN = re.compile(r"\bsk-ant-[A-Za-z0-9_\-]{20,}\b")

# OpenAI-style keys: sk-[A-Za-z0-9_-]{20,}
OPENAI_PATTERN = re.compile(r"\bsk-[A-Za-z0-9_-]{20,}\b")

# AWS access key IDs: AKIA[0-9A-Z]{16}
AWS_PATTERN = re.compile(r"\bAKIA[0-9A-Z]{16}\b")

# GitHub tokens: gh[pousr]_[A-Za-z0-9]{36,}
GITHUB_PATTERN = re.compile(r"\bgh[pousr]_[A-Za-z0-9]{36,}\b")

# Slack tokens: xox[abpors]-[A-Za-z0-9-]{10,}
SLACK_PATTERN = re.compile(r"\bxox[abpors]-[A-Za-z0-9-]{10,}\b")

# JWTs: eyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}
JWT_PATTERN = re.compile(r"\beyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\b")

# Assignments: (?i)\b(password|passwd|secret|token|api[_-]?key)\b\s*[=:]\s*['"]?[^\s'"]{8,}
ASSIGNMENT_PATTERN = re.compile(
    r"(?i)\b(password|passwd|secret|token|api[_-]?key)\b(\s*[=:]\s*['\"]?)(?!\[REDACTED:)[^\s'\"<>\n\r]{8,}(['\"]?)"
)


def redact_secrets(text: str, enabled: bool = True) -> str:
    """Redact known secret patterns with [REDACTED:<type>].

    Never throws. If enabled is False or text is empty, returns text unchanged.
    """
    if not enabled or not text:
        return text

    try:
        # 1. PEM private keys
        text = PEM_PATTERN.sub("[REDACTED:private_key]", text)

        # 2. Specific API tokens before generic ones
        text = ANTHROPIC_PATTERN.sub("[REDACTED:anthropic_key]", text)
        text = OPENAI_PATTERN.sub("[REDACTED:openai_key]", text)
        text = AWS_PATTERN.sub("[REDACTED:aws_key]", text)
        text = GITHUB_PATTERN.sub("[REDACTED:github_token]", text)
        text = SLACK_PATTERN.sub("[REDACTED:slack_token]", text)
        text = JWT_PATTERN.sub("[REDACTED:jwt]", text)

        # 3. Generic assignments (password=..., api_key: ..., etc.)
        text = ASSIGNMENT_PATTERN.sub(r"\g<1>\g<2>[REDACTED:assignment]\g<3>", text)
    except re.error:
        # Fail safe: return original text if regex engine encounters an issue
        return text

    return text
