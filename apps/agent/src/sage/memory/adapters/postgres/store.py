"""Domain-oriented PostgreSQL persistence for canonical SMRT memory."""

from __future__ import annotations

import asyncio
import hashlib
from collections.abc import Sequence
from functools import wraps
from importlib.resources import files
from typing import Any, Callable
from uuid import UUID, uuid4

from psycopg import Error as PsycopgError
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from sage.errors import MemoryIntegrityError, MemoryStorageError
from sage.memory.adapters.postgres.connection import MemoryConnectionPool
from sage.memory.canonical import canonical_digest
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
    SemanticState,
    Snapshot,
    SnapshotStatus,
)

EXPECTED_SCHEMA_VERSION = "0001_smrt_v1"


def _bounded(method: Callable[..., Any]) -> Callable[..., Any]:
    """Bound acquisition plus query time without relying on SQL session state."""

    @wraps(method)
    async def wrapped(self: Any, *args: Any, **kwargs: Any) -> Any:
        try:
            async with asyncio.timeout(float(self._timeout_seconds)):
                return await method(self, *args, **kwargs)
        except TimeoutError as error:
            raise MemoryStorageError(
                "A canonical memory operation exceeded its timeout."
            ) from error

    return wrapped


class PostgresMemoryStore:
    """Keep SQL private and expose only memory-domain operations."""

    def __init__(self, connection_pool: MemoryConnectionPool) -> None:
        self._connections = connection_pool
        self._timeout_seconds = connection_pool.timeout_seconds

    @_bounded
    async def verify_schema(self) -> None:
        try:
            async with self._connections.pool.connection() as connection:
                async with connection.cursor() as cursor:
                    await cursor.execute(
                        "SELECT checksum FROM sage_smrt.schema_migrations "
                        "WHERE version = %s",
                        (EXPECTED_SCHEMA_VERSION,),
                    )
                    row = await cursor.fetchone()
            migration = files("sage.memory.migrations").joinpath(
                f"{EXPECTED_SCHEMA_VERSION}.sql"
            ).read_bytes()
            expected_checksum = hashlib.sha256(migration).hexdigest()
            if row is None or row[0] != expected_checksum:
                raise MemoryStorageError("The canonical memory schema is outdated.")
        except MemoryStorageError:
            raise
        except PsycopgError as error:
            raise MemoryStorageError("Unable to verify the canonical memory schema.") from error

    @_bounded
    async def get_or_create_repository(
        self, identity: RepositoryIdentity
    ) -> RepositoryRecord:
        repository_id = uuid4()
        try:
            async with self._connections.pool.connection() as connection:
                async with connection.cursor(row_factory=dict_row) as cursor:
                    await cursor.execute(
                        "INSERT INTO sage_smrt.repositories "
                        "(repository_id, namespace_kind, namespace_key, display_name) "
                        "VALUES (%s, %s, %s, %s) "
                        "ON CONFLICT (namespace_kind, namespace_key) DO UPDATE SET "
                        "display_name = EXCLUDED.display_name, updated_at = now() "
                        "RETURNING repository_id, latest_ready_snapshot_id",
                        (
                            repository_id,
                            identity.namespace_kind,
                            identity.namespace_key,
                            identity.display_name,
                        ),
                    )
                    row = await cursor.fetchone()
            assert row is not None
            return RepositoryRecord(
                repository_id=row["repository_id"],
                identity=identity,
                latest_ready_snapshot_id=row["latest_ready_snapshot_id"],
            )
        except PsycopgError as error:
            raise MemoryStorageError("Unable to register the memory repository.") from error

    @_bounded
    async def load_latest_ready_snapshot(
        self, repository_id: UUID
    ) -> Snapshot | None:
        query = (
            "SELECT s.* FROM sage_smrt.repositories r "
            "JOIN sage_smrt.snapshots s ON s.repository_id = r.repository_id "
            "AND s.snapshot_id = r.latest_ready_snapshot_id "
            "WHERE r.repository_id = %s AND s.status = 'READY'"
        )
        try:
            async with self._connections.pool.connection() as connection:
                async with connection.cursor(row_factory=dict_row) as cursor:
                    await cursor.execute(query, (repository_id,))
                    row = await cursor.fetchone()
            return _snapshot(row) if row else None
        except PsycopgError as error:
            raise MemoryStorageError("Unable to load the latest memory snapshot.") from error

    @_bounded
    async def start_snapshot(
        self,
        *,
        repository_id: UUID,
        parent_snapshot_id: UUID | None,
        target_commit_oid: str,
        target_root_tree_oid: str,
        run_id: str,
    ) -> Snapshot:
        snapshot_id = uuid4()
        try:
            async with self._connections.pool.connection() as connection:
                async with connection.cursor(row_factory=dict_row) as cursor:
                    await cursor.execute(
                        "INSERT INTO sage_smrt.snapshots "
                        "(snapshot_id, repository_id, parent_snapshot_id, "
                        "target_commit_oid, target_root_tree_oid, status, run_id, schema_version) "
                        "VALUES (%s, %s, %s, %s, %s, 'BUILDING', %s, 'smrt-overlay-v1') "
                        "RETURNING *",
                        (
                            snapshot_id,
                            repository_id,
                            parent_snapshot_id,
                            target_commit_oid,
                            target_root_tree_oid,
                            run_id,
                        ),
                    )
                    row = await cursor.fetchone()
            assert row is not None
            return _snapshot(row)
        except PsycopgError as error:
            raise MemoryStorageError("Unable to start a memory snapshot.") from error

    @_bounded
    async def insert_semantic_object(
        self, repository_id: UUID, value: SemanticObject
    ) -> None:
        try:
            async with self._connections.pool.connection() as connection:
                async with connection.transaction():
                    async with connection.cursor(row_factory=dict_row) as cursor:
                        await cursor.execute(
                            "INSERT INTO sage_smrt.semantic_objects "
                            "(repository_id, semantic_digest, payload_digest, node_type, "
                            "source_oid, semantic_payload, structure, schema_version, "
                            "summarizer_provider, summarizer_model, prompt_version, "
                            "parser_version, generation_mode, delta_depth) "
                            "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s) "
                            "ON CONFLICT DO NOTHING RETURNING semantic_digest",
                            (
                                repository_id,
                                value.semantic_digest,
                                value.payload_digest,
                                value.node_type,
                                value.source_oid,
                                Jsonb(value.semantic_payload.model_dump(mode="json")),
                                Jsonb(value.structure.model_dump(mode="json"))
                                if value.structure
                                else None,
                                value.schema_version,
                                value.summarizer_provider,
                                value.summarizer_model,
                                value.prompt_version,
                                value.parser_version,
                                value.generation_mode,
                                value.delta_depth,
                            ),
                        )
                        inserted = await cursor.fetchone()
                        if inserted is None:
                            await cursor.execute(
                                "SELECT payload_digest, node_type, source_oid, semantic_payload, "
                                "structure, schema_version, summarizer_provider, summarizer_model, "
                                "prompt_version, parser_version, generation_mode, delta_depth "
                                "FROM sage_smrt.semantic_objects "
                                "WHERE repository_id = %s AND semantic_digest = %s",
                                (repository_id, value.semantic_digest),
                            )
                            existing = await cursor.fetchone()
                            if existing is None or not _same_semantic(existing, value):
                                raise MemoryIntegrityError(
                                    "A canonical semantic digest collision was detected."
                                )
                            await cursor.execute(
                                "SELECT child_name, child_digest FROM "
                                "sage_smrt.semantic_dependencies WHERE repository_id = %s "
                                "AND parent_digest = %s ORDER BY child_order",
                                (repository_id, value.semantic_digest),
                            )
                            dependencies = tuple(
                                (item["child_name"], item["child_digest"])
                                for item in await cursor.fetchall()
                            )
                            if dependencies != value.derived_from:
                                raise MemoryIntegrityError(
                                    "Canonical semantic dependencies do not match."
                                )
                        for order, (name, child_digest) in enumerate(value.derived_from):
                            await cursor.execute(
                                "INSERT INTO sage_smrt.semantic_dependencies "
                                "(repository_id, parent_digest, child_order, child_name, child_digest) "
                                "VALUES (%s, %s, %s, %s, %s) ON CONFLICT DO NOTHING",
                                (repository_id, value.semantic_digest, order, name, child_digest),
                            )
        except MemoryIntegrityError:
            raise
        except PsycopgError as error:
            raise MemoryStorageError("Unable to persist a semantic memory object.") from error

    @_bounded
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
    ) -> SemanticObject | None:
        try:
            async with self._connections.pool.connection() as connection:
                async with connection.cursor(row_factory=dict_row) as cursor:
                    await cursor.execute(
                        "SELECT * FROM sage_smrt.semantic_objects "
                        "WHERE repository_id = %s AND source_oid = %s AND node_type = %s "
                        "AND summarizer_provider = %s AND summarizer_model = %s "
                        "AND prompt_version = %s AND parser_version IS NOT DISTINCT FROM %s "
                        "ORDER BY created_at DESC LIMIT 1",
                        (
                            repository_id,
                            source_oid,
                            node_type,
                            summarizer_provider,
                            summarizer_model,
                            prompt_version,
                            parser_version,
                        ),
                    )
                    row = await cursor.fetchone()
                    dependencies: list[tuple[str, str]] = []
                    if row and node_type is NodeType.DIRECTORY:
                        await cursor.execute(
                            "SELECT child_name, child_digest FROM "
                            "sage_smrt.semantic_dependencies WHERE repository_id = %s "
                            "AND parent_digest = %s ORDER BY child_order",
                            (repository_id, row["semantic_digest"]),
                        )
                        dependencies = [
                            (item["child_name"], item["child_digest"])
                            for item in await cursor.fetchall()
                        ]
            return _semantic(row, tuple(dependencies)) if row else None
        except PsycopgError as error:
            raise MemoryStorageError("Unable to reuse semantic memory.") from error

    @_bounded
    async def insert_overlay_nodes(
        self, repository_id: UUID, values: Sequence[OverlayNode]
    ) -> None:
        try:
            async with self._connections.pool.connection() as connection:
                async with connection.transaction():
                    async with connection.cursor(row_factory=dict_row) as cursor:
                        for value in values:
                            await cursor.execute(
                                "INSERT INTO sage_smrt.overlay_nodes "
                                "(repository_id, overlay_digest, node_type, source_oid, "
                                "semantic_digest, stale_hint_digest, semantic_state, coverage_state) "
                                "VALUES (%s, %s, %s, %s, %s, %s, %s, %s) "
                                "ON CONFLICT DO NOTHING RETURNING overlay_digest",
                                (
                                    repository_id,
                                    value.overlay_digest,
                                    value.node_type,
                                    value.source_oid,
                                    value.semantic_digest,
                                    value.stale_hint_digest,
                                    value.semantic_state,
                                    value.coverage_state,
                                ),
                            )
                            inserted = await cursor.fetchone()
                            if inserted is None:
                                await cursor.execute(
                                    "SELECT node_type, source_oid, semantic_digest, "
                                    "stale_hint_digest, semantic_state, coverage_state "
                                    "FROM sage_smrt.overlay_nodes WHERE repository_id = %s "
                                    "AND overlay_digest = %s",
                                    (repository_id, value.overlay_digest),
                                )
                                existing = await cursor.fetchone()
                                if existing is None or not _same_overlay(existing, value):
                                    raise MemoryIntegrityError(
                                        "A canonical overlay digest collision was detected."
                                    )
                                await cursor.execute(
                                    "SELECT child_name, child_overlay_digest FROM "
                                    "sage_smrt.overlay_edges WHERE repository_id = %s "
                                    "AND parent_overlay_digest = %s ORDER BY child_order",
                                    (repository_id, value.overlay_digest),
                                )
                                children = tuple(
                                    (
                                        item["child_name"],
                                        item["child_overlay_digest"],
                                    )
                                    for item in await cursor.fetchall()
                                )
                                if children != value.children:
                                    raise MemoryIntegrityError(
                                        "Canonical overlay children do not match."
                                    )
                            for order, (name, child) in enumerate(value.children):
                                await cursor.execute(
                                    "INSERT INTO sage_smrt.overlay_edges "
                                    "(repository_id, parent_overlay_digest, child_name, "
                                    "child_overlay_digest, child_order) "
                                    "VALUES (%s, %s, %s, %s, %s) ON CONFLICT DO NOTHING",
                                    (repository_id, value.overlay_digest, name, child, order),
                                )
        except MemoryIntegrityError:
            raise
        except PsycopgError as error:
            raise MemoryStorageError("Unable to persist the sparse overlay.") from error

    @_bounded
    async def publish_snapshot(
        self,
        *,
        snapshot_id: UUID,
        repository_id: UUID,
        root_overlay_digest: str,
        expected_latest_id: UUID | None,
    ) -> Snapshot:
        try:
            async with self._connections.pool.connection() as connection:
                async with connection.transaction():
                    async with connection.cursor(row_factory=dict_row) as cursor:
                        await cursor.execute(
                            "SELECT latest_ready_snapshot_id FROM sage_smrt.repositories "
                            "WHERE repository_id = %s FOR UPDATE",
                            (repository_id,),
                        )
                        repository = await cursor.fetchone()
                        if repository is None or repository["latest_ready_snapshot_id"] != expected_latest_id:
                            raise MemoryIntegrityError(
                                "The memory snapshot base changed during publication."
                            )
                        await cursor.execute(
                            "UPDATE sage_smrt.snapshots SET root_overlay_digest = %s, "
                            "status = 'READY', ready_at = now() WHERE snapshot_id = %s "
                            "AND repository_id = %s AND status = 'BUILDING' RETURNING *",
                            (root_overlay_digest, snapshot_id, repository_id),
                        )
                        row = await cursor.fetchone()
                        if row is None:
                            raise MemoryIntegrityError(
                                "The memory snapshot was not publishable."
                            )
                        await cursor.execute(
                            "UPDATE sage_smrt.repositories SET latest_ready_snapshot_id = %s, "
                            "updated_at = now() WHERE repository_id = %s",
                            (snapshot_id, repository_id),
                        )
            return _snapshot(row)
        except MemoryIntegrityError:
            raise
        except PsycopgError as error:
            raise MemoryStorageError("Unable to publish the memory snapshot.") from error

    @_bounded
    async def mark_snapshot_failed(
        self, snapshot_id: UUID, *, failure_code: str
    ) -> None:
        try:
            async with self._connections.pool.connection() as connection:
                await connection.execute(
                    "UPDATE sage_smrt.snapshots SET status = 'FAILED', failure_code = %s "
                    "WHERE snapshot_id = %s AND status = 'BUILDING'",
                    (failure_code[:100], snapshot_id),
                )
        except PsycopgError as error:
            raise MemoryStorageError("Unable to close a failed memory snapshot.") from error

    @_bounded
    async def load_search_documents(
        self, repository_id: UUID, *, root_overlay_digest: str
    ) -> Sequence[SearchDocument]:
        query = """
            WITH RECURSIVE reachable AS (
                SELECT n.overlay_digest, n.node_type, n.source_oid, n.semantic_digest,
                       n.stale_hint_digest, n.semantic_state, ''::text AS path
                FROM sage_smrt.overlay_nodes n
                WHERE n.repository_id = %s AND n.overlay_digest = %s
                UNION ALL
                SELECT child.overlay_digest, child.node_type, child.source_oid, child.semantic_digest,
                       child.stale_hint_digest, child.semantic_state,
                       CASE WHEN parent.path = '' THEN edge.child_name
                            ELSE parent.path || '/' || edge.child_name END
                FROM reachable parent
                JOIN sage_smrt.overlay_edges edge
                  ON edge.repository_id = %s
                 AND edge.parent_overlay_digest = parent.overlay_digest
                JOIN sage_smrt.overlay_nodes child
                  ON child.repository_id = edge.repository_id
                 AND child.overlay_digest = edge.child_overlay_digest
            )
            SELECT CASE WHEN reachable.path = '' THEN '.' ELSE reachable.path END AS path,
                   reachable.node_type, reachable.source_oid,
                   reachable.semantic_digest, reachable.stale_hint_digest,
                   reachable.semantic_state,
                   semantic.payload_digest, semantic.semantic_payload,
                   semantic.structure, semantic.generation_mode,
                   semantic.delta_depth,
                   COALESCE((
                       SELECT jsonb_agg(
                           jsonb_build_array(dependency.child_name, dependency.child_digest)
                           ORDER BY dependency.child_order
                       )
                       FROM sage_smrt.semantic_dependencies dependency
                       WHERE dependency.repository_id = %s
                         AND dependency.parent_digest = semantic.semantic_digest
                   ), '[]'::jsonb) AS derived_from
            FROM reachable
            LEFT JOIN sage_smrt.semantic_objects semantic
              ON semantic.repository_id = %s
             AND semantic.semantic_digest = COALESCE(
                 reachable.semantic_digest, reachable.stale_hint_digest
             )
            ORDER BY reachable.path
        """
        try:
            async with self._connections.pool.connection() as connection:
                async with connection.cursor(row_factory=dict_row) as cursor:
                    await cursor.execute(
                        query,
                        (
                            repository_id,
                            root_overlay_digest,
                            repository_id,
                            repository_id,
                            repository_id,
                        ),
                    )
                    rows = await cursor.fetchall()
                    await _verify_reachable_objects(
                        cursor,
                        repository_id=repository_id,
                        root_overlay_digest=root_overlay_digest,
                    )
            return [_search_document(row) for row in rows]
        except MemoryIntegrityError:
            raise
        except PsycopgError as error:
            raise MemoryStorageError("Unable to load the sparse memory overlay.") from error

    @_bounded
    async def retain_latest_five(self, repository_id: UUID) -> int:
        """Retain five READY roots and collect objects unreachable from them."""

        try:
            async with self._connections.pool.connection() as connection:
                async with connection.transaction():
                    async with connection.cursor() as cursor:
                        await cursor.execute(
                            "SELECT snapshot_id FROM sage_smrt.snapshots "
                            "WHERE repository_id = %s AND status = 'READY' "
                            "ORDER BY ready_at DESC, snapshot_id DESC",
                            (repository_id,),
                        )
                        ready_ids = [row[0] for row in await cursor.fetchall()]
                        obsolete = ready_ids[5:]
                        if obsolete:
                            await cursor.execute(
                                "UPDATE sage_smrt.snapshots SET parent_snapshot_id = NULL "
                                "WHERE repository_id = %s AND parent_snapshot_id = ANY(%s)",
                                (repository_id, obsolete),
                            )
                            await cursor.execute(
                                "DELETE FROM sage_smrt.snapshots WHERE repository_id = %s "
                                "AND snapshot_id = ANY(%s)",
                                (repository_id, obsolete),
                            )
                        await cursor.execute(
                            "WITH RECURSIVE reachable(digest) AS ("
                            " SELECT root_overlay_digest FROM sage_smrt.snapshots"
                            " WHERE repository_id = %s AND status = 'READY'"
                            " UNION SELECT edge.child_overlay_digest"
                            " FROM reachable parent JOIN sage_smrt.overlay_edges edge"
                            " ON edge.repository_id = %s AND edge.parent_overlay_digest = parent.digest"
                            ") DELETE FROM sage_smrt.overlay_edges edge WHERE edge.repository_id = %s"
                            " AND edge.parent_overlay_digest NOT IN (SELECT digest FROM reachable)",
                            (repository_id, repository_id, repository_id),
                        )
                        await cursor.execute(
                            "WITH RECURSIVE reachable(digest) AS ("
                            " SELECT root_overlay_digest FROM sage_smrt.snapshots"
                            " WHERE repository_id = %s AND status = 'READY'"
                            " UNION SELECT edge.child_overlay_digest"
                            " FROM reachable parent JOIN sage_smrt.overlay_edges edge"
                            " ON edge.repository_id = %s AND edge.parent_overlay_digest = parent.digest"
                            ") DELETE FROM sage_smrt.overlay_nodes node WHERE node.repository_id = %s"
                            " AND node.overlay_digest NOT IN (SELECT digest FROM reachable)",
                            (repository_id, repository_id, repository_id),
                        )
                        await cursor.execute(
                            "WITH RECURSIVE reachable(digest) AS ("
                            " SELECT semantic_digest FROM sage_smrt.overlay_nodes"
                            " WHERE repository_id = %s AND semantic_digest IS NOT NULL"
                            " UNION SELECT stale_hint_digest FROM sage_smrt.overlay_nodes"
                            " WHERE repository_id = %s AND stale_hint_digest IS NOT NULL"
                            " UNION SELECT dependency.child_digest"
                            " FROM reachable parent JOIN sage_smrt.semantic_dependencies dependency"
                            " ON dependency.repository_id = %s"
                            " AND dependency.parent_digest = parent.digest"
                            ") DELETE FROM sage_smrt.semantic_dependencies dependency"
                            " WHERE dependency.repository_id = %s"
                            " AND dependency.parent_digest NOT IN (SELECT digest FROM reachable)",
                            (repository_id, repository_id, repository_id, repository_id),
                        )
                        await cursor.execute(
                            "WITH RECURSIVE reachable(digest) AS ("
                            " SELECT semantic_digest FROM sage_smrt.overlay_nodes"
                            " WHERE repository_id = %s AND semantic_digest IS NOT NULL"
                            " UNION SELECT stale_hint_digest FROM sage_smrt.overlay_nodes"
                            " WHERE repository_id = %s AND stale_hint_digest IS NOT NULL"
                            " UNION SELECT dependency.child_digest"
                            " FROM reachable parent JOIN sage_smrt.semantic_dependencies dependency"
                            " ON dependency.repository_id = %s"
                            " AND dependency.parent_digest = parent.digest"
                            ") DELETE FROM sage_smrt.semantic_objects semantic"
                            " WHERE semantic.repository_id = %s"
                            " AND semantic.semantic_digest NOT IN (SELECT digest FROM reachable)",
                            (repository_id, repository_id, repository_id, repository_id),
                        )
            return min(len(ready_ids), 5)
        except PsycopgError as error:
            raise MemoryStorageError("Unable to retain canonical memory snapshots.") from error

    @_bounded
    async def inspect_repository(
        self, identity: RepositoryIdentity
    ) -> dict[str, object]:
        try:
            async with self._connections.pool.connection() as connection:
                async with connection.cursor(row_factory=dict_row) as cursor:
                    await cursor.execute(
                        "SELECT repository_id, latest_ready_snapshot_id FROM sage_smrt.repositories "
                        "WHERE namespace_kind = %s AND namespace_key = %s",
                        (identity.namespace_kind, identity.namespace_key),
                    )
                    repository = await cursor.fetchone()
                    if repository is None:
                        return {"found": False, "ready_snapshots": 0}
                    await cursor.execute(
                        "SELECT count(*) AS ready_snapshots, max(target_commit_oid) "
                        "FILTER (WHERE snapshot_id = %s) AS latest_target "
                        "FROM sage_smrt.snapshots WHERE repository_id = %s AND status = 'READY'",
                        (repository["latest_ready_snapshot_id"], repository["repository_id"]),
                    )
                    counts = await cursor.fetchone()
                    await cursor.execute(
                        "SELECT count(*) AS semantic_objects FROM sage_smrt.semantic_objects "
                        "WHERE repository_id = %s",
                        (repository["repository_id"],),
                    )
                    semantic = await cursor.fetchone()
            return {
                "found": True,
                "ready_snapshots": int(counts["ready_snapshots"]),
                "latest_target": counts["latest_target"],
                "semantic_objects": int(semantic["semantic_objects"]),
            }
        except PsycopgError as error:
            raise MemoryStorageError("Unable to inspect canonical memory.") from error


