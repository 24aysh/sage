"""Allowlisted GitHub provenance artifacts for Actions diagnostics."""

from __future__ import annotations

from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field

from sage.artifacts.files import write_json_atomic, write_text_atomic
from sage.integrations.github.commands import SageCommand
from sage.integrations.github.models import GIT_OBJECT_ID_PATTERN, GitHubInvocation

_DIAGNOSTIC_FILES = (
    "metadata.json",
    "agent-final.json",
    "changed-files.json",
    "diff.patch",
    "usage.json",
    "terminal.json",
    "verification-summary.json",
    "review.json",
)


class GitHubProvenance(BaseModel):
    """Non-secret, bounded metadata about one GitHub invocation."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    repository: str = Field(min_length=3, max_length=140)
    repository_id: int = Field(gt=0)
    issue_number: int = Field(gt=0)
    invocation_comment_id: int = Field(gt=0)
    actor: str = Field(min_length=1, max_length=100)
    actions_run_id: int = Field(gt=0)
    actions_run_attempt: int = Field(gt=0)
    actions_run_url: str = Field(min_length=1, max_length=2_048)
    command: SageCommand
    base_branch: str = Field(min_length=1, max_length=255)
    original_base_sha: str = Field(pattern=GIT_OBJECT_ID_PATTERN)
    current_base_sha: str | None = Field(
        default=None,
        pattern=GIT_OBJECT_ID_PATTERN,
    )
    branch: str = Field(min_length=1, max_length=255)
    outcome: str = Field(min_length=1, max_length=100)
    local_run_id: str | None = Field(default=None, max_length=100)
    pull_request_number: int | None = Field(default=None, gt=0)
    pull_request_url: str | None = Field(default=None, max_length=2_048)


def build_github_provenance(
    invocation: GitHubInvocation,
    *,
    branch: str,
    outcome: str,
    current_base_sha: str | None = None,
    local_run_id: str | None = None,
    pull_request_number: int | None = None,
    pull_request_url: str | None = None,
) -> GitHubProvenance:
    """Build provenance only from validated invocation and result metadata."""

    if invocation.command is None:
        raise ValueError("GitHub provenance requires a supported command.")
    return GitHubProvenance(
        repository=invocation.repository.full_name,
        repository_id=invocation.repository.repository_id,
        issue_number=invocation.issue.number,
        invocation_comment_id=invocation.comment.comment_id,
        actor=invocation.actor.login,
        actions_run_id=invocation.actions_run.run_id,
        actions_run_attempt=invocation.actions_run.attempt,
        actions_run_url=invocation.actions_run.html_url,
        command=invocation.command,
        base_branch=invocation.default_branch,
        original_base_sha=invocation.base_sha,
        current_base_sha=current_base_sha,
        branch=branch,
        outcome=outcome,
        local_run_id=local_run_id,
        pull_request_number=pull_request_number,
        pull_request_url=pull_request_url,
    )


def persist_github_diagnostics(
    provenance: GitHubProvenance,
    *,
    diagnostics_dir: Path,
    run_dir: Path | None = None,
) -> Path:
    """Atomically write provenance and copy only allowlisted run artifacts."""

    destination = diagnostics_dir.expanduser().resolve()
    destination.mkdir(parents=True, exist_ok=True)
    provenance_path = destination / "github.json"
    write_json_atomic(provenance_path, provenance.model_dump(mode="json"))

    if run_dir is not None:
        source_root = run_dir.expanduser().resolve()
        run_provenance_path = source_root / "github.json"
        write_json_atomic(
            run_provenance_path,
            provenance.model_dump(mode="json"),
        )
        for name in _DIAGNOSTIC_FILES:
            source = source_root / name
            if source.is_file():
                write_text_atomic(
                    destination / name,
                    source.read_text(encoding="utf-8"),
                )
    return provenance_path
