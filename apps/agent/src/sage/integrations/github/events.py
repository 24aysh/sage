"""GitHub issue-comment event loading and normalization."""

from __future__ import annotations

import json
import os
from collections.abc import Mapping
from pathlib import Path

from pydantic import BaseModel, ConfigDict, ValidationError

from sage.errors import GitHubEventError
from sage.integrations.github.commands import parse_command
from sage.integrations.github.models import (
    GITHUB_WEB_URL,
    GitHubActionsRun,
    GitHubActor,
    GitHubComment,
    GitHubInvocation,
    GitHubIssue,
    GitHubRepository,
)

MAX_EVENT_BYTES = 2_000_000


class _WebhookUser(BaseModel):
    model_config = ConfigDict(extra="ignore")

    login: str
    id: int


class _WebhookRepositoryOwner(BaseModel):
    model_config = ConfigDict(extra="ignore")

    login: str


class _WebhookRepository(BaseModel):
    model_config = ConfigDict(extra="ignore")

    id: int
    name: str
    full_name: str
    html_url: str
    default_branch: str
    owner: _WebhookRepositoryOwner


class _WebhookIssue(BaseModel):
    model_config = ConfigDict(extra="ignore")

    number: int
    title: str
    body: str | None = None
    html_url: str
    pull_request: dict[str, object] | None = None


class _WebhookComment(BaseModel):
    model_config = ConfigDict(extra="ignore")

    id: int
    body: str
    created_at: str
    html_url: str
    user: _WebhookUser


class _IssueCommentPayload(BaseModel):
    model_config = ConfigDict(extra="ignore")

    action: str
    issue: _WebhookIssue
    comment: _WebhookComment
    repository: _WebhookRepository
    sender: _WebhookUser


def load_issue_comment_event(
    environ: Mapping[str, str] | None = None,
) -> GitHubInvocation:
    """Load a bounded event file from the trusted Actions environment."""

    values = os.environ if environ is None else environ
    event_path = Path(_required_environment(values, "GITHUB_EVENT_PATH"))
    try:
        size = event_path.stat().st_size
        if size > MAX_EVENT_BYTES:
            raise GitHubEventError(
                f"GitHub event payload exceeds {MAX_EVENT_BYTES} bytes."
            )
        payload = json.loads(event_path.read_text(encoding="utf-8"))
    except GitHubEventError:
        raise
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
        raise GitHubEventError("Unable to read the GitHub event payload.") from error

    return parse_issue_comment_event(payload, values)


def parse_issue_comment_event(
    payload: object,
    environ: Mapping[str, str],
) -> GitHubInvocation:
    """Validate and normalize an `issue_comment.created` event."""

    if _required_environment(environ, "GITHUB_EVENT_NAME") != "issue_comment":
        raise GitHubEventError("Expected a GitHub issue_comment event.")

    server_url = _required_environment(environ, "GITHUB_SERVER_URL").rstrip("/")
    if server_url != GITHUB_WEB_URL:
        raise GitHubEventError("Sage V1.0 supports GitHub.com events only.")

    try:
        parsed = _IssueCommentPayload.model_validate(payload)
    except ValidationError as error:
        raise GitHubEventError(
            "GitHub event payload is missing required fields."
        ) from error

    if parsed.action != "created":
        raise GitHubEventError("Expected a newly created GitHub issue comment.")

    if (
        parsed.comment.user.id != parsed.sender.id
        or parsed.comment.user.login.casefold() != parsed.sender.login.casefold()
    ):
        raise GitHubEventError(
            "GitHub event sender does not match the comment author."
        )

    repository = _normalize_repository(parsed.repository, environ)
    run_id = _positive_environment_integer(environ, "GITHUB_RUN_ID")
    run_attempt = _positive_environment_integer(environ, "GITHUB_RUN_ATTEMPT")
    is_pull_request = parsed.issue.pull_request is not None

    try:
        return GitHubInvocation(
            repository=repository,
            issue=GitHubIssue(
                number=parsed.issue.number,
                title=parsed.issue.title,
                body=parsed.issue.body or "",
                html_url=parsed.issue.html_url,
                is_pull_request=is_pull_request,
            ),
            actor=GitHubActor(
                login=parsed.comment.user.login,
                user_id=parsed.comment.user.id,
            ),
            comment=GitHubComment(
                comment_id=parsed.comment.id,
                body=parsed.comment.body,
                created_at=parsed.comment.created_at,
                html_url=parsed.comment.html_url,
            ),
            command=parse_command(parsed.comment.body),
            default_branch=parsed.repository.default_branch,
            base_sha=_required_environment(environ, "GITHUB_SHA"),
            actions_run=GitHubActionsRun(
                run_id=run_id,
                attempt=run_attempt,
                html_url=f"{repository.html_url}/actions/runs/{run_id}",
            ),
        )
    except ValidationError as error:
        raise GitHubEventError("GitHub event fields failed validation.") from error


def _normalize_repository(
    repository: _WebhookRepository,
    environ: Mapping[str, str],
) -> GitHubRepository:
    expected_full_name = f"{repository.owner.login}/{repository.name}"
    environment_full_name = _required_environment(environ, "GITHUB_REPOSITORY")
    if repository.full_name.casefold() != expected_full_name.casefold():
        raise GitHubEventError("GitHub repository identity is inconsistent.")
    if environment_full_name.casefold() != repository.full_name.casefold():
        raise GitHubEventError(
            "GitHub event repository does not match the Actions environment."
        )

    try:
        return GitHubRepository(
            owner=repository.owner.login,
            name=repository.name,
            repository_id=repository.id,
            html_url=repository.html_url,
        )
    except ValidationError as error:
        raise GitHubEventError("GitHub repository fields failed validation.") from error


def _positive_environment_integer(
    environ: Mapping[str, str],
    name: str,
) -> int:
    raw_value = _required_environment(environ, name)
    try:
        value = int(raw_value)
    except ValueError as error:
        raise GitHubEventError(f"{name} must be a positive integer.") from error
    if value < 1 or str(value) != raw_value:
        raise GitHubEventError(f"{name} must be a positive integer.")
    return value


def _required_environment(environ: Mapping[str, str], name: str) -> str:
    value = environ.get(name, "")
    if not value or value != value.strip():
        raise GitHubEventError(
            f"{name} is required and must not have surrounding whitespace."
        )
    return value