def _snapshot(row: dict[str, object]) -> Snapshot:
    return Snapshot(
        snapshot_id=row["snapshot_id"],
        repository_id=row["repository_id"],
        parent_snapshot_id=row["parent_snapshot_id"],
        target_commit_oid=row["target_commit_oid"],
        target_root_tree_oid=row["target_root_tree_oid"],
        root_overlay_digest=row["root_overlay_digest"],
        status=SnapshotStatus(row["status"]),
        run_id=row["run_id"],
        created_at=row["created_at"],
        ready_at=row["ready_at"],
        schema_version=row["schema_version"],
        failure_code=row["failure_code"],
    )


def _same_semantic(row: dict[str, object], value: SemanticObject) -> bool:
    expected = {
        "payload_digest": value.payload_digest,
        "node_type": value.node_type.value,
        "source_oid": value.source_oid,
        "semantic_payload": value.semantic_payload.model_dump(mode="json"),
        "structure": value.structure.model_dump(mode="json") if value.structure else None,
        "schema_version": value.schema_version,
        "summarizer_provider": value.summarizer_provider,
        "summarizer_model": value.summarizer_model,
        "prompt_version": value.prompt_version,
        "parser_version": value.parser_version,
        "generation_mode": value.generation_mode,
        "delta_depth": value.delta_depth,
    }
    return all(row[key] == item for key, item in expected.items())


