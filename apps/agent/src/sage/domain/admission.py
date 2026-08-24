"""Admission evidence and durable GitHub clarification contracts."""

from __future__ import annotations

import hashlib
import json
from datetime import datetime

from enum import StrEnum

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class ReadinessDisposition(StrEnum):
    """Validated routes from the read-only V2 Admission node."""

    READY = "READY"
    NEEDS_HUMAN_INFORMATION = "NEEDS_HUMAN_INFORMATION"
    NEEDS_HUMAN_DESIGN_DECISION = "NEEDS_HUMAN_DESIGN_DECISION"
    ENVIRONMENT_BLOCKED = "ENVIRONMENT_BLOCKED"
    UNSUPPORTED = "UNSUPPORTED"


class EvidenceSourceType(StrEnum):
    """Origin of one bounded, controller-verified evidence excerpt."""

    REPOSITORY = "repository"
    ISSUE = "issue"
    OFFICIAL_DOCUMENTATION = "official_documentation"
    WEB = "web"


class RepositoryEvidenceInput(BaseModel):
    """Model-selected repository region resolved by the trusted controller."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    evidence_id: str = Field(
        min_length=1,
        max_length=80,
        pattern=r"^[A-Za-z0-9_.-]+$",
    )
    path: str = Field(min_length=1, max_length=500)
    line_start: int = Field(default=1, ge=1)
    line_end: int | None = Field(default=None, ge=1)
    title: str = Field(min_length=1, max_length=300)

    @model_validator(mode="after")
    def validate_lines(self) -> RepositoryEvidenceInput:
        if self.line_end is not None and self.line_end < self.line_start:
            raise ValueError("Evidence line_end must not precede line_start.")
        return self


class ResearchEvidenceInput(BaseModel):
    """Reference to one controller-recorded same-run research result."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    evidence_id: str = Field(
        min_length=1,
        max_length=80,
        pattern=r"^[A-Za-z0-9_.-]+$",
    )
    result_id: str = Field(pattern=r"^research-[0-9]{3}$")


class EvidenceReference(BaseModel):
    """One immutable evidence excerpt with controller-derived provenance."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    evidence_id: str = Field(
        min_length=1,
        max_length=80,
        pattern=r"^[A-Za-z0-9_.-]+$",
    )
    source_type: EvidenceSourceType
    title: str = Field(min_length=1, max_length=300)
    locator: str = Field(min_length=1, max_length=2_048)
    excerpt: str = Field(min_length=1, max_length=4_000)
    content_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    line_start: int | None = Field(default=None, ge=1)
    line_end: int | None = Field(default=None, ge=1)
    detected_version: str | None = Field(default=None, max_length=100)
    fetched_at: datetime | None = None
    authoritative: bool = False


class AdmissionRequirement(BaseModel):
    """One Issue requirement and its supporting evidence."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    requirement_id: str = Field(
        min_length=1,
        max_length=80,
        pattern=r"^[A-Za-z0-9_.-]+$",
    )
    statement: str = Field(min_length=1, max_length=1_000)
    evidence_ids: tuple[str, ...] = Field(default=(), max_length=12)
    status: Literal["supported", "assumed", "blocked"]


