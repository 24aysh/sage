from pathlib import Path

import sage.cli as cli
from sage.cli import _build_parser, _render_result
from sage.domain.results import SolveResult
from sage.integrations.github.gate_models import GateOutcome, GateResult


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
