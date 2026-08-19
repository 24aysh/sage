"""Trusted GitHub controller configuration."""

from __future__ import annotations

import os
from collections.abc import Mapping
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from sage.errors import GitHubConfigurationError

GITHUB_API_URL = "https://api.github.com"
MAX_CONTEXT_COMMENTS = 100
MAX_COMMENT_PAGES = 20
MIN_CONTEXT_CHARS = 2_000
MAX_CONTEXT_CHARS = 200_000


class GitHubSettings(BaseModel):
    """Bounded GitHub settings loaded independently from model configuration."""

    model_config = ConfigDict(frozen=True)

    github_token: str = Field(repr=False, min_length=1)
    api_url: Literal["https://api.github.com"] = GITHUB_API_URL
    api_timeout_seconds: int = Field(default=30, ge=1, le=120)
    max_comments: int = Field(default=20, ge=0, le=MAX_CONTEXT_COMMENTS)
    max_comment_pages: int = Field(default=5, ge=1, le=MAX_COMMENT_PAGES)
    max_context_chars: int = Field(
        default=40_000,
        ge=MIN_CONTEXT_CHARS,
        le=MAX_CONTEXT_CHARS,
    )

    @classmethod
    def from_env(
        cls,
        environ: Mapping[str, str] | None = None,
    ) -> GitHubSettings:
        """Load GitHub settings from the trusted controller environment."""

        values = os.environ if environ is None else environ
        token = values.get("SAGE_GITHUB_TOKEN", "").strip()
        if not token:
            raise GitHubConfigurationError("SAGE_GITHUB_TOKEN is required.")

        try:
            return cls(
                github_token=token,
                api_timeout_seconds=values.get(
                    "SAGE_GITHUB_API_TIMEOUT_SECONDS",
                    "30",
                ),
                max_comments=values.get("SAGE_GITHUB_MAX_COMMENTS", "20"),
                max_comment_pages=values.get(
                    "SAGE_GITHUB_MAX_COMMENT_PAGES",
                    "5",
                ),
                max_context_chars=values.get(
                    "SAGE_GITHUB_MAX_CONTEXT_CHARS",
                    "40000",
                ),
            )
        except ValidationError as error:
            raise GitHubConfigurationError(
                f"Invalid GitHub configuration: {error}"
            ) from error
