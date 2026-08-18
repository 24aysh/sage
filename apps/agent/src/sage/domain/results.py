"""Agent and workflow output models."""

from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field


class AgentFinalOutput(BaseModel):
    """Provider-neutral structured output requested from an agent runtime."""

    summary: str
    changed_files_claimed: list[str] = Field(default_factory=list)
    remaining_uncertainty: list[str] = Field(default_factory=list)


class SolveResult(BaseModel):
    """Authoritative solve result derived from the isolated Git workspace."""

    model_config = ConfigDict(frozen=True)

    run_id: str
    base_sha: str
    summary: str
    remaining_uncertainty: list[str]
    changed_files: list[str]
    diff: str
    run_dir: Path
    workspace_dir: Path
