"""Validated GitHub Actions output-file writing."""

from pathlib import Path

from sage.errors import GitHubConfigurationError
from sage.integrations.github.models import GateResult

_MAX_OUTPUT_VALUE_CHARS = 2_048


def write_gate_outputs(result: GateResult, output_path: Path) -> None:
    """Append the non-secret gate contract to GitHub's output file."""

    values = {
        "should_run": "true" if result.should_run else "false",
        "base_sha": result.base_sha,
        "base_branch": result.base_branch,
        "status_comment_id": (
            str(result.status_comment_id)
            if result.status_comment_id is not None
            else ""
        ),
        "issue_number": str(result.issue_number),
        "existing_pr_url": result.existing_pull_request_url or "",
    }
    lines = [f"{name}={_single_line(value)}\n" for name, value in values.items()]
    try:
        with output_path.open("a", encoding="utf-8") as output_file:
            output_file.writelines(lines)
    except OSError as error:
        raise GitHubConfigurationError(
            "Unable to write validated GitHub Actions outputs."
        ) from error


def _single_line(value: str) -> str:
    if len(value) > _MAX_OUTPUT_VALUE_CHARS or any(
        ord(character) < 32 or ord(character) == 127 for character in value
    ):
        raise GitHubConfigurationError(
            "A GitHub Actions output was not a bounded single-line value."
        )
    return value
