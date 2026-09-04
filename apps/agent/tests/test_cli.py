from pathlib import Path

import sage.cli as cli
from sage.cli import _build_parser, _render_result
from sage.config import Settings
from sage.domain.memory import (
    MemoryBuildResult,
    MemoryBuildType,
    MemoryGraphStats,
    MemoryRetrievalItem,
    MemoryRetrievalOutcome,
    MemoryRetrievalResult,
    MemoryRetrievalStatus,
    MemoryStatus,
)
from sage.domain.solve import SolveOutcome, SolveResult
from sage.integrations.github.models import GateOutcome, GateResult
from sage.workflows.github import GitHubWorkflowOutcome, GitHubWorkflowResult


def test_cli_uses_sage_name(capsys, tmp_path: Path) -> None:
    parser = _build_parser()
    result = SolveResult(
        run_id="run-id",
        base_sha="a" * 40,
        summary="No change required.",
        remaining_uncertainty=[],
        changed_files=[],
        diff="",
        run_dir=tmp_path,
        workspace_dir=tmp_path / "repo",
    )

    _render_result(result, model="test-model")

    assert parser.prog == "sage"
    assert capsys.readouterr().out.startswith("Sage\n")


def test_local_solve_arguments_remain_compatible(tmp_path: Path) -> None:
    arguments = _build_parser().parse_args(
        [
            "solve",
            "--repo",
            str(tmp_path / "repo"),
            "--issue-file",
            str(tmp_path / "issue.md"),
            "--base-ref",
            "main",
            "--sandbox-image",
            "custom:test",
            "--debug",
        ]
    )

    assert arguments.command == "solve"
    assert arguments.repo == tmp_path / "repo"
    assert arguments.issue_file == tmp_path / "issue.md"
    assert arguments.base_ref == "main"
    assert arguments.sandbox_image == "custom:test"
    assert arguments.debug is True


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

    monkeypatch.setattr(cli, "build_legion_memory_service", lambda: FakeService())

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
    monkeypatch.setattr(cli, "build_legion_memory_service", lambda: FakeService())

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

    monkeypatch.setattr(cli, "build_legion_memory_service", lambda: FakeService())

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

    cli._render_memory_retrieval(result)

    output = capsys.readouterr().out
    assert "Legion Memory retrieval: no_match" in output
    assert "Memory used: no" in output
    assert "Retrieved memories:" not in output


def test_langsmith_traces_are_flushed_only_when_enabled(monkeypatch) -> None:
    calls: list[str] = []
    monkeypatch.setattr(cli, "wait_for_all_tracers", lambda: calls.append("flush"))
    monkeypatch.setenv("LANGSMITH_TRACING", "false")

    cli._flush_langsmith_traces()
    assert calls == []

    monkeypatch.setenv("LANGSMITH_TRACING", "true")
    cli._flush_langsmith_traces()
    assert calls == ["flush"]


def test_non_publishable_partial_diff_returns_exit_two(
    monkeypatch,
    tmp_path: Path,
) -> None:
    settings = Settings(
        openai_api_key="openai-test",
        gemini_api_key="gemini-test",
        google_model_context_approved=True,
    )
    result = SolveResult(
        run_id="run-id",
        base_sha="a" * 40,
        summary="Verification failed.",
        remaining_uncertainty=[],
        changed_files=["app.py"],
        diff="diff --git a/app.py b/app.py\n",
        run_dir=tmp_path,
        workspace_dir=tmp_path / "repo",
        outcome=SolveOutcome.VERIFICATION_FAILED,
    )
    monkeypatch.setattr(cli.Settings, "from_env", lambda: settings)
    monkeypatch.setattr(cli, "_validate_prerequisites", lambda *args, **kwargs: None)
    monkeypatch.setattr(cli, "build_orchestrator", lambda value: object())

    async def fake_solve(request, orchestrator, effective_settings):
        return result

    monkeypatch.setattr(cli, "solve_issue", fake_solve)
    arguments = _build_parser().parse_args(
        [
            "solve",
            "--repo",
            str(tmp_path / "repo"),
            "--issue-file",
            str(tmp_path / "issue.md"),
        ]
    )

    assert cli._run_local_solve(arguments) == 2


def test_github_gate_does_not_require_model_configuration(
    monkeypatch,
    tmp_path: Path,
    capsys,
) -> None:
    fixture = (
        Path(__file__).parent / "fixtures" / "github" / "issue_solve.json"
    )
    output_path = tmp_path / "github-output"
    fake_client = object()
    result = GateResult(
        outcome=GateOutcome.ACCEPTED,
        should_run=True,
        base_sha="a" * 40,
        base_branch="main",
        issue_number=17,
        status_comment_id=7001,
    )
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.setenv("SAGE_GITHUB_TOKEN", "test-github-token")
    monkeypatch.setenv("GITHUB_EVENT_NAME", "issue_comment")
    monkeypatch.setenv("GITHUB_REPOSITORY", "24aysh/example")
    monkeypatch.setenv("GITHUB_RUN_ATTEMPT", "1")
    monkeypatch.setenv("GITHUB_RUN_ID", "9001")
    monkeypatch.setenv("GITHUB_SERVER_URL", "https://github.com")
    monkeypatch.setenv("GITHUB_SHA", "a" * 40)
    monkeypatch.setattr(cli, "RestGitHubClient", lambda settings: fake_client)

    def fake_evaluate_gate(invocation, client, *, max_comment_pages):
        assert client is fake_client
        assert invocation.issue.number == 17
        assert max_comment_pages == 5
        return result

    monkeypatch.setattr(cli, "evaluate_gate", fake_evaluate_gate)

    exit_code = cli.main(
        [
            "github",
            "gate",
            "--event-file",
            str(fixture),
            "--output-file",
            str(output_path),
        ]
    )

    assert exit_code == 0
    assert "should_run=true" in output_path.read_text(encoding="utf-8")
    assert capsys.readouterr().out == "GitHub gate outcome: accepted\n"


