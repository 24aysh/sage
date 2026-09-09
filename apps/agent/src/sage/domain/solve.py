"""Inputs, prepared workspace, and terminal contracts for one solve use case."""

from enum import StrEnum
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field

from sage.domain.memory import LegionMemoryRunArtifact
from sage.domain.usage import RunProvenance


class SolveRequest(BaseModel):
    """A request to solve one issue against one committed repository revision."""

    model_config = ConfigDict(frozen=True)

    repo_path: Path
    issue_path: Path
    base_ref: str = "HEAD"
    sandbox_image: str | None = None
    memory_file: Path | None = None


class PreparedRun(BaseModel):
    """Paths and revision details for an isolated run clone."""

    model_config = ConfigDict(frozen=True)

    run_id: str
    source_repo: Path
    run_dir: Path
    workspace_dir: Path
    base_ref: str
    base_sha: str


class SolveOutcome(StrEnum):
    """Provider-neutral terminal outcomes for solve workflows."""

    COMPLETED = "completed"
    NO_CHANGE = "no_change"
    HUMAN_REQUIRED_AFTER_START = "human_required_after_start"
    ENVIRONMENT_BLOCKED = "environment_blocked"
    UNRESOLVED = "unresolved"
    PROVIDER_UNAVAILABLE = "provider_unavailable"
    RATE_LIMITED = "rate_limited"
    BUDGET_EXHAUSTED = "budget_exhausted"
    VERIFICATION_FAILED = "verification_failed"
    REVIEW_FAILED = "review_failed"
    INVALID_MODEL_OUTPUT = "invalid_model_output"


class AgentFinalOutput(BaseModel):
    """Provider-neutral terminal output returned by solve coordination."""

    model_config = ConfigDict(extra="forbid")

    summary: str = Field(min_length=1, max_length=2_000)
    changed_files_claimed: list[str] = Field(default_factory=list)
    remaining_uncertainty: list[str] = Field(default_factory=list)
    outcome: SolveOutcome = SolveOutcome.COMPLETED
    provenance: RunProvenance | None = None


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
    outcome: SolveOutcome = SolveOutcome.COMPLETED
    provenance: RunProvenance | None = None
    memory: LegionMemoryRunArtifact | None = None
