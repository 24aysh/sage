"""Deterministic verification contracts for Sage."""

from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field


class VerificationSource(StrEnum):
    MANDATORY = "mandatory"
    CONFIGURED = "configured"
    DISCOVERED = "discovered"
    PLANNED = "planned"


class VerificationStatus(StrEnum):
    PASS = "pass"
    FAIL = "fail"
    UNAVAILABLE = "unavailable"
    TIMEOUT = "timeout"


class VerificationCommand(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    check_id: str = Field(min_length=1, max_length=80, pattern=r"^[A-Za-z0-9_.-]+$")
    command: str = Field(min_length=1, max_length=4_000)
    source: VerificationSource
    required: bool = True
    timeout_seconds: int = Field(default=60, ge=1, le=600)


class VerificationCheckResult(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    check: VerificationCommand
    status: VerificationStatus
    exit_code: int
    output_excerpt: str = Field(default="", max_length=24_000)
    fingerprint: str = Field(min_length=1, max_length=64)
    log_ref: str = Field(min_length=1, max_length=500)


class VerificationResult(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    status: VerificationStatus
    checks: tuple[VerificationCheckResult, ...] = Field(min_length=1, max_length=4)
    passing_check_count: int = Field(ge=0)
    candidate_diff_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    uncertainty: tuple[str, ...] = Field(default=(), max_length=10)
