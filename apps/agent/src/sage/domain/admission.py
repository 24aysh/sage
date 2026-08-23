"""Autonomy-admission contracts for Sage V2."""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field, model_validator

from sage.domain.planning import ExecutionPlan, RetrievalRequest


class ReadinessDisposition(StrEnum):
    """Typed intake outcomes that drive deterministic routing."""

    READY_AUTONOMOUS = "READY_AUTONOMOUS"
    NEEDS_REPOSITORY_CONTEXT = "NEEDS_REPOSITORY_CONTEXT"
    NEEDS_HUMAN_INFORMATION = "NEEDS_HUMAN_INFORMATION"
    NEEDS_HUMAN_DESIGN_DECISION = "NEEDS_HUMAN_DESIGN_DECISION"
    HUMAN_REQUIRED = "HUMAN_REQUIRED"
    ENVIRONMENT_BLOCKED = "ENVIRONMENT_BLOCKED"
    UNSUPPORTED = "UNSUPPORTED"


class DimensionStatus(StrEnum):
    """Small readiness status used for every explicit admission dimension."""

    SUFFICIENT = "sufficient"
    INSUFFICIENT = "insufficient"
    UNKNOWN = "unknown"
    NOT_APPLICABLE = "not_applicable"


class DimensionAssessment(BaseModel):
    """One readiness decision with bounded evidence."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    status: DimensionStatus
    evidence: str = Field(min_length=1, max_length=600)


class ReadinessDimensions(BaseModel):
    """All mandatory admission dimensions from the provisional design."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    objective_clarity: DimensionAssessment
    expected_behavior_clarity: DimensionAssessment
    acceptance_testability: DimensionAssessment
    scope_boundedness: DimensionAssessment
    repository_evidence_sufficiency: DimensionAssessment
    design_choice_closed: DimensionAssessment
    external_dependency_availability: DimensionAssessment
    sandbox_compatibility: DimensionAssessment
    permission_or_credential_independence: DimensionAssessment
    cross_repository_dependency: DimensionAssessment
    human_approval_dependency: DimensionAssessment


class BlockingQuestion(BaseModel):
    """One consolidated human-owned decision or fact request."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    question: str = Field(min_length=1, max_length=1_000)
    why_blocking: str = Field(min_length=1, max_length=1_000)
    repository_evidence: tuple[str, ...] = Field(default=(), max_length=5)
    options: tuple[str, ...] = Field(default=(), max_length=4)
    proposed_default: str | None = Field(default=None, max_length=500)


class IntakeResult(BaseModel):
    """Structured output of intake planning and autonomy classification."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    disposition: ReadinessDisposition
    dimensions: ReadinessDimensions
    rationale: str = Field(min_length=1, max_length=2_000)
    plan: ExecutionPlan | None = None
    retrieval_requests: tuple[RetrievalRequest, ...] = Field(default=(), max_length=12)
    blocking_questions: tuple[BlockingQuestion, ...] = Field(default=(), max_length=3)
    confidence: float | None = Field(default=None, ge=0, le=1)

    @model_validator(mode="after")
    def validate_disposition_contract(self) -> IntakeResult:
        if self.disposition is ReadinessDisposition.READY_AUTONOMOUS:
            if self.plan is None:
                raise ValueError("Ready intake requires an execution plan.")
            if self.retrieval_requests or self.blocking_questions:
                raise ValueError("Ready intake cannot contain blocking requests.")
        elif self.disposition is ReadinessDisposition.NEEDS_REPOSITORY_CONTEXT:
            if not self.retrieval_requests:
                raise ValueError("Repository-context intake requires retrieval requests.")
            if self.plan is not None or self.blocking_questions:
                raise ValueError("Repository-context intake cannot be ready or human-blocked.")
        elif self.disposition in {
            ReadinessDisposition.NEEDS_HUMAN_INFORMATION,
            ReadinessDisposition.NEEDS_HUMAN_DESIGN_DECISION,
        }:
            if not self.blocking_questions:
                raise ValueError("Human-needed intake requires blocking questions.")
            if self.plan is not None or self.retrieval_requests:
                raise ValueError("Human-needed intake cannot contain an executable plan.")
        elif self.plan is not None or self.retrieval_requests:
            raise ValueError("Blocked intake cannot contain a plan or retrieval requests.")
        return self


class ClarificationPacket(BaseModel):
    """Durable bounded clarification stored in the Issue status comment."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    version: int = Field(default=1, ge=1, le=1)
    round: int = Field(ge=1, le=2)
    disposition: ReadinessDisposition
    summary: str = Field(min_length=1, max_length=1_500)
    questions: tuple[BlockingQuestion, ...] = Field(min_length=1, max_length=3)
    rerun_instruction: str = Field(min_length=1, max_length=500)


class AutonomyContract(BaseModel):
    """Controller-frozen contract authorizing mutation after admission."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    task_summary: str = Field(min_length=1, max_length=2_000)
    acceptance_contract: tuple[dict[str, str], ...] = Field(min_length=1, max_length=12)
    safe_assumptions: tuple[str, ...] = Field(default=(), max_length=10)
    allowed_write_scopes: tuple[str, ...] = Field(min_length=1, max_length=20)
    forbidden_write_scopes: tuple[str, ...] = (".git/**", ".sage/runs/**")
    non_blocking_uncertainties: tuple[str, ...] = Field(default=(), max_length=10)
    available_capabilities: tuple[str, ...] = Field(default=(), max_length=20)
    forbidden_capabilities: tuple[str, ...] = Field(default=(), max_length=20)
    verification_expectations: tuple[str, ...] = Field(default=(), max_length=10)
    provider_profile: str = Field(min_length=1, max_length=100)
    model_calls_remaining: int = Field(ge=0, le=6)
    implementation_repairs_remaining: int = Field(ge=0, le=1)
    review_repairs_remaining: int = Field(ge=0, le=1)
    solver_context_expansions_remaining: int = Field(ge=0, le=1)
    base_sha: str = Field(pattern=r"^[0-9a-f]{40,64}$")
    plan_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
