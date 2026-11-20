"""Unified command-line interface for mem-plane (Section 10)."""

from __future__ import annotations

import argparse
import json
import os
import sys
from collections.abc import Sequence
from pathlib import Path

from mem_plane.bench import run_bench
from mem_plane.hooks.runner import run_hook


def create_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="mem-plane",
        description="Local persistent memory plane and context manager for Claude Code.",
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

    # reindex command
    reindex_parser = subparsers.add_parser("reindex", help="Rebuild vector indexes")
    reindex_parser.add_argument("--model", help="Embedding model name")

    # gc command
    subparsers.add_parser("gc", help="Purge expired traces and optimize database")

    # export command
    export_parser = subparsers.add_parser("export", help="Export facts and history")
    export_parser.add_argument("--project", help="Project path or ID")

    # import command
    import_parser = subparsers.add_parser("import", help="Import facts from JSON Lines file")
    import_parser.add_argument("file", help="JSON Lines file to import")

    # doctor command
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

    if args.command == "doctor":
        from mem_plane.doctor import print_hooks_config, run_doctor

        if args.print_hooks:
            print_hooks_config()
            return 0
        return run_doctor()

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

    if args.command == "reindex":
        from mem_plane.config import Settings
        from mem_plane.pipeline.embedder_worker import embed_pending_batch_sync
        from mem_plane.pipeline.embeddings import SUPPORTED_MODELS, build_embedding_client
        from mem_plane.storage.sqlite.schema import get_schema_v1_ddl
        from mem_plane.storage.sqlite.store import SQLiteStore

        settings = Settings()
        model_name = args.model or settings.embedding_model
        if model_name not in SUPPORTED_MODELS:
            print(
                f"Error: Unsupported model '{model_name}'. Supported models: {list(SUPPORTED_MODELS.keys())}",
                file=sys.stderr,
            )
            return 1
        dim = SUPPORTED_MODELS[model_name]

        store = SQLiteStore(
            database=settings.db_path,
            dimension=dim,
            model_name=model_name,
            settings=settings,
        )
        try:
            with store.conn:
                store.conn.execute("DROP TABLE IF EXISTS facts_vec;")
                store.conn.execute("DROP TABLE IF EXISTS episode_vec;")
                ddl = get_schema_v1_ddl(
                    dimension=dim, partition_keys=store.partition_keys_supported
                )
                for stmt in ddl:
                    if "facts_vec" in stmt or "episode_vec" in stmt:
                        store.conn.execute(stmt)
                store.conn.execute(
                    "INSERT OR REPLACE INTO meta (key, value) VALUES ('embedding_model', ?);",
                    (model_name,),
                )
                store.conn.execute(
                    "INSERT OR REPLACE INTO meta (key, value) VALUES ('embedding_dimension', ?);",
                    (str(dim),),
                )
                store.conn.execute("UPDATE facts SET embedded_at = NULL;")
                store.conn.execute("UPDATE episode_chunks SET embedded_at = NULL;")

            settings.embedding_model = model_name
            settings.embedding_dimension = dim
            client = build_embedding_client(settings)
            total_embedded = 0
            if client:
                while True:
                    cnt = embed_pending_batch_sync(store, client, limit=64)
                    if cnt == 0:
                        break
                    total_embedded += cnt

            print(
                f"Reindexed database with model '{model_name}' ({dim} dimensions). Embedded {total_embedded} items."
            )
            return 0
        finally:
            store.close()

    if args.command == "gc":
        import sqlite3

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
            try:
                store.conn.execute("INSERT INTO facts_fts(facts_fts) VALUES('optimize');")
                store.conn.execute("INSERT INTO episode_fts(episode_fts) VALUES('optimize');")
            except sqlite3.OperationalError:
                pass
            print(f"Purged {purged} expired traces. Checkpoint and optimization complete.")
            return 0
        finally:
            store.close()

    if args.command == "export":
        from mem_plane.config import Settings
        from mem_plane.project import resolve_project
        from mem_plane.storage.sqlite.store import SQLiteStore

        settings = Settings()
        store = SQLiteStore(database=settings.db_path, load_vec=False)
        try:
            target_proj_id = None
            if args.project:
                target_proj_id = resolve_project(args.project).project_id

            if target_proj_id:
                cur = store.conn.execute(
                    """
                    SELECT fact_id, project_id, session_id, kind, summary, body,
                           status, supersedes, superseded_by, pinned, source, created_at, updated_at
                    FROM facts WHERE project_id = ? ORDER BY created_at ASC;
                    """,
                    (target_proj_id,),
                )
            else:
                cur = store.conn.execute(
                    """
                    SELECT fact_id, project_id, session_id, kind, summary, body,
                           status, supersedes, superseded_by, pinned, source, created_at, updated_at
                    FROM facts ORDER BY created_at ASC;
                    """
                )

            for r in cur.fetchall():
                cur_p = store.conn.execute(
                    "SELECT rel_path, content_sha256, git_commit FROM fact_paths WHERE fact_id = ?;",
                    (r[0],),
                )
                paths = [
                    {"rel_path": pr[0], "content_sha256": pr[1], "git_commit": pr[2]}
                    for pr in cur_p.fetchall()
                ]
                fact_dict = {
                    "fact_id": r[0],
                    "project_id": r[1],
                    "session_id": r[2],
                    "kind": r[3],
                    "summary": r[4],
                    "body": r[5],
                    "status": r[6],
                    "supersedes": r[7],
                    "superseded_by": r[8],
                    "pinned": bool(r[9]),
                    "source": r[10],
                    "created_at": r[11],
                    "updated_at": r[12],
                    "paths": paths,
                }
                print(json.dumps({"type": "fact", "data": fact_dict}))

            # Export history
            if target_proj_id:
                cur_h = store.conn.execute(
                    """
                    SELECT h.history_id, h.fact_id, h.old_summary, h.new_summary,
                           h.old_body, h.new_body, h.actor, h.session_id, h.created_at
                    FROM fact_history h
                    JOIN facts f ON h.fact_id = f.fact_id
                    WHERE f.project_id = ?
                    ORDER BY h.created_at ASC;
                    """,
                    (target_proj_id,),
                )
            else:
                cur_h = store.conn.execute(
                    """
                    SELECT history_id, fact_id, old_summary, new_summary,
                           old_body, new_body, actor, session_id, created_at
                    FROM fact_history ORDER BY created_at ASC;
                    """
                )

            for hr in cur_h.fetchall():
                hist_dict = {
                    "history_id": hr[0],
                    "fact_id": hr[1],
                    "old_summary": hr[2],
                    "new_summary": hr[3],
                    "old_body": hr[4],
                    "new_body": hr[5],
                    "actor": hr[6],
                    "session_id": hr[7],
                    "created_at": hr[8],
                }
                print(json.dumps({"type": "history", "data": hist_dict}))

            return 0
        finally:
            store.close()

    if args.command == "import":
        from mem_plane.config import Settings
        from mem_plane.core.models import Fact
        from mem_plane.storage.sqlite.store import SQLiteStore

        filepath = Path(args.file).expanduser().resolve()
        if not filepath.is_file():
            print(f"Error: Import file not found: {filepath}", file=sys.stderr)
            return 1

        settings = Settings()
        store = SQLiteStore(database=settings.db_path, load_vec=False)
        imported_count = 0
        skipped_count = 0

        try:
            with open(filepath, encoding="utf-8") as f:
                for line in f:
                    line_str = line.strip()
                    if not line_str:
                        continue
                    try:
                        entry = json.loads(line_str)
                    except (json.JSONDecodeError, ValueError):
                        continue
                    if not isinstance(entry, dict):
                        continue

                    fact_data = entry.get("data") if entry.get("type") == "fact" else entry
                    if not isinstance(fact_data, dict) or "summary" not in fact_data:
                        continue

                    try:
                        fact_obj = Fact.model_validate(fact_data)
                        res = store.create_fact(fact_obj)
                        if res.status in ("created", "superseded"):
                            imported_count += 1
                        else:
                            skipped_count += 1
                    except (KeyError, ValueError, TypeError):
                        continue

            print(f"Imported {imported_count} facts, skipped {skipped_count} duplicates.")
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
