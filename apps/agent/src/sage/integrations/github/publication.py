"""GitHub publication policy and transaction coordination."""

from __future__ import annotations

from collections.abc import Callable
from enum import StrEnum
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field

from sage.domain.solve import SolveResult
from sage.errors import (
    GitHubApiError,
    GitHubOrphanBranchError,
    GitHubPublicationError,
)
from sage.integrations.github.api_models import GitHubPullRequestSnapshot
from sage.integrations.github.client import GitHubClient
from sage.integrations.github.git_publish import BaseMovement, prepare_git_publication
from sage.integrations.github.models import (
    GIT_OBJECT_ID_PATTERN,
    GitHubInvocation,
    GitHubRepository,
    issue_branch_name,
    validate_github_url,
)
_PULL_REQUEST_MARKER_PREFIX = "<!-- sage-pull-request:issue-"
_MAX_PR_TITLE_CHARS = 120
_MAX_SUMMARY_CHARS = 2_000
_MAX_UNCERTAINTY_ITEMS = 10
_MAX_UNCERTAINTY_CHARS = 500
_MAX_CHANGED_FILES = 500
_MAX_PATH_CHARS = 1_000


class PublicationOutcome(StrEnum):
    """Deterministic publisher outcomes."""

    NO_CHANGES = "no_changes"
    PULL_REQUEST_CREATED = "pull_request_created"


