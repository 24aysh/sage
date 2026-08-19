"""Typed GitHub REST API results used by controller services."""

from __future__ import annotations

from datetime import datetime
from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from sage.integrations.github.models import (
    GIT_OBJECT_ID_PATTERN,
    validate_github_url,
)


class GitHubPermission(BaseModel):
    """GitHub's calculated legacy repository permission."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    permission: Literal["admin", "write", "read", "none"]
    role_name: str | None = Field(default=None, max_length=100)


class GitHubIssueSnapshot(BaseModel):
    """Current GitHub Issue contents used for task context."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    number: int = Field(gt=0)
    title: str = Field(min_length=1, max_length=1_024)
    body: str = Field(default="", max_length=1_000_000)
    html_url: str = Field(min_length=1, max_length=2_048)

    @field_validator("html_url")
    @classmethod
    def validate_html_url(cls, value: str) -> str:
        return validate_github_url(value)


class GitHubIssueCommentSnapshot(BaseModel):
    """One normalized GitHub Issue comment."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    comment_id: int = Field(gt=0)
    body: str = Field(default="", max_length=1_000_000)
    author_login: str = Field(min_length=1, max_length=100)
    created_at: datetime
    html_url: str = Field(min_length=1, max_length=2_048)

    @field_validator("created_at")
    @classmethod
    def validate_created_at(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("GitHub comment timestamp must include a timezone.")
        return value

    @field_validator("html_url")
    @classmethod
    def validate_html_url(cls, value: str) -> str:
        return validate_github_url(value)


class GitHubCommentPage(BaseModel):
    """One bounded page of Issue comments and pagination metadata."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    comments: tuple[GitHubIssueCommentSnapshot, ...]
    page: int = Field(gt=0)
    next_page: int | None = Field(default=None, gt=0)
    last_page: int = Field(gt=0)

    @model_validator(mode="after")
    def validate_pages(self) -> Self:
        if self.last_page < self.page:
            raise ValueError("Last page cannot precede the current page.")
        if self.next_page is not None and self.next_page <= self.page:
            raise ValueError("Next page must follow the current page.")
        if self.next_page is not None and self.next_page > self.last_page:
            raise ValueError("Next page cannot follow the last page.")
        return self


class GitHubBranchSnapshot(BaseModel):
    """Current commit at a GitHub repository branch."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    name: str = Field(min_length=1, max_length=255)
    sha: str = Field(pattern=GIT_OBJECT_ID_PATTERN)
    protected: bool


class GitHubPullRequestSnapshot(BaseModel):
    """Pull Request fields needed for deduplication and publication."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    number: int = Field(gt=0)
    html_url: str = Field(min_length=1, max_length=2_048)
    state: Literal["open", "closed"]
    draft: bool
    head_ref: str = Field(min_length=1, max_length=255)
    base_ref: str = Field(min_length=1, max_length=255)

    @field_validator("html_url")
    @classmethod
    def validate_html_url(cls, value: str) -> str:
        return validate_github_url(value)
