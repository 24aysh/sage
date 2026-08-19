"""Typed results returned by the GitHub authorization gate."""

from enum import StrEnum
from typing import Self

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from sage.integrations.github.models import (
    GIT_OBJECT_ID_PATTERN,
    validate_branch_name,
    validate_github_url,
)


class GateOutcome(StrEnum):
    """Expected decisions made before model configuration is loaded."""

    ACCEPTED = "accepted"
    EXISTING_PULL_REQUEST = "existing_pull_request"
    UNAUTHORIZED = "unauthorized"
    IGNORED = "ignored"
    BLOCKED_EXISTING_BRANCH = "blocked_existing_branch"


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
                raise ValueError(
                    "Ignored invocations must not have a status comment."
                )
        elif self.status_comment_id is None:
            raise ValueError("Supported invocations require a status comment.")
        if self.outcome is GateOutcome.EXISTING_PULL_REQUEST:
            if self.existing_pull_request_url is None:
                raise ValueError("Existing Pull Request outcome requires its URL.")
        elif self.existing_pull_request_url is not None:
            raise ValueError("Pull Request URL is only valid for a duplicate outcome.")
        return self
