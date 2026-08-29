"""Agent and workflow output models."""

from enum import StrEnum
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field

from sage.domain.usage import RunProvenance


class SolveOutcome(StrEnum):
    """Provider-neutral terminal outcomes for V2 solve workflows."""

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
    """Provider-neutral structured output requested from an agent runtime."""

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