class AdmissionContextSnapshot(BaseModel):
    """Compact base-repository evidence reused by Solver and Reviewer."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    version: Literal[1] = 1
    base_sha: str = Field(min_length=7, max_length=64)
    issue_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    summary: str = Field(min_length=1, max_length=2_000)
    requirements: tuple[AdmissionRequirement, ...] = Field(
        min_length=1,
        max_length=30,
    )
    relevant_paths: tuple[str, ...] = Field(default=(), max_length=40)
    relevant_symbols: tuple[str, ...] = Field(default=(), max_length=60)
    repository_conventions: tuple[str, ...] = Field(default=(), max_length=20)
    candidate_verification_commands: tuple[str, ...] = Field(
        default=(),
        max_length=10,
    )
    assumptions: tuple[str, ...] = Field(default=(), max_length=20)
    open_questions: tuple[str, ...] = Field(default=(), max_length=20)
    evidence: tuple[EvidenceReference, ...] = Field(default=(), max_length=40)
    created_at: datetime
    digest: str = Field(pattern=r"^[0-9a-f]{64}$")

    @model_validator(mode="after")
    def validate_references(self) -> AdmissionContextSnapshot:
        evidence_ids = [item.evidence_id for item in self.evidence]
        if len(evidence_ids) != len(set(evidence_ids)):
            raise ValueError("Admission evidence IDs must be unique.")
        known = set(evidence_ids)
        requirement_ids = [item.requirement_id for item in self.requirements]
        if len(requirement_ids) != len(set(requirement_ids)):
            raise ValueError("Admission requirement IDs must be unique.")
        for requirement in self.requirements:
            unknown = set(requirement.evidence_ids) - known
            if unknown:
                raise ValueError(
                    f"Requirement {requirement.requirement_id} references unknown "
                    "evidence: " + ", ".join(sorted(unknown))
                )
        if self.digest != self.calculate_digest():
            raise ValueError("Admission context digest does not match its content.")
        return self

    def calculate_digest(self) -> str:
        payload = self.model_dump(mode="json", exclude={"digest"})
        serialized = json.dumps(payload, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(serialized.encode("utf-8")).hexdigest()


class AdmissionContextSummary(BaseModel):
    """Upload-safe context metadata without repository or web excerpts."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    version: Literal[1] = 1
    base_sha: str = Field(min_length=7, max_length=64)
    issue_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    context_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    requirement_count: int = Field(ge=0)
    evidence_count: int = Field(ge=0)
    external_source_count: int = Field(ge=0)
    relevant_paths: tuple[str, ...] = Field(default=(), max_length=40)


class BlockingQuestion(BaseModel):
    """One consolidated human-owned decision or fact request."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    question: str = Field(min_length=1, max_length=1_000)
    why_blocking: str = Field(min_length=1, max_length=1_000)
    repository_evidence: tuple[str, ...] = Field(default=(), max_length=5)
    options: tuple[str, ...] = Field(default=(), max_length=4)
    proposed_default: str | None = Field(default=None, max_length=500)


class ClarificationPacket(BaseModel):
    """Durable bounded clarification stored in the Issue status comment."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    version: int = Field(default=1, ge=1, le=1)
    round: int = Field(ge=1, le=2)
    disposition: ReadinessDisposition
    summary: str = Field(min_length=1, max_length=1_500)
    questions: tuple[BlockingQuestion, ...] = Field(min_length=1, max_length=3)
    rerun_instruction: str = Field(min_length=1, max_length=500)

    @model_validator(mode="after")
    def validate_disposition(self) -> ClarificationPacket:
        if self.disposition not in {
            ReadinessDisposition.NEEDS_HUMAN_INFORMATION,
            ReadinessDisposition.NEEDS_HUMAN_DESIGN_DECISION,
        }:
            raise ValueError("Clarification requires a human-owned disposition.")
        return self


class AdmissionResult(BaseModel):
    """Structured terminal decision bound to one saved context snapshot."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    version: Literal[1] = 1
    disposition: ReadinessDisposition
    summary: str = Field(min_length=1, max_length=2_000)
    rationale: str = Field(min_length=1, max_length=2_000)
    confidence: float = Field(ge=0, le=1)
    context_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    clarification: ClarificationPacket | None = None

    @model_validator(mode="after")
    def validate_decision(self) -> AdmissionResult:
        human = {
            ReadinessDisposition.NEEDS_HUMAN_INFORMATION,
            ReadinessDisposition.NEEDS_HUMAN_DESIGN_DECISION,
        }
        if self.disposition in human:
            if self.clarification is None:
                raise ValueError("Human-required Admission needs clarification.")
            if self.clarification.disposition is not self.disposition:
                raise ValueError("Clarification disposition must match Admission.")
        elif self.clarification is not None:
            raise ValueError("Only human-required Admission may clarify.")
        return self
