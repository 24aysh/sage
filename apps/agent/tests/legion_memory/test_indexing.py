from __future__ import annotations

import sqlite3
import subprocess
from pathlib import Path

import pytest

from sage.domain.memory import MemoryBuildType, MemoryStatus
from sage.errors import LegionMemoryBuildError, LegionMemoryQueryError
from sage.legion_memory.service import LegionMemoryService
from sage.legion_memory.store import GraphStore



def git(repository: Path, *arguments: str) -> str:
    result = subprocess.run(
        ["git", "-C", str(repository), *arguments],
        check=True,
        capture_output=True,
        text=True,
    )
    return result.stdout.strip()


def commit_all(repository: Path, message: str) -> str:
    git(repository, "add", "--all")
    git(repository, "commit", "-m", message)
    return git(repository, "rev-parse", "HEAD")


def test_build_handles_full_no_change_and_ignores_dirty_worktree(
    fixture_repo: Path,
    tmp_path: Path,
) -> None:
    service = LegionMemoryService(data_root=tmp_path / "memory")

    first = service.build_or_update_graph_tool(repo_root=fixture_repo)
    second = service.build_or_update_graph_tool(repo_root=fixture_repo)
    (fixture_repo / "service.py").write_text(
        "this dirty worktree content is not the accepted Git snapshot\n",
        encoding="utf-8",
    )
    third = service.build_or_update_graph_tool(repo_root=fixture_repo)

    assert first.build_type is MemoryBuildType.FULL
    assert first.files_parsed == 4
    assert first.total_nodes > first.files_indexed
    assert first.total_edges > 0
    assert second.build_type is MemoryBuildType.NO_CHANGE
    assert third.build_type is MemoryBuildType.NO_CHANGE
    assert first.indexed_sha == third.indexed_sha


def test_incremental_build_reconciles_change_add_rename_and_delete(
    fixture_repo: Path,
    tmp_path: Path,
) -> None:
    service = LegionMemoryService(data_root=tmp_path / "memory")
    first = service.build_or_update_graph_tool(repo_root=fixture_repo)
    (fixture_repo / "service.py").write_text(
        "def replacement():\n    return 'new'\n",
        encoding="utf-8",
    )
    (fixture_repo / "added.py").write_text(
        "def added():\n    return replacement()\n",
        encoding="utf-8",
    )
    (fixture_repo / "app.py").rename(fixture_repo / "entry.py")
    (fixture_repo / "web.ts").unlink()
    expected_sha = commit_all(fixture_repo, "update graph inputs")

    result = service.build_or_update_graph_tool(
        repo_root=fixture_repo,
        memory_file=first.memory_file,
    )

    assert result.build_type is MemoryBuildType.INCREMENTAL
    assert result.files_parsed == 3
    assert result.files_removed == 2
    assert result.indexed_sha == expected_sha
    with GraphStore(result.memory_file, read_only=True) as store:
        files = set(store.file_hashes())
        assert {"added.py", "entry.py", "service.py"} <= files
        assert "app.py" not in files
        assert "web.ts" not in files
        assert store.node("helper") is None
        assert store.node("replacement") is not None


def test_incremental_replacement_preserves_incoming_edges_to_stable_symbols(
    fixture_repo: Path,
    tmp_path: Path,
) -> None:
    service = LegionMemoryService(data_root=tmp_path / "memory")
    built = service.build_or_update_graph_tool(repo_root=fixture_repo)
    (fixture_repo / "service.py").write_text(
        "class Base:\n"
        "    pass\n\n"
        "class Worker(Base):\n"
        "    def run(self):\n"
        "        return helper()\n\n"
        "def helper():\n"
        "    return 84\n",
        encoding="utf-8",
    )
    commit_all(fixture_repo, "change stable helper implementation")

    service.build_or_update_graph_tool(
        repo_root=fixture_repo,
        memory_file=built.memory_file,
    )
    callers = service.query_graph_tool(
        pattern="callers_of",
        target="helper",
        repo_root=fixture_repo,
        memory_file=built.memory_file,
    )

    names = {item["name"] for item in callers["data"]["results"]}
    assert {"run", "test_helper"} <= names


