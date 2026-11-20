"""Command-line interface for mem-plane."""

from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence

from mem_plane.bench import run_bench
from mem_plane.hooks.runner import run_hook


def create_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="mem-plane",
        description="mem-plane: long-lived memory and tool-output shortening for AI harnesses",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    # hook command
    hook_parser = subparsers.add_parser("hook", help="Run a Claude Code hook handler")
    hook_parser.add_argument(
        "event",
        help="Hook event name (e.g. session-start, post-tool-use, stop, pre-compact, session-end, user-prompt-submit)",
    )

    # serve command (stub for M0, implemented in M2/M6)
    subparsers.add_parser("serve", help="Run MCP server over stdio")

    # status command (stub for M0)
    status_parser = subparsers.add_parser("status", help="Print memory status")
    status_parser.add_argument("--project", help="Project path or ID")

    # search command (stub for M0)
    search_parser = subparsers.add_parser("search", help="Search memory")
    search_parser.add_argument("query", help="Query text")
    search_parser.add_argument("--project", help="Project path or ID")

    # embed-pending command (stub for M0)
    subparsers.add_parser("embed-pending", help="Embed all pending records")

    # reindex command (stub for M0)
    reindex_parser = subparsers.add_parser("reindex", help="Rebuild vector indexes")
    reindex_parser.add_argument("--model", help="Embedding model name")

    # gc command (stub for M0)
    subparsers.add_parser("gc", help="Purge expired traces and optimize database")

    # export command (stub for M0)
    export_parser = subparsers.add_parser("export", help="Export facts and history")
    export_parser.add_argument("--project", help="Project path or ID")

    # import command (stub for M0)
    import_parser = subparsers.add_parser("import", help="Import facts from JSON Lines")
    import_parser.add_argument("file", help="JSON Lines file to import")

    # doctor command (stub for M0)
    doctor_parser = subparsers.add_parser("doctor", help="Check installation and configuration")
    doctor_parser.add_argument(
        "--print-hooks", action="store_true", help="Print hook configuration"
    )

    # bench command (stub for M0)
    bench_parser = subparsers.add_parser("bench", help="Generate token report from transcripts")
    bench_parser.add_argument("transcripts", nargs="+", help="Transcript file path(s)")

    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = create_parser()
    args = parser.parse_args(argv)

    if args.command == "hook":
        return run_hook(args.event)

    if args.command == "bench":
        return run_bench(args.transcripts)

    print(f"Command '{args.command}' is not implemented yet.", file=sys.stderr)
    return 1


if __name__ == "__main__":
    sys.exit(main())