def _same_overlay(row: dict[str, object], value: OverlayNode) -> bool:
    return (
        row["node_type"] == value.node_type.value
        and row["source_oid"] == value.source_oid
        and row["semantic_digest"] == value.semantic_digest
        and row["stale_hint_digest"] == value.stale_hint_digest
        and row["semantic_state"] == value.semantic_state.value
        and row["coverage_state"] == (
            value.coverage_state.value if value.coverage_state else None
        )
    )


def _semantic(
    row: dict[str, object],
    dependencies: tuple[tuple[str, str], ...],
) -> SemanticObject:
    node_type = NodeType(row["node_type"])
    if node_type is NodeType.FILE:
        payload = FileSemanticPayload.model_validate(row["semantic_payload"])
        structure = FileStructure.model_validate(row["structure"])
    else:
        payload = DirectorySemanticPayload.model_validate(row["semantic_payload"])
        structure = None
    return SemanticObject(
        semantic_digest=row["semantic_digest"],
        payload_digest=row["payload_digest"],
        node_type=node_type,
        source_oid=row["source_oid"],
        semantic_payload=payload,
        structure=structure,
        derived_from=dependencies,
        schema_version=row["schema_version"],
        summarizer_provider=row["summarizer_provider"],
        summarizer_model=row["summarizer_model"],
        prompt_version=row["prompt_version"],
        parser_version=row["parser_version"],
        generation_mode=row["generation_mode"],
        delta_depth=row["delta_depth"],
    )