def test_stale_and_foreign_graphs_are_rejected(
    fixture_repo: Path,
    tmp_path: Path,
) -> None:
    service = LegionMemoryService(data_root=tmp_path / "memory")
    built = service.build_or_update_graph_tool(repo_root=fixture_repo)
    (fixture_repo / "README.md").write_text("new commit\n", encoding="utf-8")
    commit_all(fixture_repo, "advance accepted sha")

    with pytest.raises(LegionMemoryQueryError, match="current Git SHA"):
        service.graph_stats(repo_root=fixture_repo, memory_file=built.memory_file)

    foreign = tmp_path / "foreign"
    foreign.mkdir()
    git(foreign, "init", "--initial-branch=main")
    git(foreign, "config", "user.name", "Sage Tests")
    git(foreign, "config", "user.email", "sage-tests@example.invalid")
    (foreign / "main.py").write_text("def foreign():\n    pass\n", encoding="utf-8")
    commit_all(foreign, "foreign fixture")
    with pytest.raises(LegionMemoryBuildError, match="different repository"):
        service.build_or_update_graph_tool(
            repo_root=foreign,
            memory_file=built.memory_file,
        )


def test_missing_graph_has_an_explicit_status(
    fixture_repo: Path,
    tmp_path: Path,
) -> None:
    service = LegionMemoryService(data_root=tmp_path / "memory")

    status = service.graph_stats(repo_root=fixture_repo)

    assert status.status is MemoryStatus.MISSING
    assert status.nodes == 0


def test_unavailable_incremental_base_forces_a_full_rebuild(
    fixture_repo: Path,
    tmp_path: Path,
) -> None:
    service = LegionMemoryService(data_root=tmp_path / "memory")
    built = service.build_or_update_graph_tool(repo_root=fixture_repo)
    with GraphStore(built.memory_file) as store:
        store.set_metadata("indexed_sha", "f" * 40)
        store.connection.commit()

    rebuilt = service.build_or_update_graph_tool(
        repo_root=fixture_repo,
        memory_file=built.memory_file,
    )

    assert rebuilt.build_type is MemoryBuildType.FULL
    assert rebuilt.files_parsed == rebuilt.files_indexed


def test_build_does_not_mutate_the_target_repository(
    fixture_repo: Path,
    tmp_path: Path,
) -> None:
    service = LegionMemoryService(data_root=tmp_path / "memory")
    before = git(fixture_repo, "status", "--short", "--untracked-files=all")

    service.build_or_update_graph_tool(repo_root=fixture_repo)

    assert git(fixture_repo, "status", "--short", "--untracked-files=all") == before


def test_local_workspace_clone_reuses_source_repository_identity(
    fixture_repo: Path,
    tmp_path: Path,
) -> None:
    service = LegionMemoryService(data_root=tmp_path / "memory")
    built = service.build_or_update_graph_tool(repo_root=fixture_repo)
    workspace = tmp_path / "workspace"
    subprocess.run(
        ["git", "clone", "--quiet", str(fixture_repo), str(workspace)],
        check=True,
    )

    updated = service.build_or_update_graph_tool(
        repo_root=workspace,
        memory_file=built.memory_file,
    )

    assert updated.repository_id == built.repository_id
    assert updated.indexed_sha == built.indexed_sha
    assert updated.build_type is MemoryBuildType.NO_CHANGE


def test_failed_postprocessing_preserves_the_previous_ready_graph(
    fixture_repo: Path,
    tmp_path: Path,
    monkeypatch,
) -> None:
    service = LegionMemoryService(data_root=tmp_path / "memory")
    built = service.build_or_update_graph_tool(repo_root=fixture_repo)
    with GraphStore(built.memory_file, read_only=True) as store:
        prior_nodes = store.stats()["nodes"]
    (fixture_repo / "service.py").write_text(
        "def changed():\n    return 7\n",
        encoding="utf-8",
    )
    commit_all(fixture_repo, "trigger failed update")

    def fail_postprocessing(self) -> None:
        raise sqlite3.OperationalError("forced postprocessing failure")

    monkeypatch.setattr(GraphStore, "_rebuild_flows", fail_postprocessing)
    with pytest.raises(LegionMemoryBuildError, match="forced postprocessing"):
        service.build_or_update_graph_tool(
            repo_root=fixture_repo,
            memory_file=built.memory_file,
        )

    with GraphStore(built.memory_file, read_only=True) as store:
        assert store.get_metadata("build_state") == "ready"
        assert store.get_metadata("indexed_sha") == built.indexed_sha
        assert store.stats()["nodes"] == prior_nodes
