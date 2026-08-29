"""Narrow memory migration, doctor, and inspection commands."""

from __future__ import annotations

import hashlib
import os
from collections.abc import Mapping
from importlib.resources import files

import psycopg
from pydantic import BaseModel, ConfigDict, Field

from sage.errors import MemoryConfigurationError, MemoryStorageError
from sage.memory.adapters.postgres.connection import MemoryConnectionPool
from sage.memory.adapters.postgres.store import (
    EXPECTED_SCHEMA_VERSION,
    PostgresMemoryStore,
)
from sage.memory.models import RepositoryIdentity
from sage.memory.parsing import verify_grammars
from sage.memory.retrieval.sparse import SQLiteSparseIndex


class MemoryAdminSettings(BaseModel):
    """Memory-only settings that never require coding-provider credentials."""

    model_config = ConfigDict(frozen=True)

    enabled: bool = False
    database_url: str | None = Field(default=None, repr=False)
    migration_database_url: str | None = Field(default=None, repr=False)
    db_timeout_seconds: int = Field(default=15, ge=1, le=60)
    summarizer_provider: str = Field(default="google", min_length=1, max_length=80)
    summarizer_model: str = Field(
        default="gemini-3.5-flash", min_length=1, max_length=120
    )

    @classmethod
    def from_env(
        cls, environ: Mapping[str, str] | None = None
    ) -> "MemoryAdminSettings":
        values = os.environ if environ is None else environ
        return cls(
            enabled=_bool(values.get("SAGE_MEMORY_ENABLED", "false")),
            database_url=values.get("SAGE_MEMORY_DATABASE_URL", "").strip() or None,
            migration_database_url=(
                values.get("SAGE_MEMORY_MIGRATION_DATABASE_URL", "").strip() or None
            ),
            db_timeout_seconds=values.get("SAGE_MEMORY_DB_TIMEOUT_SECONDS", "15"),
            summarizer_provider=(
                values.get("SAGE_MEMORY_SUMMARIZER_PROVIDER", "google").strip()
                or "google"
            ),
            summarizer_model=(
                values.get(
                    "SAGE_MEMORY_SUMMARIZER_MODEL", "gemini-3.5-flash"
                ).strip()
                or "gemini-3.5-flash"
            ),
        )


def migrate(settings: MemoryAdminSettings) -> str:
    """Apply the packaged migration once under a transaction-scoped lock."""

    dsn = settings.migration_database_url
    if dsn is None:
        raise MemoryConfigurationError(
            "SAGE_MEMORY_MIGRATION_DATABASE_URL is required for migration."
        )
    migration = files("sage.memory.migrations").joinpath(
        f"{EXPECTED_SCHEMA_VERSION}.sql"
    ).read_text(encoding="utf-8")
    checksum = hashlib.sha256(migration.encode("utf-8")).hexdigest()
    try:
        with psycopg.connect(
            dsn, connect_timeout=settings.db_timeout_seconds
        ) as connection:
            with connection.transaction():
                connection.execute(
                    "SELECT pg_advisory_xact_lock(hashtext(%s))",
                    ("sage_smrt_migrations",),
                )
                connection.execute("CREATE SCHEMA IF NOT EXISTS sage_smrt")
                connection.execute(
                    "CREATE TABLE IF NOT EXISTS sage_smrt.schema_migrations ("
                    "version text PRIMARY KEY, checksum char(64) NOT NULL, "
                    "applied_at timestamptz NOT NULL DEFAULT now())"
                )
                row = connection.execute(
                    "SELECT checksum FROM sage_smrt.schema_migrations WHERE version = %s",
                    (EXPECTED_SCHEMA_VERSION,),
                ).fetchone()
                if row is not None:
                    if row[0] != checksum:
                        raise MemoryStorageError(
                            "An applied memory migration has a changed checksum."
                        )
                    return EXPECTED_SCHEMA_VERSION
                connection.execute(migration)
                connection.execute(
                    "INSERT INTO sage_smrt.schema_migrations(version, checksum) "
                    "VALUES (%s, %s)",
                    (EXPECTED_SCHEMA_VERSION, checksum),
                )
        return EXPECTED_SCHEMA_VERSION
    except MemoryStorageError:
        raise
    except (psycopg.Error, ValueError) as error:
        raise MemoryStorageError("Unable to apply the memory migration.") from error


def doctor(settings: MemoryAdminSettings) -> dict[str, object]:
    """Check local capabilities and optional runtime storage without a model call."""

    result: dict[str, object] = {
        "memory_enabled": settings.enabled,
        "database_configured": settings.database_url is not None,
        "summarizer_configured": bool(
            settings.summarizer_provider and settings.summarizer_model
        ),
        "fts5": "ok" if SQLiteSparseIndex.available() else "unavailable",
    }
    try:
        result["tree_sitter"] = verify_grammars()
    except Exception:
        result["tree_sitter"] = "incompatible"
    if settings.database_url is None:
        result["postgres"] = "not_configured"
        return result
    try:
        with psycopg.connect(
            settings.database_url,
            connect_timeout=settings.db_timeout_seconds,
        ) as connection:
            row = connection.execute(
                "SELECT version FROM sage_smrt.schema_migrations "
                "WHERE version = %s",
                (EXPECTED_SCHEMA_VERSION,),
            ).fetchone()
            result["postgres"] = "ok" if row else "schema_outdated"
            permission = connection.execute(
                "SELECT has_schema_privilege(current_user, 'sage_smrt', 'USAGE')"
            ).fetchone()
            result["runtime_schema_access"] = bool(permission and permission[0])
    except (psycopg.Error, ValueError):
        result["postgres"] = "unreachable"
    return result


async def inspect(
    settings: MemoryAdminSettings,
    identity: RepositoryIdentity,
) -> dict[str, object]:
    if settings.database_url is None:
        raise MemoryConfigurationError(
            "SAGE_MEMORY_DATABASE_URL is required for memory inspection."
        )
    connections = MemoryConnectionPool(
        settings.database_url,
        timeout_seconds=settings.db_timeout_seconds,
        max_size=1,
    )
    try:
        await connections.open()
        store = PostgresMemoryStore(connections)
        await store.verify_schema()
        return await store.inspect_repository(identity)
    finally:
        await connections.close()


def _bool(value: str) -> bool:
    normalized = value.strip().lower()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off", ""}:
        return False
    raise MemoryConfigurationError("SAGE_MEMORY_ENABLED must be true or false.")
