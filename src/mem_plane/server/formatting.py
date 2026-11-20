"""Plain text result formatting for search and memory tools (Section 7.5)."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from mem_plane.core.models import SearchHit

FIXED_FOOTER = "Use get_memory(ref) for full detail."


def truncate_summary(text: str, max_chars: int = 200) -> str:
    """Truncate summary to max_chars characters with ellipsis '…' if longer."""
    cleaned = " ".join(text.split())
    if len(cleaned) > max_chars:
        return cleaned[: max_chars - 1] + "…"
    return cleaned


def format_search_results(
    hits: list[SearchHit],
    query: str,
    project_id: str,
) -> str:
    """Format search results into deterministic, token-efficient plain text (Section 7.5)."""
    count = len(hits)
    res_str = "result" if count == 1 else "results"
    lines = [f'{count} {res_str} for "{query}" (project: {project_id})']

    for hit in hits:
        summary_text = truncate_summary(hit.summary, max_chars=200)
        date_str = hit.created_at.strftime("%Y-%m-%d")

        if hit.ref_type == "fact":
            stale_str = f" [STALE: {hit.stale_reason}]" if hit.stale_reason else ""
            lines.append(f"{hit.ref} [{hit.kind}]{stale_str} {summary_text} ({date_str})")
        else:
            lines.append(f"{hit.ref} [episode] {summary_text} ({date_str})")

    lines.append(FIXED_FOOTER)
    return "\n".join(lines)
