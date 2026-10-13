"""Ephemeral tool pruner for compressing noisy execution and test outputs."""

import uuid

from mem_plane.storage.protocols import MetadataStorage


class EphemeralToolPruner:
    """Intercepts and compresses noisy tool returns (build dumps, stack traces).

    When outputs exceed threshold limits, raw text is persisted out-of-band
    to MetadataStorage and replaced in the context window with a high-signal
    head/tail preview and trace_id reference.
    """

    def __init__(
        self,
        storage: MetadataStorage | None = None,
        max_lines: int = 15,
        max_chars: int = 600,
        head_lines: int = 5,
        tail_lines: int = 5,
    ) -> None:
        self.storage = storage
        self.max_lines = max_lines
        self.max_chars = max_chars
        self.head_lines = head_lines
        self.tail_lines = tail_lines

    def should_prune(self, text: str) -> bool:
        """Determine whether tool output exceeds line or character thresholds."""
        lines = text.splitlines()
        return len(lines) > self.max_lines or len(text) > self.max_chars

    def format_preview(self, raw_output: str, trace_id: str) -> str:
        """Format compressed preview containing head lines, callout banner, and tail lines."""
        lines = raw_output.splitlines()
        total_lines = len(lines)

        first_slice = lines[: self.head_lines]
        last_slice = lines[-self.tail_lines :] if total_lines >= self.tail_lines else []

        banner = (
            f"[TOOL OUTPUT TRUNCATED: {total_lines} lines. "
            f"Trace ID: {trace_id}. "
            f"Call dereference_raw_trace('{trace_id}') for full trace.]"
        )

        head_text = "\n".join(first_slice)
        tail_text = "\n".join(last_slice)

        return f"{banner}\n{head_text}\n... [truncated] ...\n{tail_text}"

    async def process_tool_output(
        self, raw_output: str, trace_id: str | None = None
    ) -> tuple[str, str | None]:
        """Compress output if exceeding thresholds and persist full trace out-of-band.

        Returns:
            Tuple of (processed_text, trace_id_or_none)
        """
        if not self.should_prune(raw_output):
            return raw_output, None

        assigned_trace_id = trace_id or str(uuid.uuid4())

        if self.storage is not None:
            await self.storage.save_tool_trace(assigned_trace_id, raw_output)

        preview = self.format_preview(raw_output, assigned_trace_id)
        return preview, assigned_trace_id