def test_github_event_check_classifies_fixture_without_credentials(
    monkeypatch,
    capsys,
) -> None:
    fixture = Path(__file__).parent / "fixtures" / "github" / "issue_solve.json"
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("SAGE_GITHUB_TOKEN", raising=False)

    exit_code = cli.main(
        ["github", "event-check", "--event-file", str(fixture)]
    )

    assert exit_code == 0
    assert capsys.readouterr().out == (
        "GitHub event classification: supported_solve\n"
    )


def test_github_publication_smoke_runs_without_credentials(
    monkeypatch,
    tmp_path: Path,
    capsys,
) -> None:
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.delenv("SAGE_GITHUB_TOKEN", raising=False)

    exit_code = cli.main(
        [
            "github",
            "publication-smoke",
            "--output-dir",
            str(tmp_path / "publication-smoke"),
            "--issue-number",
            "9",
        ]
    )

    assert exit_code == 0
    output = capsys.readouterr().out
    assert "GitHub publication smoke: passed" in output
    assert "Sage branch: sage/issue-9" in output
    assert "Draft PR requested: true" in output
    assert "Model calls: 0" in output
    assert "Network calls: 0" in output


def test_github_solve_wires_runner_paths_and_status_id(
    monkeypatch,
    tmp_path: Path,
    capsys,
) -> None:
    fixture = Path(__file__).parent / "fixtures" / "github" / "issue_solve.json"
    fake_client = object()
    _github_environment(monkeypatch, fixture)
    monkeypatch.setenv("SAGE_GITHUB_TOKEN", "github-token")
    monkeypatch.setenv("OPENAI_API_KEY", "model-token")
    monkeypatch.setenv("GEMINI_API_KEY", "reviewer-token")
    monkeypatch.setattr(cli, "RestGitHubClient", lambda settings: fake_client)

    async def fake_run(invocation, client, settings, **kwargs):
        assert client is fake_client
        assert kwargs["target_checkout"] == tmp_path / "target"
        assert kwargs["context_dir"] == tmp_path / "context"
        assert kwargs["diagnostics_dir"] == tmp_path / "diagnostics"
        assert kwargs["runner_temp"] == tmp_path / "runner"
        assert kwargs["status_comment_id"] == 7001
        loaded = kwargs["settings_factory"]()
        assert loaded.openai_api_key == "model-token"
        return GitHubWorkflowResult(outcome=GitHubWorkflowOutcome.NO_CHANGES)

    monkeypatch.setattr(cli, "run_github_issue", fake_run)

    exit_code = cli.main(
        [
            "github",
            "solve",
            "--event-file",
            str(fixture),
            "--target-checkout",
            str(tmp_path / "target"),
            "--context-dir",
            str(tmp_path / "context"),
            "--diagnostics-dir",
            str(tmp_path / "diagnostics"),
            "--runner-temp",
            str(tmp_path / "runner"),
            "--status-comment-id",
            "7001",
        ]
    )

    assert exit_code == 0
    assert capsys.readouterr().out == "GitHub solve outcome: no_changes\n"


def test_github_finalizer_does_not_require_model_configuration(
    monkeypatch,
    capsys,
) -> None:
    fixture = Path(__file__).parent / "fixtures" / "github" / "issue_solve.json"
    fake_client = object()
    _github_environment(monkeypatch, fixture)
    monkeypatch.setenv("SAGE_GITHUB_TOKEN", "github-token")
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.setattr(cli, "RestGitHubClient", lambda settings: fake_client)
    calls: list[int] = []

    def fake_finalize(invocation, client, *, max_comment_pages):
        assert client is fake_client
        calls.append(max_comment_pages)

    monkeypatch.setattr(cli, "finalize_github_issue", fake_finalize)

    exit_code = cli.main(
        ["github", "finalize", "--event-file", str(fixture)]
    )

    assert exit_code == 0
    assert calls == [5]
    assert capsys.readouterr().out == "GitHub finalizer completed.\n"


def _github_environment(monkeypatch, fixture: Path) -> None:
    monkeypatch.setenv("GITHUB_EVENT_NAME", "issue_comment")
    monkeypatch.setenv("GITHUB_EVENT_PATH", str(fixture))
    monkeypatch.setenv("GITHUB_REPOSITORY", "24aysh/example")
    monkeypatch.setenv("GITHUB_RUN_ATTEMPT", "1")
    monkeypatch.setenv("GITHUB_RUN_ID", "9001")
    monkeypatch.setenv("GITHUB_SERVER_URL", "https://github.com")
    monkeypatch.setenv("GITHUB_SHA", "a" * 40)
