"""Input and prepared-workspace domain models."""

from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class SolveRequest(BaseModel):
    """A request to solve one issue against one committed repository revision."""

    model_config = ConfigDict(frozen=True)

    repo_path: Path
    issue_path: Path
    base_ref: str = "HEAD"
    sandbox_image: str | None = None
    memory_repository_kind: Literal["github", "local"] = Field(
        default="local", exclude=True
    )
    memory_repository_key: str | None = Field(default=None, exclude=True)
    memory_repository_display_name: str | None = Field(default=None, exclude=True)


class PreparedRun(BaseModel):
    """Paths and revision details for an isolated run clone."""

    model_config = ConfigDict(frozen=True)

    run_id: str
    source_repo: Path
    run_dir: Path
    workspace_dir: Path
    base_ref: str
    base_sha: str
