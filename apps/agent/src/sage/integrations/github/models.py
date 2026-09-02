"""Validated GitHub issue-comment event models."""

from __future__ import annotations

import re
from datetime import datetime
from enum import StrEnum
from typing import Self
from urllib.parse import urlsplit

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    field_validator,
    model_validator,
)

GITHUB_WEB_URL = "https://github.com"
GIT_OBJECT_ID_PATTERN = r"^(?:[0-9a-f]{40}|[0-9a-f]{64})$"
_INVALID_REF_CHARACTERS = frozenset(" ~^:?*[\\")
_OWNER_PATTERN = re.compile(r"^[A-Za-z0-9](?:[A-Za-z0-9-]{0,37}[A-Za-z0-9])?$")
_REPOSITORY_PATTERN = re.compile(r"^[A-Za-z0-9_.-]+$")
_ACTOR_PATTERN = re.compile(
    r"^[A-Za-z0-9](?:[A-Za-z0-9-]{0,98}[A-Za-z0-9])?(?:\[bot\])?$"
)


class SageCommand(StrEnum):
    """Semantic actions accepted from GitHub Issue comments."""

    SOLVE = "solve"


class GateOutcome(StrEnum):
    """Expected decisions made before model configuration is loaded."""

    ACCEPTED = "accepted"
    EXISTING_PULL_REQUEST = "existing_pull_request"
    UNAUTHORIZED = "unauthorized"
    IGNORED = "ignored"
    BLOCKED_EXISTING_BRANCH = "blocked_existing_branch"


def issue_branch_name(issue_number: int) -> str:
    """Return the single Sage branch allocated to an Issue."""

    if issue_number < 1:
        raise ValueError("Issue number must be positive.")
    return f"sage/issue-{issue_number}"


class GateResult(BaseModel):
    """Validated non-secret values shared with the later solve job."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    outcome: GateOutcome
    should_run: bool
    base_sha: str = Field(pattern=GIT_OBJECT_ID_PATTERN)
    base_branch: str = Field(min_length=1, max_length=255)
    issue_number: int = Field(gt=0)
    status_comment_id: int | None = Field(default=None, gt=0)
    existing_pull_request_url: str | None = Field(
        default=None,
        min_length=1,
        max_length=2_048,
    )

    @field_validator("base_branch")
    @classmethod
    def validate_base_branch(cls, value: str) -> str:
        return validate_branch_name(value)

    @field_validator("existing_pull_request_url")
    @classmethod
    def validate_pull_request_url(cls, value: str | None) -> str | None:
        return validate_github_url(value) if value is not None else None

    @model_validator(mode="after")
    def validate_decision(self) -> Self:
        if self.should_run != (self.outcome is GateOutcome.ACCEPTED):
            raise ValueError("Only an accepted gate result may run the solver.")
        if self.outcome is GateOutcome.IGNORED:
            if self.status_comment_id is not None:
                raise ValueError("Ignored invocations must not have a status comment.")
        elif self.status_comment_id is None:
            raise ValueError("Supported invocations require a status comment.")
        if self.outcome is GateOutcome.EXISTING_PULL_REQUEST:
            if self.existing_pull_request_url is None:
                raise ValueError("Existing Pull Request outcome requires its URL.")
        elif self.existing_pull_request_url is not None:
            raise ValueError("Pull Request URL is only valid for a duplicate outcome.")
        return self


def validate_github_timestamp(value: datetime) -> datetime:
    """Require the timezone information present on GitHub timestamps."""

    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("GitHub comment timestamp must include a timezone.")
    return value


class GitHubRepository(BaseModel):
    """Validated target repository identity."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    owner: str = Field(min_length=1, max_length=39)
    name: str = Field(min_length=1, max_length=100)
    repository_id: int = Field(gt=0)
    html_url: str = Field(min_length=1, max_length=2_048)

    @field_validator("owner")
    @classmethod
    def validate_owner(cls, value: str) -> str:
        if _OWNER_PATTERN.fullmatch(value) is None:
            raise ValueError("Repository owner is not a valid GitHub.com account.")
        return value

    @field_validator("name")
    @classmethod
    def validate_name(cls, value: str) -> str:
        if value in {".", ".."} or _REPOSITORY_PATTERN.fullmatch(value) is None:
            raise ValueError("Repository name is not a safe GitHub.com path segment.")
        return value

    @field_validator("html_url")
    @classmethod
    def validate_html_url(cls, value: str) -> str:
        return validate_github_url(value)

    @model_validator(mode="after")
    def validate_repository_url(self) -> Self:
        expected = f"{GITHUB_WEB_URL}/{self.owner}/{self.name}"
        if self.html_url.rstrip("/") != expected:
            raise ValueError("Repository URL does not match its owner and name.")
        return self

    @property
    def full_name(self) -> str:
        """Return GitHub's owner/name repository identifier."""

        return f"{self.owner}/{self.name}"


