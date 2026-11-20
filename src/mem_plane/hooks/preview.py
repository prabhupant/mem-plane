"""Preview builder for shortened tool outputs (Section 9.2)."""

from __future__ import annotations

import re
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from mem_plane.config import Settings

SIGNAL_PATTERN = re.compile(
    r"error|fail|failed|failure|exception|traceback|panic|fatal|assert|warning|denied|"
    r"not found|undefined|cannot|segmentation|exit code|E\d{3,}|✗|✘",
    re.IGNORECASE,
)


def _dedup_key(line: str) -> str:
    """Normalize line by stripping digits and whitespace for deduplication."""
    return re.sub(r"[\d\s]+", "", line).lower()


def build_preview(
    text: str,
    trace_id: str,
    stream: str = "stdout",
    exit_code: int | None = None,
    settings: Settings | None = None,
) -> str:
    """Build a signal-preserving preview of a large tool output (Section 9.2)."""
    head_limit = settings.preview_head_lines if settings else 15
    tail_limit = settings.preview_tail_lines if settings else 25
    signal_limit = settings.preview_signal_lines if settings else 30
    max_chars = settings.preview_max_chars if settings else 4000

    all_lines = text.splitlines()
    total_lines = len(all_lines)
    total_chars = len(text)

    exit_str = f" Exit code {exit_code}." if exit_code is not None else ""
    banner = (
        f"[mem-plane: output shortened. {stream} had {total_lines:,} lines ({total_chars:,} chars), "
        f"stored as {trace_id}.{exit_str}\n"
        f'Search it with get_memory("{trace_id}", grep="pattern") or read a range with lines="400-460".]'
    )

    # If already small, return banner + full text
    if total_lines <= (head_limit + tail_limit):
        lines_formatted = [f"  {i + 1:5d} | {l[:300]}" for i, l in enumerate(all_lines)]
        return f"{banner}\n" + "\n".join(lines_formatted)

    # 1. Head
    head_part = [
        f"  {i + 1:5d} | {all_lines[i][:300]}" for i in range(min(head_limit, total_lines))
    ]

    # 3. Tail
    tail_start_idx = max(head_limit, total_lines - tail_limit)
    tail_part = [f"  {i + 1:5d} | {all_lines[i][:300]}" for i in range(tail_start_idx, total_lines)]

    # 2. Middle signal lines
    middle_start_1based = head_limit + 1
    middle_end_1based = tail_start_idx  # 1-based inclusive end of middle
    signal_part: list[str] = []
    seen_signals: set[str] = set()

    for i in range(head_limit, tail_start_idx):
        line = all_lines[i]
        if SIGNAL_PATTERN.search(line):
            key = _dedup_key(line)
            if key not in seen_signals:
                seen_signals.add(key)
                signal_part.append(f"  {i + 1:5d} | {line[:300]}")
                if len(signal_part) >= signal_limit:
                    break

    def _assemble(curr_signals: list[str], curr_tail: list[str], curr_head: list[str]) -> str:
        sections = [banner]
        if curr_head:
            sections.append("\n".join(curr_head))

        if curr_signals:
            sections.append(
                f"... [lines {middle_start_1based}-{middle_end_1based} omitted, "
                f"{len(curr_signals)} signal lines shown below] ..."
            )
            sections.append("\n".join(curr_signals))
            sections.append("... [omitted lines continue below] ...")
        else:
            sections.append(f"... [lines {middle_start_1based}-{middle_end_1based} omitted] ...")

        if curr_tail:
            sections.append("\n".join(curr_tail))
        return "\n".join(sections)

    # Reduction order: signal lines first, then tail, then head
    out = _assemble(signal_part, tail_part, head_part)

    while len(out) > max_chars and signal_part:
        signal_part.pop()
        out = _assemble(signal_part, tail_part, head_part)

    while len(out) > max_chars and len(tail_part) > 5:
        tail_part.pop(0)
        out = _assemble(signal_part, tail_part, head_part)

    while len(out) > max_chars and len(head_part) > 5:
        head_part.pop()
        out = _assemble(signal_part, tail_part, head_part)

    if len(out) > max_chars:
        out = out[:max_chars]

    return out
