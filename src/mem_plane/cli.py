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

    # serve command
    subparsers.add_parser("serve", help="Run MCP server over stdio")

    # status command
    status_parser = subparsers.add_parser("status", help="Print memory status")
    status_parser.add_argument("--project", help="Project path or ID")

    # search command
    search_parser = subparsers.add_parser("search", help="Search memory")
    search_parser.add_argument("query", help="Query text")
    search_parser.add_argument("--project", help="Project path or ID")

    # embed-pending command
    subparsers.add_parser("embed-pending", help="Embed all pending records")

    # reindex command (stub)
    reindex_parser = subparsers.add_parser("reindex", help="Rebuild vector indexes")
    reindex_parser.add_argument("--model", help="Embedding model name")

    # gc command
    subparsers.add_parser("gc", help="Purge expired traces and optimize database")

    # export command (stub)
    export_parser = subparsers.add_parser("export", help="Export facts and history")
    export_parser.add_argument("--project", help="Project path or ID")

    # import command (stub)
    import_parser = subparsers.add_parser("import", help="Import facts from JSON Lines file")
    import_parser.add_argument("file", help="JSON Lines file to import")

    # doctor command (stub)
    doctor_parser = subparsers.add_parser("doctor", help="Check installation and configuration")
    doctor_parser.add_argument(
        "--print-hooks", action="store_true", help="Print hook configuration"
    )

    # bench command
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

    if args.command == "embed-pending":
        from mem_plane.config import Settings
        from mem_plane.pipeline.embedder_worker import embed_pending_batch_sync
        from mem_plane.pipeline.embeddings import build_embedding_client
        from mem_plane.storage.sqlite.store import SQLiteStore

        settings = Settings()
        client = build_embedding_client(settings)
        if client is None:
            print("Vector search / embeddings disabled or unavailable.", file=sys.stderr)
            return 1

        store = SQLiteStore(database=settings.db_path, dimension=settings.embedding_dimension)
        try:
            total = 0
            while True:
                count = embed_pending_batch_sync(store, client, limit=64)
                if count == 0:
                    break
                total += count
            print(f"Embedded {total} pending items.")
            return 0
        finally:
            store.close()

    if args.command == "gc":
        from mem_plane.config import Settings
        from mem_plane.storage.sqlite.store import SQLiteStore

        settings = Settings()
        store = SQLiteStore(
            database=settings.db_path,
            dimension=settings.embedding_dimension,
            settings=settings,
        )
        try:
            purged = store.purge_expired_traces()
            store.conn.execute("PRAGMA wal_checkpoint(TRUNCATE);")
            store.conn.execute("PRAGMA optimize;")
            print(f"Purged {purged} expired traces. Checkpoint and optimization complete.")
            return 0
        finally:
            store.close()

    if args.command == "serve":
        import asyncio

        from mem_plane.server.mcp_server import run_server_stdio

        asyncio.run(run_server_stdio())
        return 0

    if args.command == "search":
        import asyncio
        import os

        from mem_plane.config import Settings
        from mem_plane.pipeline.embeddings import build_embedding_client
        from mem_plane.project import resolve_project
        from mem_plane.server.service import MemPlaneService
        from mem_plane.storage.sqlite.store import SQLiteStore

        settings = Settings()
        store = SQLiteStore(
            database=settings.db_path,
            dimension=settings.embedding_dimension,
            settings=settings,
        )
        proj_dir = args.project or os.getcwd()
        proj = resolve_project(proj_dir)
        store.upsert_project(proj)
        client = build_embedding_client(settings)
        svc = MemPlaneService(
            store=store,
            project_id=proj.project_id,
            embedding_client=client,
            settings=settings,
            project_root=proj.root_path,
        )
        try:
            out = asyncio.run(svc.search_memory(query=args.query))
            print(out)
            return 0
        finally:
            store.close()

    if args.command == "status":
        import asyncio
        import os

        from mem_plane.config import Settings
        from mem_plane.pipeline.embeddings import build_embedding_client
        from mem_plane.project import resolve_project
        from mem_plane.server.service import MemPlaneService
        from mem_plane.storage.sqlite.store import SQLiteStore

        settings = Settings()
        store = SQLiteStore(
            database=settings.db_path,
            dimension=settings.embedding_dimension,
            settings=settings,
        )
        proj_dir = args.project or os.getcwd()
        proj = resolve_project(proj_dir)
        store.upsert_project(proj)
        client = build_embedding_client(settings)
        svc = MemPlaneService(
            store=store,
            project_id=proj.project_id,
            embedding_client=client,
            settings=settings,
            project_root=proj.root_path,
        )
        try:
            out = asyncio.run(svc.memory_status())
            print(out)
            return 0
        finally:
            store.close()

    print(f"Command '{args.command}' is not implemented yet.", file=sys.stderr)
    return 1


if __name__ == "__main__":
    sys.exit(main())
