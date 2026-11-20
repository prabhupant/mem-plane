"""Optional fact extractor for closed transcript segments (Section 9.6, M7.3)."""

from __future__ import annotations

import json
import os
import re
import uuid
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from mem_plane.core.models import Fact, FactKind, FactPath

if TYPE_CHECKING:
    from mem_plane.config import Settings
    from mem_plane.storage.sqlite.store import SQLiteStore

EXTRACTOR_SYSTEM_PROMPT = """You are an expert software engineering memory extractor for AI coding sessions.
Analyze the provided transcript segment and extract durable, high-value project facts.
Extract at most 8 durable facts.

Only extract facts matching these kinds:
- 'decision': Architectural, design, or library selection decisions.
- 'rule': Invariant constraints, hard requirements, or non-obvious business rules.
- 'bugfix': Root causes of non-trivial bugs and their specific resolutions.
- 'convention': Team patterns, styling conventions, testing practices, or CLI commands.

Do NOT extract:
- Temporary status updates ("fixed typo in line 14", "running make test")
- Generic programming trivia or common standard library knowledge
- Casual conversational chatter or greetings

Output ONLY a JSON array of objects with this exact structure:
[
  {
    "kind": "decision" | "rule" | "bugfix" | "convention",
    "summary": "Short single-sentence summary (<= 250 characters)",
    "body": "Detailed explanation with context and technical specifics",
    "paths": ["path/to/relevant/file.py"]
  }
]
No extra markdown formatting, explanations, or commentary outside the JSON array."""


def parse_extracted_facts(raw_text: str) -> list[dict[str, Any]]:
    """Parse and validate JSON array of facts from LLM response."""
    text = raw_text.strip()
    if not text:
        return []

    # Strip markdown code blocks if wrapped in ```json ... ``` or ``` ... ```
    match = re.search(r"```(?:json)?\s*(\[\s*\{.*?\}\s*\])\s*```", text, re.DOTALL)
    if match:
        text = match.group(1).strip()
    elif text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text)
        text = re.sub(r"\s*```$", "", text)

    try:
        parsed = json.loads(text)
    except (json.JSONDecodeError, ValueError):
        return []

    if not isinstance(parsed, list):
        return []

    valid_facts: list[dict[str, Any]] = []
    allowed_kinds = {k.value for k in FactKind}

    for item in parsed:
        if not isinstance(item, dict):
            continue

        kind_str = str(item.get("kind", "")).lower()
        if kind_str not in allowed_kinds:
            continue

        summary = str(item.get("summary", "")).strip()
        body = str(item.get("body", "")).strip()
        if not summary or not body:
            continue

        paths_raw = item.get("paths", [])
        paths: list[str] = []
        if isinstance(paths_raw, list):
            paths = [str(p).strip() for p in paths_raw if isinstance(p, str)]

        valid_facts.append(
            {
                "kind": kind_str,
                "summary": summary[:300],
                "body": body,
                "paths": paths,
            }
        )

    return valid_facts[:8]


class FactExtractor:
    """Extracts durable facts from closed transcript segments."""

    def __init__(self, settings: Settings, store: SQLiteStore) -> None:
        self.settings = settings
        self.store = store

    def _call_model(self, text: str, client: Any | None = None) -> str:
        """Call Anthropic model or mock client."""
        if client is not None:
            # Test or injected client
            res = client.messages.create(
                model=self.settings.extractor_model,
                max_tokens=2048,
                system=EXTRACTOR_SYSTEM_PROMPT,
                messages=[{"role": "user", "content": text}],
            )
            # Support both object response and string
            if hasattr(res, "content") and isinstance(res.content, list):
                return str(res.content[0].text)
            return str(res)

        # Real Anthropic client (only imported when enabled and needed)
        try:
            import anthropic  # type: ignore[import-not-found]
        except ImportError as e:
            raise RuntimeError(
                "Anthropic package required for fact extraction: pip install anthropic"
            ) from e

        api_key = self.settings.anthropic_api_key or os.environ.get("ANTHROPIC_API_KEY")
        if not api_key:
            raise ValueError("ANTHROPIC_API_KEY is required when MEM_PLANE_EXTRACTOR=anthropic")

        real_client = anthropic.Anthropic(api_key=api_key)
        response = real_client.messages.create(
            model=self.settings.extractor_model,
            max_tokens=2048,
            system=EXTRACTOR_SYSTEM_PROMPT,
            messages=[{"role": "user", "content": text}],
        )
        return str(response.content[0].text)

    def extract_from_text(
        self,
        project_id: str,
        session_id: str | None,
        text: str,
        client: Any | None = None,
    ) -> list[Fact]:
        """Extract facts from dialogue text, returning Fact instances with source='extractor'."""
        if not text.strip():
            return []

        raw_response = self._call_model(text, client=client)
        parsed_items = parse_extracted_facts(raw_response)

        now = datetime.now(UTC)
        facts: list[Fact] = []

        for item in parsed_items:
            fact_id = "f_" + uuid.uuid4().hex[:12]
            fact_paths = [FactPath(rel_path=p) for p in item.get("paths", [])]
            fact = Fact(
                fact_id=fact_id,
                project_id=project_id,
                session_id=session_id,
                kind=item["kind"],
                summary=item["summary"],
                body=item["body"],
                paths=fact_paths,
                pinned=False,  # Section 9.6: Never pin extracted facts automatically
                source="extractor",  # Section 9.6: source='extractor'
                created_at=now,
                updated_at=now,
            )
            facts.append(fact)

        return facts

    def process_segment(
        self,
        session_id: str,
        segment: int,
        client: Any | None = None,
    ) -> list[Fact]:
        """Read chunks from a closed segment, run extraction, and store facts."""
        cur = self.store.conn.execute(
            """
            SELECT project_id, text
            FROM episode_chunks
            WHERE session_id = ? AND segment = ?
            ORDER BY seq ASC;
            """,
            (session_id, segment),
        )
        rows = cur.fetchall()
        if not rows:
            return []

        project_id = str(rows[0][0])
        combined_text = "\n\n".join(str(r[1]) for r in rows if r[1])
        if len(combined_text) < 100:
            return []

        extracted = self.extract_from_text(
            project_id=project_id,
            session_id=session_id,
            text=combined_text,
            client=client,
        )

        created: list[Fact] = []
        for fact in extracted:
            # Normal create_fact handles dedup + supersede
            res = self.store.create_fact(fact)
            if res.status == "created":
                created.append(fact)

        return created
