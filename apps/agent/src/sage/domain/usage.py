"""Provider-neutral model usage and provenance contracts."""

from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field


class ModelRole(StrEnum):
    SOLVER = "solver"
    REVIEWER = "reviewer"


class AttemptKind(StrEnum):
    PRIMARY = "primary"
    RETRY = "retry"
    FALLBACK = "fallback"
    SCHEMA_REPAIR = "schema_repair"


class ModelCallRecord(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    call_number: int = Field(ge=1)
    stage: str = Field(min_length=1, max_length=100)
    role: ModelRole
    attempt_kind: AttemptKind
    provider: str = Field(min_length=1, max_length=40)
    model: str = Field(min_length=1, max_length=120)
    input_tokens: int | None = Field(default=None, ge=0)
    output_tokens: int | None = Field(default=None, ge=0)
    cached_tokens: int | None = Field(default=None, ge=0)
    latency_ms: float = Field(ge=0)
    outcome: str = Field(min_length=1, max_length=80)
    retry_count: int = Field(default=0, ge=0, le=1)
    error_category: str | None = Field(default=None, max_length=80)
    status_code: int | None = Field(default=None, ge=100, le=599)
    request_id: str | None = Field(default=None, max_length=200)


class RunProvenance(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    graph_version: str = "v2"
    route: str = "single"
    profile: str = "constrained-cross-provider"
    calls: tuple[ModelCallRecord, ...] = ()
    solver_sessions: int = Field(default=0, ge=0)
    review_cycles: int = Field(default=0, ge=0)
