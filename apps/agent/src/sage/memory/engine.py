"""Composition and lifecycle of disabled or PostgreSQL-backed memory."""

from __future__ import annotations

import asyncio
import logging
from uuid import UUID

from sage.config import Settings
from sage.memory.adapters.postgres import MemoryConnectionPool, PostgresMemoryStore
from sage.memory.canonical import canonical_digest
from sage.memory.git_state import GitStateReader
from sage.memory.models import MemoryFailure, MemoryRunRequest
from sage.memory.parsing import TreeSitterExtractor
from sage.memory.retrieval.sparse import SQLiteSparseIndex
from sage.memory.session import (
    ActiveMemorySession,
    DisabledMemorySession,
    FallbackMemorySession,
)
from sage.memory.summarizer import ProviderSemanticSummarizer
from sage.providers.google import GoogleProvider
from sage.repository import RepositoryTools

logger = logging.getLogger(__name__)


class DisabledMemoryEngine:
    def __init__(self, repository: RepositoryTools) -> None:
        self._repository = repository

    async def begin(self, request: MemoryRunRequest) -> DisabledMemorySession:
        del request
        return DisabledMemorySession(self._repository)

    async def close(self) -> None:
        return None


class FailedMemoryEngine:
    """Delay a composition failure into the normal solve-local fallback path."""

    def __init__(self, repository: RepositoryTools, error: Exception) -> None:
        self._repository = repository
        self._error_code = type(error).__name__[:100]

    async def begin(self, request: MemoryRunRequest) -> FallbackMemorySession:
        return FallbackMemorySession(
            self._repository,
            identity=request.identity,
            target_commit=request.target_commit,
            failure=MemoryFailure(
                component="composition",
                stage="build",
                error_code=self._error_code,
                safe_message="SMRT could not be configured safely for this solve.",
                target_commit=request.target_commit,
            ),
        )

    async def close(self) -> None:
        return None


class PostgresMemoryEngine:
    """Open memory lazily and contain all expected/unexpected adapter failures."""

    def __init__(self, *, settings: Settings, repository: RepositoryTools) -> None:
        assert settings.memory_database_url is not None
        self._settings = settings
        self._repository = repository
        self._connections = MemoryConnectionPool(
            settings.memory_database_url,
            timeout_seconds=settings.memory_db_timeout_seconds,
        )
        self._store = PostgresMemoryStore(self._connections)
        google_key = settings.gemini_api_key
        if not google_key:
            raise ValueError("Memory summarization requires the configured Google key.")
        provider = GoogleProvider(
            api_key=google_key,
            model_name=settings.memory_summarizer_model,
            timeout_seconds=settings.memory_summarizer_timeout_seconds,
        )
        self._summarizer = ProviderSemanticSummarizer(
            provider,
            timeout_seconds=settings.memory_summarizer_timeout_seconds,
            max_retries=settings.memory_summarizer_max_retries,
        )
        self._opened = False

    async def begin(self, request: MemoryRunRequest):
        snapshot_id: UUID | None = None
        try:
            await self._connections.open()
            self._opened = True
            await self._store.verify_schema()
            repository = await self._store.get_or_create_repository(request.identity)
            latest = await self._store.load_latest_ready_snapshot(
                repository.repository_id
            )
            git = GitStateReader(request.workspace_path)
            root_tree_oid = git.root_tree_oid(request.target_commit)
            prior_documents = (
                list(
                    await self._store.load_search_documents(
                        repository.repository_id,
                        root_overlay_digest=latest.root_overlay_digest,
                    )
                )
                if latest and latest.root_overlay_digest
                else []
            )
            snapshot = await self._store.start_snapshot(
                repository_id=repository.repository_id,
                parent_snapshot_id=latest.snapshot_id if latest else None,
                target_commit_oid=request.target_commit,
                target_root_tree_oid=root_tree_oid,
                run_id=request.run_id,
            )
            snapshot_id = snapshot.snapshot_id
            index = SQLiteSparseIndex()
            index.rebuild(prior_documents)
            logger.info(
                "memory session started",
                extra={
                    "repository_identity": canonical_digest(request.identity)[:12],
                    "input_snapshot_id": str(latest.snapshot_id) if latest else None,
                    "target_commit": request.target_commit,
                },
            )
            return ActiveMemorySession(
                repository=self._repository,
                identity=request.identity,
                repository_id=repository.repository_id,
                target_commit=request.target_commit,
                workspace=request.workspace_path,
                git=git,
                store=self._store,
                summarizer=self._summarizer,
                extractor=TreeSitterExtractor(),
                index=index,
                prior_documents=prior_documents,
                input_snapshot_id=latest.snapshot_id if latest else None,
                building_snapshot_id=snapshot.snapshot_id,
                initial_max_files=self._settings.memory_initial_max_files,
                expansion_max_files=self._settings.memory_expansion_max_files,
                context_chars=self._settings.memory_context_chars,
                max_file_source_chars=self._settings.memory_max_file_source_chars,
                beam_width=self._settings.memory_beam_width,
                max_candidates_per_round=(
                    self._settings.memory_max_candidates_per_round
                ),
                max_navigation_rounds=self._settings.memory_max_navigation_rounds,
                parent_delta_limit=self._settings.memory_parent_delta_limit,
                parent_changed_child_limit=(
                    self._settings.memory_parent_changed_child_limit
                ),
            )
        except asyncio.CancelledError:
            raise
        except Exception as error:
            logger.warning(
                "memory startup entered fallback",
                extra={"stage": "begin", "error_code": type(error).__name__[:100]},
            )
            logger.debug("memory startup failure detail", exc_info=True)
            return FallbackMemorySession(
                self._repository,
                identity=request.identity,
                target_commit=request.target_commit,
                failure=MemoryFailure(
                    component="engine",
                    stage="begin",
                    error_code=type(error).__name__[:100],
                    safe_message="SMRT could not start safely for this solve.",
                    snapshot_id=snapshot_id,
                    target_commit=request.target_commit,
                ),
            )

    async def close(self) -> None:
        if not self._opened:
            return
        try:
            await self._connections.close()
        except Exception:
            logger.warning("memory connection pool did not close cleanly")
            logger.debug("memory pool close failure detail", exc_info=True)


def build_memory_engine(
    settings: Settings, repository: RepositoryTools
) -> DisabledMemoryEngine | PostgresMemoryEngine | FailedMemoryEngine:
    """Select the no-op or enabled engine at one composition boundary."""

    if not settings.memory_enabled:
        return DisabledMemoryEngine(repository)
    try:
        return PostgresMemoryEngine(settings=settings, repository=repository)
    except Exception as error:
        logger.warning(
            "memory composition entered fallback",
            extra={"stage": "build", "error_code": type(error).__name__[:100]},
        )
        logger.debug("memory composition failure detail", exc_info=True)
        return FailedMemoryEngine(repository, error)
