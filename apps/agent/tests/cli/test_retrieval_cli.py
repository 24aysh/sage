from pathlib import Path
import json

from sage.cli import app as cli, retrieval as cli_retrieval
from sage.cli.output import _render_retrieval
from sage.domain.retrieval import (
    IndexBuildResult, IndexBuildType, RepositoryGraphStats, RetrievalItem,
    RetrievalOutcome, RetrievalResult, RetrievalStatus, IndexStatus,
)


def test_retrieval_build_arguments_and_output(
    monkeypatch,
    tmp_path: Path,
    capsys,
) -> None:
    index_file = tmp_path / "graph.sqlite3"
    result = IndexBuildResult(
        build_type=IndexBuildType.FULL,
        index_file=index_file,
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
                "index_file": index_file,
                "full_rebuild": True,
            }
            return result

    monkeypatch.setattr(cli_retrieval, "build_retrieval_service", lambda: FakeService())

    exit_code = cli.main(
        [
            "retrieval",
            "build",
            "--repo",
            str(tmp_path),
            "--index-file",
            str(index_file),
            "--full-rebuild",
        ]
    )

    assert exit_code == 0
    output = capsys.readouterr().out
    assert "Repository retrieval index: ready" in output
    assert "Build type: full" in output
    assert f"Index file: {index_file}" in output


def test_retrieval_status_reports_missing_without_model_configuration(
    monkeypatch,
    tmp_path: Path,
    capsys,
) -> None:
    stats = RepositoryGraphStats(
        status=IndexStatus.MISSING,
        index_file=tmp_path / "missing.sqlite3",
    )

    class FakeService:
        def graph_stats(self, **arguments):
            assert arguments["repo_root"] == tmp_path
            return stats

    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.setattr(cli_retrieval, "build_retrieval_service", lambda: FakeService())

    exit_code = cli.main(["retrieval", "status", "--repo", str(tmp_path)])

    assert exit_code == 1
    assert "Repository retrieval index: missing" in capsys.readouterr().out


def test_retrieval_retrieve_prints_usage_and_ranked_items(
    monkeypatch,
    tmp_path: Path,
    capsys,
) -> None:
    issue_file = tmp_path / "issue.md"
    issue_file.write_text("Fix `helper`.\n", encoding="utf-8")
    index_file = tmp_path / "graph.sqlite3"
    index_file.touch()
    result = RetrievalResult(
        status=RetrievalStatus.USED,
        outcome=RetrievalOutcome.USEFUL_CONTEXT,
        summary="Retrieved one relevant symbol.",
        index_file=index_file,
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
            RetrievalItem(
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
            assert arguments.pop("budgets").max_chars == 12_000
            assert arguments == {
                "issue_text": "Fix `helper`.\n",
                "repo_root": tmp_path,
                "index_file": index_file,
            }
            return result

    monkeypatch.setattr(cli_retrieval, "build_retrieval_service", lambda: FakeService())

    exit_code = cli.main(
        [
            "retrieval",
            "retrieve",
            "--repo",
            str(tmp_path),
            "--issue-file",
            str(issue_file),
            "--index-file",
            str(index_file),
        ]
    )

    assert exit_code == 0
    output = capsys.readouterr().out
    assert "Repository retrieval: used" in output
    assert "Context used: yes" in output
    assert "service.py::helper" in output
    assert "Why: exact_identifier, fts" in output
    context_file = tmp_path / "graph.context.md"
    assert f"Context file: {context_file}" in output
    saved_context = context_file.read_text(encoding="utf-8")
    assert "# Repository retrieval context" in saved_context
    assert "Graph-derived navigation context" in saved_context
    assert f"- Issue file: `{issue_file}`" in saved_context
    assert "- Status: `used`" in saved_context
    assert "- Context characters: 15" in saved_context
    assert "bounded context" in saved_context
    diagnostic = json.loads((tmp_path / "graph.retrieval.json").read_text())
    assert diagnostic["context"] == "bounded context"


def test_retrieval_retrieve_context_replaces_a_stale_result(tmp_path: Path) -> None:
    index_file = tmp_path / "graph.sqlite3"
    context_file = tmp_path / "graph.context.md"
    context_file.write_text("stale retrieval\n", encoding="utf-8")
    result = RetrievalResult(
        status=RetrievalStatus.NO_MATCH,
        outcome=RetrievalOutcome.NO_LEXICAL_CANDIDATES,
        summary="The graph is ready, but the Issue produced no lexical matches.",
        index_file=index_file,
        indexed_sha="a" * 40,
    )

    written = cli_retrieval._write_retrieval_context(
        result,
        issue_file=tmp_path / "issue.md",
    )

    assert written == context_file
    saved_context = written.read_text(encoding="utf-8")
    assert "stale retrieval" not in saved_context
    assert "- Status: `no_match`" in saved_context
    assert "_No Issue-relevant context was retrieved._" in saved_context


def test_retrieval_retrieve_prints_explicit_no_match(capsys, tmp_path: Path) -> None:
    result = RetrievalResult(
        status=RetrievalStatus.NO_MATCH,
        outcome=RetrievalOutcome.NO_LEXICAL_CANDIDATES,
        summary="The graph is ready, but the Issue produced no lexical matches.",
        index_file=tmp_path / "graph.sqlite3",
        indexed_sha="a" * 40,
        search_modes=("none",),
        query_terms=("quasarnebulazxq",),
        duration_ms=0.5,
    )

    _render_retrieval(result)

    output = capsys.readouterr().out
    assert "Repository retrieval: no_match" in output
    assert "Context used: no" in output
    assert "Retrieved items:" not in output


def test_memory_command_and_flag_remain_deprecated_aliases(
    monkeypatch,
    tmp_path: Path,
) -> None:
    index_file = tmp_path / "graph.sqlite3"
    result = IndexBuildResult(
        build_type=IndexBuildType.NO_CHANGE,
        index_file=index_file,
        repository_id="repository-id",
        indexed_sha="a" * 40,
        schema_version=4,
        files_indexed=0,
        files_parsed=0,
        files_removed=0,
        total_nodes=0,
        total_edges=0,
        total_flows=0,
        total_communities=0,
        duration_ms=0,
    )

    class FakeService:
        def build_or_update_graph_tool(self, **arguments):
            assert arguments["index_file"] == index_file
            return result

    monkeypatch.setattr(cli_retrieval, "build_retrieval_service", lambda: FakeService())

    assert cli.main([
        "memory", "build", "--repo", str(tmp_path), "--memory-file", str(index_file)
    ]) == 0
