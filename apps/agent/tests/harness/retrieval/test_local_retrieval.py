"""Repository indexing and retrieval remain local and credential-free."""

import socket

import pytest

from sage.composition import build_retrieval_service
from sage.errors import RetrievalBuildError
from sage.harness.retrieval.locking import index_lock


def test_graph_build_and_retrieval_need_no_provider_or_network(fixture_repo, tmp_path, monkeypatch):
    for key in ("OPENAI_API_KEY", "GEMINI_API_KEY", "TYPESAFE_API_KEY"):
        monkeypatch.delenv(key, raising=False)
    def reject_network(*args, **kwargs):
        raise AssertionError("Repository retrieval must never open a network connection")

    monkeypatch.setattr(socket, "create_connection", reject_network)
    service = build_retrieval_service()
    database = tmp_path / "graph.sqlite3"
    build = service.build_or_update_graph_tool(repo_root=fixture_repo, index_file=database)
    assert build.status.value == "ready"
    retrieved = service.retrieve_issue_context(issue_text="Fix `helper`.", repo_root=fixture_repo, index_file=database)
    assert retrieved.status.value == "used"
    assert retrieved.ranking_duration_ms == retrieved.duration_ms
    assert service.build_or_update_graph_tool(repo_root=fixture_repo, index_file=database).build_type.value == "no_change"


def test_build_lock_fails_fast_then_releases(tmp_path):
    database = tmp_path / "graph.sqlite3"
    with index_lock(database):
        with pytest.raises(RetrievalBuildError, match="busy"):
            with index_lock(database):
                pytest.fail("Second writer entered the lock")
    with index_lock(database):
        pass
