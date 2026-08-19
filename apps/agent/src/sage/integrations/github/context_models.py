"""Typed results for bounded GitHub Issue context assembly."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field, model_validator

from sage.integrations.github.config import MAX_CONTEXT_CHARS


class GitHubIssueContext(BaseModel):
    """One bounded task document ready for the existing solve workflow."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    issue_number: int = Field(gt=0)
    invocation_comment_id: int = Field(gt=0)
    markdown: str = Field(min_length=1, max_length=MAX_CONTEXT_CHARS, repr=False)
    included_comment_ids: tuple[int, ...] = ()
    history_truncated: bool

    @model_validator(mode="after")
    def validate_comment_ids(self) -> GitHubIssueContext:
        if any(comment_id < 1 for comment_id in self.included_comment_ids):
            raise ValueError("Included comment IDs must be positive.")
        if len(set(self.included_comment_ids)) != len(self.included_comment_ids):
            raise ValueError("Included comment IDs must be unique.")
        return self
