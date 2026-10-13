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

    storage_backend: Literal["sqlite", "production"] = Field(
        default="sqlite",
        description="Active storage backend: 'sqlite' or 'production' (Postgres + OpenSearch)",
    )
    sqlite_db_path: str = Field(
        default=":memory:",
        description="Path to SQLite database file or :memory:",
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
    embedding_dimension: int = Field(
        default=384,
        description="Dense vector embedding dimension",
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

    elif cfg.storage_backend == "production":
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
