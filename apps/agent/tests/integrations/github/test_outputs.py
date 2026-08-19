from pathlib import Path

import pytest

from sage.errors import GitHubConfigurationError
from sage.integrations.github.gate_models import GateOutcome, GateResult
from sage.integrations.github.outputs import write_gate_outputs


def test_write_gate_outputs_appends_only_the_documented_safe_contract(
    tmp_path: Path,
) -> None:
    output_path = tmp_path / "github-output"
    output_path.write_text("prior=value\n", encoding="utf-8")
    result = _result()

    write_gate_outputs(result, output_path)

    assert output_path.read_text(encoding="utf-8") == (
        "prior=value\n"
        "should_run=true\n"
        f"base_sha={'a' * 40}\n"
        "base_branch=main\n"
        "status_comment_id=7001\n"
        "issue_number=17\n"
        "existing_pr_url=\n"
    )


def test_output_writer_rejects_control_characters_even_for_unvalidated_model(
    tmp_path: Path,
) -> None:
    result = GateResult.model_construct(
        outcome=GateOutcome.ACCEPTED,
        should_run=True,
        base_sha="a" * 40,
        base_branch="main\nunsafe=true",
        issue_number=17,
        status_comment_id=7001,
        existing_pull_request_url=None,
    )

    with pytest.raises(GitHubConfigurationError, match="single-line"):
        write_gate_outputs(result, tmp_path / "github-output")


def test_output_writer_wraps_file_errors(tmp_path: Path) -> None:
    with pytest.raises(GitHubConfigurationError, match="Unable to write"):
        write_gate_outputs(_result(), tmp_path / "missing" / "output")


def _result() -> GateResult:
    return GateResult(
        outcome=GateOutcome.ACCEPTED,
        should_run=True,
        base_sha="a" * 40,
        base_branch="main",
        issue_number=17,
        status_comment_id=7001,
    )
