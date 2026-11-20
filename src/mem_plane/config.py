"""Configuration settings and storage factory for mem-plane."""

from typing import Literal

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

from mem_plane.storage.protocols import MetadataStorage, SearchIndex


class Settings(BaseSettings):
    """Runtime configuration settings for mem-plane."""

    model_config = SettingsConfigDict(
        env_prefix="MEM_PLANE_",
        env_file=".env",
        extra="ignore",
    )

    storage_backend: Literal["sqlite", "production_experimental"] = Field(
        default="sqlite",
        description="Active storage backend: 'sqlite' or 'production_experimental' (Postgres + OpenSearch)",
    )
    db_path: str = Field(
        default="~/.mem-plane/memory.db",
        description="Path to SQLite database file",
    )
    log_dir: str = Field(
        default="~/.mem-plane/logs",
        description="Directory for logs and debug payloads",
    )
    project_id: str | None = Field(
        default=None,
        description="Forced project ID override",
    )
    embedding_provider: Literal["fastembed", "none", "dummy"] = Field(
        default="fastembed",
        description="Active embedding provider: 'fastembed', 'none', or 'dummy'",
    )
    embedding_model: str = Field(
        default="BAAI/bge-small-en-v1.5",
        description="Dense vector embedding model name",
    )
    allow_dummy_embeddings: bool = Field(
        default=False,
        description="Allow dummy embeddings (required when embedding_provider='dummy')",
    )
    embedding_dimension: int = Field(
        default=384,
        description="Dense vector embedding dimension",
    )
    max_cosine_distance: float = Field(
        default=0.55,
        description="Cosine distance cut-off for vector search (distance > max dropped)",
    )
    rrf_k: int = Field(
        default=60,
        description="Rank reciprocal fusion constant",
    )
    weight_fact_keyword: float = Field(
        default=1.0,
        description="Fusion weight for fact keyword matches",
    )
    weight_fact_vector: float = Field(
        default=1.0,
        description="Fusion weight for fact vector matches",
    )
    weight_episode_keyword: float = Field(
        default=0.5,
        description="Fusion weight for episode keyword matches",
    )
    weight_episode_vector: float = Field(
        default=0.5,
        description="Fusion weight for episode vector matches",
    )
    prune_enabled: bool = Field(
        default=True,
        description="Turn the PostToolUse pruner on or off",
    )
    prune_max_lines: int = Field(
        default=120,
        description="Threshold of total lines for PostToolUse pruning",
    )
    prune_max_chars: int = Field(
        default=8000,
        description="Threshold of total characters for PostToolUse pruning",
    )
    prune_exclude_regex: str = Field(
        default=r"^(cat |head |tail |sed -n|git diff|git show)|mem-plane",
        description="Regex pattern for commands excluded from pruning",
    )
    preview_head_lines: int = Field(
        default=15,
        description="Number of initial lines kept in preview",
    )
    preview_tail_lines: int = Field(
        default=25,
        description="Number of trailing lines kept in preview",
    )
    preview_signal_lines: int = Field(
        default=30,
        description="Max signal lines kept in preview",
    )
    preview_max_chars: int = Field(
        default=4000,
        description="Maximum characters in pruned preview",
    )
    trace_retention_days: int = Field(
        default=14,
        description="Trace retention period in days",
    )
    session_context_max_chars: int = Field(
        default=6000,
        description="Maximum characters for SessionStart context",
    )
    recent_facts: int = Field(
        default=8,
        description="Number of recent facts to include in SessionStart",
    )
    chunk_target_chars: int = Field(
        default=4800,
        description="Target size in characters for transcript chunks",
    )
    auto_recall: bool = Field(
        default=False,
        description="Enable automatic recall on prompt submit",
    )
    extractor: Literal["off", "anthropic"] = Field(
        default="off",
        description="Fact extractor mode",
    )
    redact: bool = Field(
        default=True,
        description="Enable secret redaction",
    )
    hook_debug: bool = Field(
        default=False,
        description="Save raw hook payloads for debugging",
    )
    sqlite_db_path: str = Field(
        default=":memory:",
        description="Legacy SQLite path field",
    )
    postgres_dsn: str = Field(
        default="postgresql://postgres:postgres@localhost:5432/mem_plane",
        description="PostgreSQL connection string DSN",
    )
    opensearch_url: str = Field(
        default="http://localhost:9200",
        description="OpenSearch cluster endpoint URL",
    )
    opensearch_index: str = Field(
        default="mem_plane_facts",
        description="OpenSearch index name for facts",
    )


async def get_storage_and_index(
    settings: Settings | None = None,
) -> tuple[MetadataStorage, SearchIndex]:
    """Factory creating and initializing MetadataStorage and SearchIndex instances."""
    cfg = settings or Settings()

    if cfg.storage_backend == "sqlite":
        from mem_plane.storage.sqlite.backend import SQLiteBackend

        backend = SQLiteBackend(
            database=cfg.sqlite_db_path,
            dimension=cfg.embedding_dimension,
        )
        await backend.initialize()
        return backend, backend

    elif cfg.storage_backend == "production_experimental":
        from mem_plane.storage.production.opensearch import OpenSearchBackend
        from mem_plane.storage.production.postgres import PostgresBackend

        metadata_storage = PostgresBackend(dsn=cfg.postgres_dsn)
        await metadata_storage.initialize()

        search_index = OpenSearchBackend(
            opensearch_url=cfg.opensearch_url,
            index_name=cfg.opensearch_index,
            dimension=cfg.embedding_dimension,
        )
        await search_index.initialize()
        return metadata_storage, search_index

    raise ValueError(f"Unsupported storage backend: {cfg.storage_backend}")