class GitHubIssue(BaseModel):
    """Validated source issue details."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    number: int = Field(gt=0)
    title: str = Field(min_length=1, max_length=1_024)
    body: str = Field(default="", max_length=1_000_000)
    html_url: str = Field(min_length=1, max_length=2_048)
    is_pull_request: bool = False

    @field_validator("html_url")
    @classmethod
    def validate_html_url(cls, value: str) -> str:
        return validate_github_url(value)


class GitHubActor(BaseModel):
    """Validated user who created the invoking comment."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    login: str = Field(min_length=1, max_length=100)
    user_id: int = Field(gt=0)

    @field_validator("login")
    @classmethod
    def validate_login(cls, value: str) -> str:
        return validate_github_login(value)


class GitHubComment(BaseModel):
    """Validated issue comment that caused the workflow event."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    comment_id: int = Field(gt=0)
    body: str = Field(max_length=1_000_000)
    created_at: datetime
    html_url: str = Field(min_length=1, max_length=2_048)

    @field_validator("created_at")
    @classmethod
    def validate_created_at(cls, value: datetime) -> datetime:
        return validate_github_timestamp(value)

    @field_validator("html_url")
    @classmethod
    def validate_html_url(cls, value: str) -> str:
        return validate_github_url(value)


class GitHubActionsRun(BaseModel):
    """Validated Actions run identity used for status links."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    run_id: int = Field(gt=0)
    attempt: int = Field(gt=0)
    html_url: str = Field(min_length=1, max_length=2_048)

    @field_validator("html_url")
    @classmethod
    def validate_html_url(cls, value: str) -> str:
        return validate_github_url(value)


class GitHubInvocation(BaseModel):
    """Normalized subset of one GitHub issue-comment workflow event."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    repository: GitHubRepository
    issue: GitHubIssue
    actor: GitHubActor
    comment: GitHubComment
    command: SageCommand | None
    default_branch: str = Field(min_length=1, max_length=255)
    base_sha: str = Field(pattern=GIT_OBJECT_ID_PATTERN)
    actions_run: GitHubActionsRun

    @field_validator("default_branch")
    @classmethod
    def validate_default_branch(cls, value: str) -> str:
        return validate_branch_name(value)

    @model_validator(mode="after")
    def validate_related_urls(self) -> Self:
        issue_kind = "pull" if self.issue.is_pull_request else "issues"
        expected_issue_url = (
            f"{self.repository.html_url}/{issue_kind}/{self.issue.number}"
        )
        if self.issue.html_url.rstrip("/") != expected_issue_url:
            raise ValueError("Issue URL does not match the repository and number.")

        expected_comment_prefix = f"{expected_issue_url}#issuecomment-"
        expected_comment_url = (
            f"{expected_comment_prefix}{self.comment.comment_id}"
        )
        if self.comment.html_url != expected_comment_url:
            raise ValueError("Comment URL does not match the issue.")

        expected_run_url = (
            f"{self.repository.html_url}/actions/runs/{self.actions_run.run_id}"
        )
        if self.actions_run.html_url.rstrip("/") != expected_run_url:
            raise ValueError("Actions run URL does not match the repository and run.")
        return self


def validate_github_url(value: str) -> str:
    """Return a URL only when it targets GitHub.com over trusted HTTPS."""

    parsed = urlsplit(value)
    if (
        parsed.scheme != "https"
        or parsed.hostname != "github.com"
        or parsed.port is not None
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
    ):
        raise ValueError("Expected a trusted GitHub.com HTTPS URL.")
    return value


def validate_github_login(value: str) -> str:
    """Return a GitHub.com login only when it is safe structured metadata."""

    if _ACTOR_PATTERN.fullmatch(value) is None:
        raise ValueError("GitHub actor login is invalid.")
    return value


def validate_branch_name(value: str) -> str:
    """Return a branch name only when it is safe as one Git ref argument."""

    if (
        not value
        or len(value) > 255
        or value != value.strip()
        or value in {"@", ".", ".."}
    ):
        raise ValueError("Branch name is invalid.")
    if value.startswith(("-", ".", "/")) or value.endswith((".", "/")):
        raise ValueError("Branch name is invalid.")
    if (
        value.endswith(".lock")
        or ".." in value
        or "@{" in value
        or "//" in value
    ):
        raise ValueError("Branch name is invalid.")
    if any(
        character in _INVALID_REF_CHARACTERS
        or ord(character) < 32
        or ord(character) == 127
        for character in value
    ):
        raise ValueError("Branch name is invalid.")
    return value
