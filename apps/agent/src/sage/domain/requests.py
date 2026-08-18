"""Input and prepared-workspace domain models."""

from pathlib import Path

from pydantic import BaseModel, ConfigDict


class SolveRequest(BaseModel):
    """A request to solve one issue against one committed repository revision."""

    model_config = ConfigDict(frozen=True)

    repo_path: Path
    issue_path: Path
    base_ref: str = "HEAD"
    sandbox_image: str | None = None


class PreparedRun(BaseModel):
    """Paths and revision details for an isolated run clone."""

    model_config = ConfigDict(frozen=True)

    run_id: str
    source_repo: Path
    run_dir: Path
    workspace_dir: Path
    base_ref: str
    base_sha: str
