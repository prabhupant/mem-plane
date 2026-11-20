"""Benchmark and token reporting from Claude Code transcripts."""

from __future__ import annotations

import json
import os
import sys
from collections.abc import Sequence
from dataclasses import dataclass


@dataclass
class SessionStats:
    session_id: str
    transcript_file: str
    requests: int = 0
    input_tokens: int = 0
    cache_write_tokens: int = 0
    cache_read_tokens: int = 0
    output_tokens: int = 0
    compactions: int = 0

    @property
    def weighted_input_tokens(self) -> float:
        # 1.0 for uncached input, 1.25 for cache writes, 0.1 for cache reads
        return (
            self.input_tokens * 1.0 + self.cache_write_tokens * 1.25 + self.cache_read_tokens * 0.1
        )


def analyze_transcript(transcript_path: str) -> SessionStats:
    """Read a Claude Code transcript .jsonl file and calculate token usage and compaction stats."""
    session_id = os.path.basename(transcript_path).replace(".jsonl", "")
    stats = SessionStats(session_id=session_id, transcript_file=transcript_path)

    with open(transcript_path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                entry = json.loads(line)
            except json.JSONDecodeError:
                continue

            entry_type = entry.get("type")
            subtype = entry.get("subtype")

            # Check compaction
            if entry_type == "system" and subtype == "compact_boundary":
                stats.compactions += 1

            # Check assistant message usage
            if entry_type == "assistant":
                msg = entry.get("message", {})
                usage = msg.get("usage")
                if usage:
                    stats.requests += 1
                    stats.input_tokens += int(usage.get("input_tokens", 0))
                    stats.cache_write_tokens += int(usage.get("cache_creation_input_tokens", 0))
                    stats.cache_read_tokens += int(usage.get("cache_read_input_tokens", 0))
                    stats.output_tokens += int(usage.get("output_tokens", 0))

    return stats


def run_bench(transcripts: Sequence[str]) -> int:
    """Analyze provided transcripts and print report."""
    if not transcripts:
        print("Error: No transcript files provided.", file=sys.stderr)
        return 1

    reports: list[SessionStats] = []
    for path in transcripts:
        if not os.path.exists(path):
            print(f"Warning: Transcript file not found: {path}", file=sys.stderr)
            continue
        reports.append(analyze_transcript(path))

    if not reports:
        print("No valid transcripts analyzed.", file=sys.stderr)
        return 1

    print("=" * 80)
    print("MEM-PLANE TOKEN BENCHMARK REPORT")
    print("=" * 80)
    for stat in reports:
        print(f"Session: {stat.session_id}")
        print(f"  Transcript:   {stat.transcript_file}")
        print(f"  Requests:     {stat.requests}")
        print(f"  Input Tokens: {stat.input_tokens:,}")
        print(f"  Cache Writes: {stat.cache_write_tokens:,}")
        print(f"  Cache Reads:  {stat.cache_read_tokens:,}")
        print(f"  Output Tokens:{stat.output_tokens:,}")
        print(f"  Compactions:  {stat.compactions}")
        print(f"  Cost-Weighted Input Tokens: {stat.weighted_input_tokens:,.1f}")
        print("-" * 80)

    return 0
