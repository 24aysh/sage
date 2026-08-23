"""V2 Solver structured result contract."""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field, model_validator

from sage.domain.planning import RetrievalRequest, VerificationHint


class SolverStatus(StrEnum):
    IMPLEMENTED = "implemented"
    NO_CHANGE = "no_change"
    BLOCKED = "blocked"
    NEED_CONTEXT = "need_context"
    HUMAN_DECISION_DISCOVERED = "human_decision_discovered"


class SolverResult(BaseModel):
    """Patch-first output envelope for implementation and repair calls."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    status: SolverStatus
    summary: str = Field(min_length=1, max_length=2_000)
    patch: str = Field(default="", max_length=96_000)
    changed_files_claimed: tuple[str, ...] = Field(default=(), max_length=30)
    expected_checks: tuple[VerificationHint, ...] = Field(default=(), max_length=4)
    uncertainties: tuple[str, ...] = Field(default=(), max_length=10)
    retrieval_requests: tuple[RetrievalRequest, ...] = Field(default=(), max_length=12)
    blocker_evidence: tuple[str, ...] = Field(default=(), max_length=10)

    @model_validator(mode="after")
    def validate_status_contract(self) -> SolverResult:
        if self.status is SolverStatus.IMPLEMENTED:
            if not self.patch.strip():
                raise ValueError("Implemented Solver result requires a patch.")
            if self.retrieval_requests:
                raise ValueError("Implemented Solver result cannot request context.")
        elif self.status is SolverStatus.NEED_CONTEXT:
            if not self.retrieval_requests:
                raise ValueError("Context request status requires retrieval requests.")
            if self.patch.strip():
                raise ValueError("Context request status cannot contain a patch.")
        elif self.patch.strip():
            raise ValueError("Only implemented status may contain a patch.")
        return self