def _search_document(row: dict[str, object]) -> SearchDocument:
    payload = row["semantic_payload"] or {}
    structure = row["structure"] or {}
    return SearchDocument(
        path=row["path"],
        node_type=NodeType(row["node_type"]),
        source_oid=row["source_oid"],
        semantic_digest=row["semantic_digest"],
        stale_hint_digest=row["stale_hint_digest"],
        payload_digest=row["payload_digest"],
        summary=payload.get("summary", ""),
        responsibilities=tuple(payload.get("responsibilities", ())),
        not_responsible_for=tuple(payload.get("not_responsible_for", ())),
        concepts=tuple(payload.get("concepts", ())),
        symbols=tuple(structure.get("symbols", ())),
        imports=tuple(structure.get("imports", ())),
        derived_from=tuple(tuple(item) for item in row["derived_from"]),
        generation_mode=row["generation_mode"] or "full",
        delta_depth=row["delta_depth"] or 0,
        semantic_state=SemanticState(row["semantic_state"]),
    )


async def _verify_reachable_objects(
    cursor: Any,
    *,
    repository_id: UUID,
    root_overlay_digest: str,
) -> None:
    """Recompute canonical identities before trusting stored navigation data."""

    reachable = """
        WITH RECURSIVE reachable(digest) AS (
            SELECT %s::char(64)
            UNION
            SELECT edge.child_overlay_digest
            FROM reachable parent
            JOIN sage_smrt.overlay_edges edge
              ON edge.repository_id = %s
             AND edge.parent_overlay_digest = parent.digest
        )
    """
    await cursor.execute(
        reachable
        + """
        SELECT node.* FROM reachable
        JOIN sage_smrt.overlay_nodes node
          ON node.repository_id = %s AND node.overlay_digest = reachable.digest
        ORDER BY node.overlay_digest
        """,
        (root_overlay_digest, repository_id, repository_id),
    )
    overlay_rows = await cursor.fetchall()
    if not overlay_rows:
        raise MemoryIntegrityError("The memory snapshot root is missing.")
    await cursor.execute(
        "SELECT parent_overlay_digest, child_name, child_overlay_digest "
        "FROM sage_smrt.overlay_edges WHERE repository_id = %s "
        "AND parent_overlay_digest = ANY(%s) "
        "ORDER BY parent_overlay_digest, child_order",
        (repository_id, [row["overlay_digest"] for row in overlay_rows]),
    )
    children: dict[str, list[tuple[str, str]]] = {}
    for edge in await cursor.fetchall():
        children.setdefault(edge["parent_overlay_digest"], []).append(
            (edge["child_name"], edge["child_overlay_digest"])
        )
    semantic_digests: set[str] = set()
    for row in overlay_rows:
        semantic_digests.update(
            digest
            for digest in (row["semantic_digest"], row["stale_hint_digest"])
            if digest
        )
        envelope = {
            "node_type": row["node_type"],
            "source_oid": row["source_oid"],
            "semantic_digest": row["semantic_digest"],
            "stale_hint_digest": row["stale_hint_digest"],
            "semantic_state": row["semantic_state"],
            "coverage_state": row["coverage_state"],
            "children": tuple(children.get(row["overlay_digest"], ())),
        }
        if canonical_digest(envelope) != row["overlay_digest"]:
            raise MemoryIntegrityError("A stored overlay digest is invalid.")
    if not semantic_digests:
        return
    await cursor.execute(
        "SELECT * FROM sage_smrt.semantic_objects WHERE repository_id = %s "
        "AND semantic_digest = ANY(%s) ORDER BY semantic_digest",
        (repository_id, list(semantic_digests)),
    )
    semantic_rows = await cursor.fetchall()
    if {row["semantic_digest"] for row in semantic_rows} != semantic_digests:
        raise MemoryIntegrityError("A referenced semantic object is missing.")
    await cursor.execute(
        "SELECT parent_digest, child_name, child_digest FROM "
        "sage_smrt.semantic_dependencies WHERE repository_id = %s "
        "AND parent_digest = ANY(%s) ORDER BY parent_digest, child_order",
        (repository_id, list(semantic_digests)),
    )
    dependencies: dict[str, list[tuple[str, str]]] = {}
    for dependency in await cursor.fetchall():
        dependencies.setdefault(dependency["parent_digest"], []).append(
            (dependency["child_name"], dependency["child_digest"])
        )
    for row in semantic_rows:
        if canonical_digest(row["semantic_payload"]) != row["payload_digest"]:
            raise MemoryIntegrityError("A stored semantic payload digest is invalid.")
        envelope = {
            "payload_digest": row["payload_digest"],
            "node_type": row["node_type"],
            "source_oid": row["source_oid"],
            "semantic_payload": row["semantic_payload"],
            "structure": row["structure"],
            "derived_from": tuple(dependencies.get(row["semantic_digest"], ())),
            "schema_version": row["schema_version"],
            "summarizer_provider": row["summarizer_provider"],
            "summarizer_model": row["summarizer_model"],
            "prompt_version": row["prompt_version"],
            "parser_version": row["parser_version"],
            "generation_mode": row["generation_mode"],
            "delta_depth": row["delta_depth"],
        }
        if canonical_digest(envelope) != row["semantic_digest"]:
            raise MemoryIntegrityError("A stored semantic digest is invalid.")
