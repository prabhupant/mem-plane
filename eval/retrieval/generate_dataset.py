"""Dataset generator for retrieval evaluation suite (Section 15.3).

Generates 150 facts across 3 projects with near-duplicates, superseded pairs,
and 150 queries (exact identifiers, paraphrased, replaced decisions).
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

OUT_DIR = Path(__file__).parent

PROJECTS = ["payments-api", "auth-service", "data-pipeline"]

# 15 exact identifier items per project
EXACT_TEMPLATES = [
    # (identifier, summary_template, body_template, query_text)
    (
        "--no-cov",
        "Fast test runner always passes --no-cov flag",
        "Pytest runs in under 2 seconds when coverage instrumentation is disabled via --no-cov.",
        "--no-cov flag",
    ),
    (
        "argon2id",
        "Password hashing algorithm is strictly argon2id",
        "Passlib is configured with argon2id using memory_cost=65536 and time_cost=3.",
        "argon2id",
    ),
    (
        "busy_timeout=5000",
        "SQLite connection sets busy_timeout=5000 for lock contention",
        "Pragma busy_timeout is set to 5000 ms to avoid immediate database locked errors.",
        "busy_timeout=5000",
    ),
    (
        "sqlite-vec v0.1.6",
        "Vector extension requires sqlite-vec v0.1.6 or newer",
        "Vector virtual tables vec0 use partition keys starting from sqlite-vec v0.1.6.",
        "sqlite-vec v0.1.6",
    ),
    (
        "PRAGMA synchronous=NORMAL",
        "WAL journaling pragma synchronous=NORMAL is enforced",
        "Sync normal reduces disk fsync operations without sacrificing WAL durability.",
        "PRAGMA synchronous=NORMAL",
    ),
    (
        "auth_jwt_v2",
        "Token schema header format is auth_jwt_v2",
        "Authorization header bearer tokens must conform to the auth_jwt_v2 envelope schema.",
        "auth_jwt_v2",
    ),
    (
        "stripe_webhook_secret",
        "Stripe payment webhook verification uses stripe_webhook_secret",
        "Webhook signatures are validated against stripe_webhook_secret in environment.",
        "stripe_webhook_secret",
    ),
    (
        "redis_pool_size=20",
        "Redis connection pool size is capped at redis_pool_size=20",
        "Asyncio connection pool limits concurrent connections with redis_pool_size=20.",
        "redis_pool_size=20",
    ),
    (
        "MAX_RETRIES=3",
        "External HTTP requests enforce MAX_RETRIES=3 with exponential backoff",
        "HTTP client retry adapter sets MAX_RETRIES=3 on network timeouts.",
        "MAX_RETRIES=3",
    ),
    (
        "kafka_consumer_group",
        "Event ingestion uses dedicated kafka_consumer_group",
        "Consumer group offset commits are tracked by kafka_consumer_group.",
        "kafka_consumer_group",
    ),
    (
        "snowflake_warehouse_xs",
        "Warehouse sizing defaults to snowflake_warehouse_xs",
        "ETL queries scale horizontally starting on snowflake_warehouse_xs.",
        "snowflake_warehouse_xs",
    ),
    (
        "parquet_snappy_compression",
        "Data export files must use parquet_snappy_compression",
        "Batch dumps written to cloud storage use snappy compression inside parquet files.",
        "parquet_snappy_compression",
    ),
    (
        "celery_task_acks_late",
        "Background task execution enables celery_task_acks_late",
        "Workers acknowledge task completion only after execution to prevent task loss.",
        "celery_task_acks_late",
    ),
    (
        "uvicorn_workers=4",
        "ASGI web server runs with uvicorn_workers=4 per container",
        "Production deployment spawns 4 worker processes per task container.",
        "uvicorn_workers=4",
    ),
    (
        "s3_bucket_prefix=archive",
        "Cold storage bucket naming requires s3_bucket_prefix=archive",
        "Long term historical backups are routed to buckets starting with archive.",
        "s3_bucket_prefix=archive",
    ),
    (
        "DB_POOL_MIN_SIZE=5",
        "Minimum connection pool floor set to DB_POOL_MIN_SIZE=5",
        "Database pool maintains warm idle connections with DB_POOL_MIN_SIZE=5.",
        "DB_POOL_MIN_SIZE=5",
    ),
    (
        "MAX_REQUEST_SIZE_MB=10",
        "HTTP payload size ceiling capped at MAX_REQUEST_SIZE_MB=10",
        "Nginx reverse proxy rejects requests exceeding MAX_REQUEST_SIZE_MB=10.",
        "MAX_REQUEST_SIZE_MB=10",
    ),
]

# 17 conceptual / paraphrased items per project
CONCEPTUAL_TEMPLATES = [
    # (topic, summary, body, paraphrased_query)
    (
        "retry_jitter",
        "Exponential backoff retry policy includes full random jitter",
        "To avoid thundering herd problem against downstream APIs, retries add random uniform jitter up to 1000ms.",
        "how do we avoid thundering herd on downstream service retries",
    ),
    (
        "graceful_shutdown",
        "Graceful shutdown drains in-flight requests within 30 second window",
        "SIGTERM handler stops accepting new connections and permits running requests 30s before forceful kill.",
        "what happens when a service container receives sigterm signal",
    ),
    (
        "idempotency_keys",
        "API mutation requests require idempotency key header for safe retries",
        "POST and PATCH operations check client provided Idempotency-Key header against cache for 24 hours.",
        "preventing duplicate credit card charges on network retransmissions",
    ),
    (
        "circuit_breaker",
        "Circuit breaker trips to open state after 5 consecutive upstream timeouts",
        "When failure rate reaches threshold, circuit enters open state for 60 seconds before testing recovery.",
        "handling cascading failures when third party payment gateway is down",
    ),
    (
        "connection_pooling",
        "Database connection pooling limits idle connections and recycles hourly",
        "Pool connections are recycled every 3600 seconds to prevent stale TCP sockets behind AWS NLB.",
        "preventing closed socket errors on database connections behind load balancer",
    ),
    (
        "rate_limiting",
        "Token bucket rate limiting allocates 100 requests per minute per IP",
        "Excess requests receive HTTP 429 Too Many Requests with a Retry-After header calculated from bucket refill.",
        "protecting public endpoints from abusive client traffic and denial of service",
    ),
    (
        "session_ttl",
        "User authentication sessions expire after 24 hours of inactivity",
        "Rolling inactivity timer invalidates session cookie in Redis if no authenticated request arrives within 24h.",
        "how long does an inactive user stay logged in before having to authenticate again",
    ),
    (
        "audit_logging",
        "PII access operations write tamper-evident audit log entries",
        "Any read of customer personally identifiable information publishes an immutable audit event to security sink.",
        "compliance requirements for tracking who viewed customer private data",
    ),
    (
        "database_indexing",
        "Composite index on project_id and created_at required for timeline queries",
        "Query planner avoids sequential scans on large tables by leveraging B-tree index on project and timestamp.",
        "optimizing slow queries that filter by project and sort by creation date",
    ),
    (
        "secret_rotation",
        "API private keys and encryption secrets rotate automatically every 90 days",
        "Dual-key verification window allows a 24-hour overlap when master encryption keys rotate.",
        "how frequently do we rotate encryption secrets and keys",
    ),
    (
        "error_budget",
        "Service level objective error budget threshold set at 99.9% uptime",
        "Feature deployments freeze if monthly failure budget drops below remaining threshold.",
        "policy for freezing production releases when reliability targets are missed",
    ),
    (
        "data_retention",
        "Ephemeral debugging traces purged automatically after 14 days",
        "Diagnostic telemetry logs expire and get purged by daily vacuum cleaner job to minimize storage footprint.",
        "cleanup policy for old debugging logs and execution traces",
    ),
    (
        "schema_migrations",
        "Zero-downtime database migrations mandate backward-compatible column additions",
        "Columns must be added as nullable or with defaults before application code references them in release N+1.",
        "guidelines for deploying database schema changes without taking the service down",
    ),
    (
        "health_checks",
        "Liveness probes return 200 independently of external service dependencies",
        "Liveness probe must not check database or cache status to prevent orchestrator restarting healthy nodes.",
        "preventing kubernetes restart loops during database maintenance",
    ),
    (
        "cache_invalidation",
        "Cache stampede protection utilizes distributed mutex lock during recalculation",
        "When cache key expires, only the lock holder recomputes the payload while other requests wait.",
        "preventing cache stampede when popular cached item expires",
    ),
    (
        "tracing_propagation",
        "Distributed trace headers W3C tracecontext must be propagated across service calls",
        "Outgoing HTTP requests must inject traceparent and tracestate headers from active context.",
        "how do we track request latency and spans across microservices",
    ),
    (
        "cors_policy",
        "Cross-origin resource sharing policy restricts allowed origins to company domains",
        "Access-Control-Allow-Origin header strictly validates against whitelist regex before returning 200.",
        "what domains are permitted to call our api from browser javascript",
    ),
]

# 8 superseded pairs per project (16 facts per project -> 17 + 17 + 16 = 50 facts, 50 queries)
SUPERSEDED_PAIRS = [
    (
        "Monolithic single database used for all multi-tenant storage",
        "Initial MVP used a single global PostgreSQL database with tenant_id columns on every table.",
        "Per-project SQLite database with local isolation adopted",
        "Migrated to isolated per-project SQLite files in ~/.mem-plane to ensure zero cross-tenant blast radius.",
        "what database architecture do we use to isolate customer data",
    ),
    (
        "JWT tokens signed with symmetric HMAC-SHA256 secret",
        "Shared secret key stored in environment was used for signing and verifying tokens.",
        "JWT tokens signed with asymmetric Ed25519 public-private keypairs",
        "Replaced HMAC with Ed25519 keys so verification services only need the public key.",
        "what cryptographic algorithm do we use to sign authentication tokens",
    ),
    (
        "Batch export format was gzipped CSV files",
        "Nightly customer exports generated large .csv.gz archives.",
        "Batch export format upgraded to Apache Parquet with zstd compression",
        "Switched to columnar Parquet files resulting in 70% storage savings and faster analytic queries.",
        "what file format is used for nightly data exports",
    ),
    (
        "REST polling used for async payment status updates",
        "Frontend clients polled /status endpoint every 2 seconds until transaction completed.",
        "Server-Sent Events SSE stream used for real-time payment notifications",
        "Replaced polling with persistent SSE connection reducing server load by 85%.",
        "how do clients receive updates when a payment transaction completes",
    ),
    (
        "Default password minimum length was 8 characters",
        "Legacy user registration required passwords with at least 8 characters.",
        "Password minimum length increased to 14 characters without complexity rules",
        "Adopted modern NIST guidelines requiring minimum 14 characters and checking HaveIBeenPwned API.",
        "what is the password length requirement for user accounts",
    ),
    (
        "Docker container base image used ubuntu:22.04",
        "Microservices were packaged on Ubuntu base image with full standard packages.",
        "Docker container base image migrated to distroless minimal runtime",
        "Switched to Google distroless containers eliminating package managers and shell binaries for security.",
        "what base operating system image is used in our docker containers",
    ),
    (
        "Memory cache provider was Memcached cluster",
        "Application caches used a cluster of Memcached nodes with binary protocol.",
        "Memory cache provider standardized on Redis with Redis Sentinel failover",
        "Replaced Memcached with Redis to support complex data structures and atomic operations.",
        "what caching technology do we use for in-memory caching",
    ),
    (
        "Log aggregation delivered via self-hosted Elasticsearch",
        "Application logs were shipped to a self-managed 3-node Elasticsearch cluster.",
        "Log aggregation transitioned to Grafana Loki with object storage",
        "Migrated to Loki to avoid heavy Java heap management and indexing costs.",
        "where are application logs stored and searched",
    ),
]


def generate() -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    facts: list[dict[str, Any]] = []
    queries: list[dict[str, Any]] = []

    fact_idx = 1
    query_idx = 1

    for proj in PROJECTS:
        proj_prefix = proj.split("-")[0][:4]

        # 1. Exact identifier facts and queries
        for ident, summary, body, q_text in EXACT_TEMPLATES:
            fid = f"f_{proj_prefix}_{fact_idx:04d}"
            fact_idx += 1
            facts.append(
                {
                    "fact_id": fid,
                    "project_id": proj,
                    "kind": "rule",
                    "summary": f"[{proj}] {summary}",
                    "body": f"{body} Relevant identifier: `{ident}`.",
                    "status": "active",
                }
            )
            qid = f"q_{query_idx:04d}"
            query_idx += 1
            queries.append(
                {
                    "query_id": qid,
                    "project": proj,
                    "query": q_text,
                    "relevant": [fid],
                    "type": "exact_identifier",
                }
            )

        # 2. Conceptual facts and paraphrased queries
        for topic, summary, body, para_query in CONCEPTUAL_TEMPLATES:
            fid = f"f_{proj_prefix}_{fact_idx:04d}"
            fact_idx += 1
            facts.append(
                {
                    "fact_id": fid,
                    "project_id": proj,
                    "kind": "decision",
                    "summary": f"[{proj}] {summary}",
                    "body": body,
                    "status": "active",
                }
            )
            qid = f"q_{query_idx:04d}"
            query_idx += 1
            queries.append(
                {
                    "query_id": qid,
                    "project": proj,
                    "query": para_query,
                    "relevant": [fid],
                    "type": "paraphrased",
                }
            )

        # 3. Superseded pairs and replacement queries
        for old_sum, old_body, new_sum, new_body, repl_query in SUPERSEDED_PAIRS:
            old_fid = f"f_{proj_prefix}_{fact_idx:04d}"
            fact_idx += 1
            new_fid = f"f_{proj_prefix}_{fact_idx:04d}"
            fact_idx += 1

            # Old fact is superseded
            facts.append(
                {
                    "fact_id": old_fid,
                    "project_id": proj,
                    "kind": "decision",
                    "summary": f"[{proj}] {old_sum}",
                    "body": old_body,
                    "status": "superseded",
                    "superseded_by": new_fid,
                }
            )
            # New fact is active
            facts.append(
                {
                    "fact_id": new_fid,
                    "project_id": proj,
                    "kind": "decision",
                    "summary": f"[{proj}] {new_sum}",
                    "body": new_body,
                    "status": "active",
                    "supersedes": old_fid,
                }
            )

            qid = f"q_{query_idx:04d}"
            query_idx += 1
            queries.append(
                {
                    "query_id": qid,
                    "project": proj,
                    "query": repl_query,
                    "relevant": [new_fid],  # ONLY the newer fact is relevant!
                    "type": "replaced_decision",
                }
            )

            # Query about the newer decision summary
            qid2 = f"q_{query_idx:04d}"
            query_idx += 1
            queries.append(
                {
                    "query_id": qid2,
                    "project": proj,
                    "query": f"details on {new_sum.lower()}",
                    "relevant": [new_fid],
                    "type": "replaced_decision",
                }
            )

    return facts, queries


def main() -> None:
    facts, queries = generate()
    print(f"Generated {len(facts)} facts across {len(PROJECTS)} projects.")
    print(f"Generated {len(queries)} queries.")

    facts_path = OUT_DIR / "facts.jsonl"
    with open(facts_path, "w", encoding="utf-8") as f:
        f.writelines(json.dumps(item) + "\n" for item in facts)

    queries_path = OUT_DIR / "queries.jsonl"
    with open(queries_path, "w", encoding="utf-8") as f:
        f.writelines(json.dumps(item) + "\n" for item in queries)

    print(f"Wrote {facts_path} and {queries_path}")


if __name__ == "__main__":
    main()
