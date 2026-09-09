from pathlib import Path
import json

from sage.cli import app as cli, memory as cli_memory
from sage.cli.output import _render_memory_retrieval
from sage.domain.memory import (
    MemoryBuildResult, MemoryBuildType, MemoryGraphStats, MemoryRetrievalItem,
    MemoryRetrievalOutcome, MemoryRetrievalResult, MemoryRetrievalStatus, MemoryStatus,
)


def test_memory_build_arguments_and_output(
    monkeypatch,
    tmp_path: Path,
    capsys,
) -> None:
    memory_file = tmp_path / "graph.sqlite3"
    result = MemoryBuildResult(
        build_type=MemoryBuildType.FULL,
        memory_file=memory_file,
        repository_id="repository-id",
        indexed_sha="a" * 40,
        schema_version=1,
        files_indexed=2,
        files_parsed=2,
        files_removed=0,
        total_nodes=5,
        total_edges=4,
        total_flows=1,
        total_communities=1,
        languages=("python",),
        duration_ms=12.5,
    )

    class FakeService:
        def build_or_update_graph_tool(self, **arguments):
            assert arguments == {
                "repo_root": tmp_path,
                "memory_file": memory_file,
                "full_rebuild": True,
            }
            return result

    monkeypatch.setattr(cli_memory, "build_legion_memory_service", lambda: FakeService())

    exit_code = cli.main(
        [
            "memory",
            "build",
            "--repo",
            str(tmp_path),
            "--memory-file",
            str(memory_file),
            "--full-rebuild",
        ]
    )

    assert exit_code == 0
    output = capsys.readouterr().out
    assert "Legion Memory build: ready" in output
    assert "Build type: full" in output
    assert f"Memory file: {memory_file}" in output


def test_memory_status_reports_missing_without_model_configuration(
    monkeypatch,
    tmp_path: Path,
    capsys,
) -> None:
    stats = MemoryGraphStats(
        status=MemoryStatus.MISSING,
        memory_file=tmp_path / "missing.sqlite3",
    )

    class FakeService:
        def graph_stats(self, **arguments):
            assert arguments["repo_root"] == tmp_path
            return stats

    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.setattr(cli_memory, "build_legion_memory_service", lambda: FakeService())

    exit_code = cli.main(["memory", "status", "--repo", str(tmp_path)])

    assert exit_code == 1
    assert "Legion Memory status: missing" in capsys.readouterr().out


def test_memory_retrieve_prints_usage_and_ranked_memories(
    monkeypatch,
    tmp_path: Path,
    capsys,
) -> None:
    issue_file = tmp_path / "issue.md"
    issue_file.write_text("Fix `helper`.\n", encoding="utf-8")
    memory_file = tmp_path / "graph.sqlite3"
    memory_file.touch()
    result = MemoryRetrievalResult(
        status=MemoryRetrievalStatus.USED,
        outcome=MemoryRetrievalOutcome.USEFUL_CONTEXT,
        summary="Retrieved one relevant symbol.",
        memory_file=memory_file,
        repository_id="repository-id",
        indexed_sha="a" * 40,
        search_modes=("exact", "fts"),
        query_terms=("helper",),
        lexical_candidates=1,
        total_candidates=1,
        returned=1,
        context="bounded context",
        context_chars=15,
        items=(
            MemoryRetrievalItem(
                rank=1,
                kind="Function",
                name="helper",
                qualified_name="service.py::helper",
                file_path="service.py",
                line_start=8,
                line_end=9,
                language="python",
                score=16.25,
                reasons=("exact_identifier", "fts"),
            ),
        ),
        duration_ms=1.25,
    )

    class FakeService:
        def retrieve_issue_context(self, **arguments):
            assert arguments == {
                "issue_text": "Fix `helper`.\n",
                "repo_root": tmp_path,
                "memory_file": memory_file,
            }
            return result

    monkeypatch.setattr(cli_memory, "build_legion_memory_service", lambda: FakeService())

    exit_code = cli.main(
        [
            "memory",
            "retrieve",
            "--repo",
            str(tmp_path),
            "--issue-file",
            str(issue_file),
            "--memory-file",
            str(memory_file),
        ]
    )

    assert exit_code == 0
    output = capsys.readouterr().out
    assert "Legion Memory retrieval: used" in output
    assert "Memory used: yes" in output
    assert "service.py::helper" in output
    assert "Why: exact_identifier, fts" in output
    context_file = tmp_path / "graph.context.md"
    assert f"Context file: {context_file}" in output
    saved_context = context_file.read_text(encoding="utf-8")
    assert "# Legion Memory retrieved context" in saved_context
    assert "Graph-derived navigation context" in saved_context
    assert f"- Issue file: `{issue_file}`" in saved_context
    assert "- Status: `used`" in saved_context
    assert "- Context characters: 15" in saved_context
    assert "bounded context" in saved_context
    diagnostic = json.loads((tmp_path / "graph.retrieval.json").read_text())
    assert diagnostic["context"] == "bounded context"


def test_memory_retrieve_context_replaces_a_stale_result(tmp_path: Path) -> None:
    memory_file = tmp_path / "graph.sqlite3"
    context_file = tmp_path / "graph.context.md"
    context_file.write_text("stale memory\n", encoding="utf-8")
    result = MemoryRetrievalResult(
        status=MemoryRetrievalStatus.NO_MATCH,
        outcome=MemoryRetrievalOutcome.NO_LEXICAL_CANDIDATES,
        summary="The graph is ready, but the Issue produced no lexical matches.",
        memory_file=memory_file,
        indexed_sha="a" * 40,
    )

    written = cli_memory._write_memory_retrieval_context(
        result,
        issue_file=tmp_path / "issue.md",
    )

    assert written == context_file
    saved_context = written.read_text(encoding="utf-8")
    assert "stale memory" not in saved_context
    assert "- Status: `no_match`" in saved_context
    assert "_No Issue-relevant context was retrieved._" in saved_context


def test_memory_retrieve_prints_explicit_no_match(capsys, tmp_path: Path) -> None:
    result = MemoryRetrievalResult(
        status=MemoryRetrievalStatus.NO_MATCH,
        outcome=MemoryRetrievalOutcome.NO_LEXICAL_CANDIDATES,
        summary="The graph is ready, but the Issue produced no lexical matches.",
        memory_file=tmp_path / "graph.sqlite3",
        indexed_sha="a" * 40,
        search_modes=("none",),
        query_terms=("quasarnebulazxq",),
        duration_ms=0.5,
    )

    _render_memory_retrieval(result)

    output = capsys.readouterr().out
    assert "Legion Memory retrieval: no_match" in output
    assert "Memory used: no" in output
    assert "Retrieved memories:" not in output
