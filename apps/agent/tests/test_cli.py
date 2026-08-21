from pathlib import Path

import sage.cli as cli
from sage.cli import _build_parser, _render_result
from sage.domain.results import SolveResult
from sage.integrations.github.gate_models import GateOutcome, GateResult
from sage.workflow.github_issue import GitHubWorkflowOutcome, GitHubWorkflowResult


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
    assert capsys.readouterr().out.startswith("Sage V0\n")


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
            "custom:v0",
            "--debug",
        ]
    )

    assert arguments.command == "solve"
    assert arguments.repo == tmp_path / "repo"
    assert arguments.issue_file == tmp_path / "issue.md"
    assert arguments.base_ref == "main"
    assert arguments.sandbox_image == "custom:v0"
    assert arguments.debug is True


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