class PublicationResult(BaseModel):
    """Allowlisted result returned to GitHub solve orchestration."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    outcome: PublicationOutcome
    branch_name: str
    original_base_sha: str = Field(pattern=GIT_OBJECT_ID_PATTERN)
    current_base_sha: str = Field(pattern=GIT_OBJECT_ID_PATTERN)
    base_movement: BaseMovement
    pull_request_number: int | None = Field(default=None, gt=0)
    pull_request_url: str | None = Field(default=None, max_length=2_048)


RemoteUrlFactory = Callable[[GitHubRepository], str]


def publish_solve_result(
    invocation: GitHubInvocation,
    result: SolveResult,
    client: GitHubClient,
    *,
    github_token: str,
    runner_temp: Path,
    timeout_seconds: int = 60,
    remote_url_factory: RemoteUrlFactory | None = None,
) -> PublicationResult:
    """Validate, commit, creation-push, and open one draft Pull Request."""

    branch_name = issue_branch_name(invocation.issue.number)
    if branch_name == invocation.default_branch:
        raise GitHubPublicationError(
            "The deterministic publication branch cannot be the default branch."
        )
    if not result.diff.strip():
        return PublicationResult(
            outcome=PublicationOutcome.NO_CHANGES,
            branch_name=branch_name,
            original_base_sha=invocation.base_sha,
            current_base_sha=invocation.base_sha,
            base_movement=BaseMovement.UNCHANGED,
        )
    if not github_token:
        raise GitHubPublicationError("GitHub publication credentials are missing.")
    if timeout_seconds < 1:
        raise ValueError("Publication timeout must be positive.")

    _ensure_no_duplicate(
        invocation,
        client,
        branch_name=branch_name,
    )

    remote_url = (remote_url_factory or github_remote_url)(invocation.repository)
    with prepare_git_publication(
        invocation,
        result,
        github_token=github_token,
        runner_temp=runner_temp,
        remote_url=remote_url,
        branch_name=branch_name,
        timeout_seconds=timeout_seconds,
    ) as session:
        _ensure_no_duplicate(
            invocation,
            client,
            branch_name=branch_name,
        )
        session.push_creation_only()
        pull_request = _create_or_reconcile_pull_request(
            invocation,
            result,
            client,
            branch_name=branch_name,
            current_base_sha=session.current_base_sha,
            base_movement=session.base_movement,
        )

    return PublicationResult(
        outcome=PublicationOutcome.PULL_REQUEST_CREATED,
        branch_name=branch_name,
        original_base_sha=invocation.base_sha,
        current_base_sha=session.current_base_sha,
        base_movement=session.base_movement,
        pull_request_number=pull_request.number,
        pull_request_url=pull_request.html_url,
    )


def github_remote_url(repository: GitHubRepository) -> str:
    """Derive the only production Git remote from validated repository data."""

    return f"https://github.com/{repository.full_name}.git"


def render_pull_request_title(title: str) -> str:
    """Render a bounded, non-notifying single-line Pull Request title."""

    safe = _safe_markdown(title.replace("\r", " ").replace("\n", " "), 100)
    safe = " ".join(safe.split()) or "resolve issue"
    rendered = f"Sage: {safe}"
    if len(rendered) <= _MAX_PR_TITLE_CHARS:
        return rendered
    return f"{rendered[: _MAX_PR_TITLE_CHARS - 1].rstrip()}…"


def render_pull_request_body(
    invocation: GitHubInvocation,
    result: SolveResult,
    *,
    branch_name: str,
    current_base_sha: str,
    base_movement: BaseMovement,
) -> str:
    """Render bounded review metadata from allowlisted solve fields."""

    validate_github_url(invocation.actions_run.html_url)
    files = [
        _safe_code(path, _MAX_PATH_CHARS)
        for path in result.changed_files[:_MAX_CHANGED_FILES]
    ]
    file_lines = "\n".join(f"- `{path}`" for path in files) or "- None"
    if len(result.changed_files) > _MAX_CHANGED_FILES:
        file_lines += "\n- … additional paths omitted"
    uncertainty = [
        _safe_markdown(item, _MAX_UNCERTAINTY_CHARS)
        for item in result.remaining_uncertainty[:_MAX_UNCERTAINTY_ITEMS]
    ]
    uncertainty = [item for item in uncertainty if item]
    uncertainty_lines = (
        "\n".join(f"- {item}" for item in uncertainty)
        if uncertainty
        else "- None reported"
    )
    if len(result.remaining_uncertainty) > _MAX_UNCERTAINTY_ITEMS:
        uncertainty_lines += "\n- … additional items omitted"
    warning = ""
    if base_movement is BaseMovement.ADVANCED:
        warning = (
            "\n\n> [!WARNING]\n"
            "> The default branch advanced after this solve began. Sage did not "
            "rebase or alter the reviewed candidate; resolve conflicts and run "
            "current CI before merging."
        )

    return (
        f"{_PULL_REQUEST_MARKER_PREFIX}{invocation.issue.number} -->\n"
        "## Sage candidate\n\n"
        "This draft Pull Request was generated by Sage and requires human review.\n\n"
        f"Closes #{invocation.issue.number}\n\n"
        "## Summary\n\n"
        f"{_safe_markdown(result.summary, _MAX_SUMMARY_CHARS) or 'No summary provided.'}\n\n"
        "## Changed files\n\n"
        f"{file_lines}\n\n"
        "## Remaining uncertainty\n\n"
        f"{uncertainty_lines}\n\n"
        "## Provenance\n\n"
        f"- Branch: `{_safe_code(branch_name, 255)}`\n"
        f"- Original base: `{invocation.base_sha}`\n"
        f"- Current default-branch base: `{current_base_sha}`\n"
        f"- [Actions run]({invocation.actions_run.html_url})"
        f"{warning}"
    )


def _ensure_no_duplicate(
    invocation: GitHubInvocation,
    client: GitHubClient,
    *,
    branch_name: str,
) -> None:
    pull_requests = client.list_open_pull_requests(
        invocation.repository,
        head_branch=branch_name,
        base_branch=invocation.default_branch,
    )
    if pull_requests:
        raise GitHubPublicationError(
            "A matching Pull Request appeared before publication."
        )
    if client.get_branch(invocation.repository, branch_name) is not None:
        raise GitHubPublicationError(
            "The deterministic remote branch appeared before publication."
        )


def _create_or_reconcile_pull_request(
    invocation: GitHubInvocation,
    result: SolveResult,
    client: GitHubClient,
    *,
    branch_name: str,
    current_base_sha: str,
    base_movement: BaseMovement,
) -> GitHubPullRequestSnapshot:
    title = render_pull_request_title(invocation.issue.title)
    body = render_pull_request_body(
        invocation,
        result,
        branch_name=branch_name,
        current_base_sha=current_base_sha,
        base_movement=base_movement,
    )
    try:
        return client.create_pull_request(
            invocation.repository,
            title=title,
            head_branch=branch_name,
            base_branch=invocation.default_branch,
            body=body,
            draft=True,
        )
    except GitHubApiError as error:
        if not error.ambiguous and error.status_code != 422:
            raise _orphan_error(invocation, branch_name) from error

    pull_requests = client.list_open_pull_requests(
        invocation.repository,
        head_branch=branch_name,
        base_branch=invocation.default_branch,
    )
    if len(pull_requests) == 1:
        return pull_requests[0]
    raise _orphan_error(invocation, branch_name)


def _orphan_error(
    invocation: GitHubInvocation,
    branch_name: str,
) -> GitHubOrphanBranchError:
    branch_url = f"{invocation.repository.html_url}/tree/{branch_name}"
    validate_github_url(branch_url)
    return GitHubOrphanBranchError(
        "The Sage branch was preserved because its draft Pull Request could "
        "not be created or reconciled.",
        branch_url=branch_url,
    )


def _safe_markdown(value: str, limit: int) -> str:
    normalized = "".join(
        character
        if character in "\n\t" or (ord(character) >= 32 and ord(character) != 127)
        else "�"
        for character in value
    )
    normalized = normalized.replace("<!-- sage-", "&lt;!-- sage-")
    normalized = normalized.replace("@", "@\u200b").strip()
    if len(normalized) <= limit:
        return normalized
    return f"{normalized[: max(0, limit - 18)].rstrip()}… [truncated]"


def _safe_code(value: str, limit: int) -> str:
    return _safe_markdown(value, limit).replace("`", "\\`").replace("\n", " ")
