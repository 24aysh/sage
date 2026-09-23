"""Memory remains local even when obsolete embedding configuration is present."""

import socket

import pytest

from sage.cli.app import main
from sage.composition import build_legion_memory_service
from sage.errors import LegionMemoryBuildError
from sage.harness.memory.locking import memory_lock


def test_graph_build_and_retrieval_ignore_old_provider_configuration(fixture_repo, tmp_path, monkeypatch):
    for key in ("OPENAI_API_KEY", "GEMINI_API_KEY", "TYPESAFE_API_KEY"):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("SAGE_LEGION_EMBEDDINGS_ENABLED", "true")
    monkeypatch.setenv("SAGE_LEGION_QDRANT_URL", "invalid-but-unused")

    def reject_network(*args, **kwargs):
        raise AssertionError("Graph memory must never open a network connection")

    monkeypatch.setattr(socket, "create_connection", reject_network)
    service = build_legion_memory_service()
    database = tmp_path / "graph.sqlite3"
    build = service.build_or_update_graph_tool(repo_root=fixture_repo, memory_file=database)
    assert build.status.value == "ready"
    assert "vectors" not in build.model_dump()
    retrieved = service.retrieve_issue_context(issue_text="Fix `helper`.", repo_root=fixture_repo, memory_file=database)
    assert retrieved.status.value == "used"
    assert "semantic" not in retrieved.search_modes
    assert retrieved.ranking_duration_ms == retrieved.duration_ms
    assert service.build_or_update_graph_tool(repo_root=fixture_repo, memory_file=database).build_type.value == "no_change"
    assert not (tmp_path / "qdrant").exists()


def test_build_lock_fails_fast_then_releases(tmp_path):
    database = tmp_path / "graph.sqlite3"
    with memory_lock(database):
        with pytest.raises(LegionMemoryBuildError, match="busy"):
            with memory_lock(database):
                pytest.fail("Second writer entered the lock")
    with memory_lock(database):
        pass


def test_removed_embedding_cli_flag_is_rejected(tmp_path):
    with pytest.raises(SystemExit) as failure:
        main(["memory", "build", "--repo", str(tmp_path), "--embeddings", "on"])
    assert failure.value.code == 2
