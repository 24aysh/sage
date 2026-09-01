"""Narrow dependency ports used by the memory core."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Protocol
from uuid import UUID

from sage.memory.models import (
    DirectorySemanticPayload,
    FileSemanticPayload,
    FileStructure,
    NodeType,
    OverlayNode,
    RepositoryIdentity,
    RepositoryRecord,
    SearchDocument,
    SemanticObject,
    Snapshot,
)


class SemanticObjectStore(Protocol):
    async def insert_semantic_object(
        self, repository_id: UUID, value: SemanticObject
    ) -> None: ...

    async def find_semantic_by_source(
        self,
        repository_id: UUID,
        *,
        source_oid: str,
        node_type: NodeType,
        summarizer_provider: str,
        summarizer_model: str,
        prompt_version: str,
        parser_version: str | None,
    ) -> SemanticObject | None: ...


class SnapshotStore(SemanticObjectStore, Protocol):
    async def verify_schema(self) -> None: ...
    async def get_or_create_repository(
        self, identity: RepositoryIdentity
    ) -> RepositoryRecord: ...
    async def load_latest_ready_snapshot(
        self, repository_id: UUID
    ) -> Snapshot | None: ...
    async def start_snapshot(
        self,
        *,
        repository_id: UUID,
        parent_snapshot_id: UUID | None,
        target_commit_oid: str,
        target_root_tree_oid: str,
        run_id: str,
    ) -> Snapshot: ...
    async def insert_overlay_nodes(
        self, repository_id: UUID, values: Sequence[OverlayNode]
    ) -> None: ...
    async def publish_snapshot(
        self,
        *,
        snapshot_id: UUID,
        repository_id: UUID,
        root_overlay_digest: str,
        expected_latest_id: UUID | None,
    ) -> Snapshot: ...
    async def mark_snapshot_failed(
        self, snapshot_id: UUID, *, failure_code: str
    ) -> None: ...
    async def load_search_documents(
        self, repository_id: UUID, *, root_overlay_digest: str
    ) -> Sequence[SearchDocument]: ...
    async def retain_latest_five(self, repository_id: UUID) -> int: ...
    async def inspect_repository(
        self, identity: RepositoryIdentity
    ) -> dict[str, object]: ...


class GitObjectReader(Protocol):
    def root_tree_oid(self, commit_oid: str) -> str: ...
    def list_files(self, commit_oid: str) -> Sequence[tuple[str, str]]: ...
    def read_blob(self, commit_oid: str, path: str) -> tuple[str, str]: ...


class StructuralExtractor(Protocol):
    def extract(self, path: str, source: str) -> FileStructure: ...


class SemanticSummarizer(Protocol):
    provider_name: str
    model_name: str

    async def summarize_file(
        self, *, path: str, source: str, structure: FileStructure
    ) -> FileSemanticPayload: ...

    async def summarize_directory(
        self,
        *,
        path: str,
        children: Sequence[
            tuple[str, FileSemanticPayload | DirectorySemanticPayload]
        ],
    ) -> DirectorySemanticPayload: ...

    async def summarize_directory_delta(
        self,
        *,
        path: str,
        previous: DirectorySemanticPayload,
        changed_children: Sequence[
            tuple[str, FileSemanticPayload | DirectorySemanticPayload]
        ],
        removed_children: Sequence[str],
    ) -> DirectorySemanticPayload: ...


class SparseSearchBackend(Protocol):
    def rebuild(self, documents: Sequence[SearchDocument]) -> None: ...
    def search(self, query: str, *, limit: int) -> Sequence[tuple[str, float]]: ...
    def close(self) -> None: ...
