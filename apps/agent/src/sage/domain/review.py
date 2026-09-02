"""Semantic review contracts for Sage."""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field, model_validator


class ReviewVerdict(StrEnum):
    PASS = "pass"
    FAIL = "fail"
    UNCERTAIN = "uncertain"


class ReviewFailureType(StrEnum):
    IMPLEMENTATION = "implementation"
    PLANNING = "planning"
    ENVIRONMENT = "environment"
    REQUIREMENT_AMBIGUITY = "requirement_ambiguity"
    VERIFICATION = "verification"
    MERGE = "merge"
    UNSOLVED = "unsolved"


class ReviewFinding(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    finding_id: str = Field(min_length=1, max_length=80, pattern=r"^[A-Za-z0-9_.-]+$")
    criterion_ids: tuple[str, ...] = Field(default=(), max_length=12)
    evidence: str = Field(min_length=1, max_length=1_500)
    required_outcome: str = Field(min_length=1, max_length=1_000)
    path: str | None = Field(default=None, max_length=500)
    line: int | None = Field(default=None, ge=1)


class CriterionResult(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    criterion_id: str = Field(min_length=1, max_length=40)
    satisfied: bool
    evidence: str = Field(min_length=1, max_length=1_000)


class ReviewResult(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    verdict: ReviewVerdict
    failure_type: ReviewFailureType | None = None
    blocking_findings: tuple[ReviewFinding, ...] = Field(default=(), max_length=10)
    optional_findings: tuple[ReviewFinding, ...] = Field(default=(), max_length=10)
    criterion_results: tuple[CriterionResult, ...] = Field(default=(), max_length=12)
    evidence: tuple[str, ...] = Field(default=(), max_length=10)
    confidence: float = Field(ge=0, le=1)
    uncertainty: tuple[str, ...] = Field(default=(), max_length=10)

    @model_validator(mode="after")
    def validate_verdict(self) -> ReviewResult:
        if self.verdict is ReviewVerdict.PASS:
            if self.blocking_findings or self.failure_type is not None:
                raise ValueError("Passing review cannot contain blocking failure data.")
        elif self.verdict is ReviewVerdict.FAIL:
            if not self.blocking_findings or self.failure_type is None:
                raise ValueError("Failing review requires a type and blocking finding.")
        return self
