"""Agent and workflow output models."""

from enum import StrEnum
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field, model_validator

from sage.domain.admission import ClarificationPacket
from sage.domain.usage import RunProvenance


class SolveOutcome(StrEnum):
    """Provider-neutral terminal outcomes for V2 solve workflows."""

    COMPLETED = "completed"
    NO_CHANGE = "no_change"
    NEEDS_HUMAN_INFORMATION = "needs_human_information"
    NEEDS_HUMAN_DESIGN_DECISION = "needs_human_design_decision"
    NEEDS_MAINTAINER_REWRITE = "needs_maintainer_rewrite"
    HUMAN_REQUIRED = "human_required"
    HUMAN_REQUIRED_AFTER_START = "human_required_after_start"
    ENVIRONMENT_BLOCKED = "environment_blocked"
    UNSUPPORTED = "unsupported"
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
    clarification: ClarificationPacket | None = None
    provenance: RunProvenance | None = None

    @model_validator(mode="after")
    def validate_clarification(self) -> AgentFinalOutput:
        clarification_outcomes = {
            SolveOutcome.NEEDS_HUMAN_INFORMATION,
            SolveOutcome.NEEDS_HUMAN_DESIGN_DECISION,
        }
        if self.outcome in clarification_outcomes and self.clarification is None:
            raise ValueError("Clarification outcome requires a clarification packet.")
        if self.outcome not in clarification_outcomes and self.clarification is not None:
            raise ValueError("Only clarification outcomes may include a packet.")
        return self


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
    clarification: ClarificationPacket | None = None
    provenance: RunProvenance | None = None
