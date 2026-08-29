import asyncio
import os
from uuid import uuid4

import psycopg
import pytest

from sage.errors import MemoryIntegrityError
from sage.memory.adapters.postgres.connection import MemoryConnectionPool
from sage.memory.adapters.postgres.store import PostgresMemoryStore
from sage.memory.admin import MemoryAdminSettings, migrate
from sage.memory.canonical import canonical_digest
from sage.memory.models import (
    CoverageState,
    DirectorySemanticPayload,
    FileSemanticPayload,
    FileStructure,
    NodeType,
    OverlayNode,
    RepositoryIdentity,
    SemanticState,
)
from sage.memory.summarizer import (
    PROMPT_VERSION,
    build_directory_semantic_object,
    build_file_semantic_object,
)


@pytest.mark.memory_postgres
def test_migration_repository_and_building_snapshot_lifecycle() -> None:
    asyncio.run(_exercise_postgres_lifecycle())


async def _exercise_postgres_lifecycle() -> None:
    dsn = os.environ.get("SAGE_MEMORY_TEST_DATABASE_URL", "")
    if not dsn:
        pytest.skip("SAGE_MEMORY_TEST_DATABASE_URL is not configured")
    if "sage_memory_test" not in dsn:
        pytest.fail("Integration tests require the named disposable test database")
    with psycopg.connect(dsn) as connection:
        connection.execute("DROP SCHEMA IF EXISTS sage_smrt CASCADE")
    settings = MemoryAdminSettings(
        database_url=dsn,
        migration_database_url=dsn,
    )
    assert migrate(settings) == "0001_smrt_v1"
    assert migrate(settings) == "0001_smrt_v1"

    connections = MemoryConnectionPool(dsn, timeout_seconds=10, max_size=1)
    await connections.open()
    try:
        store = PostgresMemoryStore(connections)
        await store.verify_schema()
        repository = await store.get_or_create_repository(
            RepositoryIdentity(
                namespace_kind="local",
                namespace_key=f"test-{uuid4()}",
                display_name="integration test",
            )
        )
        file_semantic = build_file_semantic_object(
            source_oid="c" * 40,
            payload=FileSemanticPayload(summary="Stores memory"),
            structure=FileStructure(
                language="python",
                symbols=["MemoryStore"],
                parser_version="integration-parser",
                parse_status="parsed",
            ),
            provider="fake",
            model="fake-v1",
        )
        await store.insert_semantic_object(repository.repository_id, file_semantic)
        await store.insert_semantic_object(repository.repository_id, file_semantic)
        with psycopg.connect(dsn, autocommit=True) as connection:
            with pytest.raises(psycopg.Error, match="immutable"):
                connection.execute(
                    "UPDATE sage_smrt.semantic_objects SET source_oid = %s "
                    "WHERE repository_id = %s AND semantic_digest = %s",
                    (
                        "e" * 40,
                        repository.repository_id,
                        file_semantic.semantic_digest,
                    ),
                )
        reused_file = await store.find_semantic_by_source(
            repository.repository_id,
            source_oid=file_semantic.source_oid,
            node_type=NodeType.FILE,
            summarizer_provider="fake",
            summarizer_model="fake-v1",
            prompt_version=PROMPT_VERSION,
            parser_version="integration-parser",
        )
        assert reused_file == file_semantic
        directory_semantic = build_directory_semantic_object(
            source_oid="d" * 40,
            payload=DirectorySemanticPayload(summary="Owns stored memory"),
            children=[("store.py", file_semantic.semantic_digest)],
            provider="fake",
            model="fake-v1",
        )
        await store.insert_semantic_object(
            repository.repository_id, directory_semantic
        )
        reused_directory = await store.find_semantic_by_source(
            repository.repository_id,
            source_oid=directory_semantic.source_oid,
            node_type=NodeType.DIRECTORY,
            summarizer_provider="fake",
            summarizer_model="fake-v1",
            prompt_version=PROMPT_VERSION,
            parser_version=None,
        )
        assert reused_directory == directory_semantic
        with pytest.raises(MemoryIntegrityError, match="collision"):
            await store.insert_semantic_object(
                repository.repository_id,
                file_semantic.model_copy(update={"source_oid": "e" * 40}),
            )
        snapshot = await store.start_snapshot(
            repository_id=repository.repository_id,
            parent_snapshot_id=None,
            target_commit_oid="a" * 40,
            target_root_tree_oid="b" * 40,
            run_id="integration-run",
        )
        assert snapshot.status.value == "BUILDING"
        assert await store.load_latest_ready_snapshot(repository.repository_id) is None
        latest_id = None
        for sequence in range(6):
            current = snapshot if sequence == 0 else await store.start_snapshot(
                repository_id=repository.repository_id,
                parent_snapshot_id=latest_id,
                target_commit_oid=f"{sequence + 1:x}" * 40,
                target_root_tree_oid=f"{sequence + 2:x}" * 40,
                run_id=f"integration-run-{sequence}",
            )
            root_envelope = {
                "node_type": NodeType.DIRECTORY.value,
                "source_oid": current.target_root_tree_oid,
                "semantic_digest": None,
                "stale_hint_digest": None,
                "semantic_state": SemanticState.MISSING.value,
                "coverage_state": CoverageState.PARTIAL.value,
                "children": (),
            }
            root_digest = canonical_digest(root_envelope)
            root = OverlayNode(
                overlay_digest=root_digest,
                node_type=NodeType.DIRECTORY,
                source_oid=current.target_root_tree_oid,
                semantic_state=SemanticState.MISSING,
                coverage_state=CoverageState.PARTIAL,
            )
            await store.insert_overlay_nodes(repository.repository_id, [root])
            published = await store.publish_snapshot(
                snapshot_id=current.snapshot_id,
                repository_id=repository.repository_id,
                root_overlay_digest=root_digest,
                expected_latest_id=latest_id,
            )
            latest_id = published.snapshot_id
            assert await store.retain_latest_five(repository.repository_id) <= 5

        latest = await store.load_latest_ready_snapshot(repository.repository_id)
        assert latest is not None and latest.snapshot_id == latest_id
        documents = await store.load_search_documents(
            repository.repository_id,
            root_overlay_digest=latest.root_overlay_digest,
        )
        assert len(documents) == 1
        assert documents[0].path == "."
        assert documents[0].semantic_state is SemanticState.MISSING
        inspection = await store.inspect_repository(repository.identity)
        assert inspection["ready_snapshots"] == 5
        isolated = await store.inspect_repository(
            RepositoryIdentity(
                namespace_kind="local",
                namespace_key="another-repository",
                display_name="another",
            )
        )
        assert isolated == {"found": False, "ready_snapshots": 0}
    finally:
        await connections.close()
