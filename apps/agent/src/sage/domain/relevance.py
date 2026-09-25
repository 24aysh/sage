"""Typed file-relevance judgments and auditable context selection."""

from typing import Literal, Protocol

from pydantic import BaseModel, ConfigDict, Field


class FileCandidate(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    id: str
    path: str = Field(min_length=1, max_length=500)
    evidence: str = Field(max_length=1800)


class RelevanceDecision(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", allow_inf_nan=False)
    model: str
    scores: dict[str, float]
    confidences: dict[str, float]
    input_tokens: int = Field(ge=0)
    output_tokens: int = Field(ge=0)


class RelevanceReport(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    policy: Literal["file-relevance-v1"] = "file-relevance-v1"
    mode: Literal["off", "shadow", "on"]
    status: str
    model: str
    candidate_files: tuple[str, ...] = ()
    retained_files: tuple[str, ...] = ()
    rejected_files: tuple[str, ...] = ()
    withheld_files: tuple[str, ...] = ()
    would_discard_files: tuple[str, ...] = ()
    candidate_items: int = 0
    discarded_items: int = 0
    withheld_items: int = 0
    context_omitted_items: int = 0
    context_omitted_files: tuple[str, ...] = ()
    scores: dict[str, float] = Field(default_factory=dict)
    confidences: dict[str, float] = Field(default_factory=dict)
    score_threshold: float
    confidence_threshold: float
    latency_ms: float = 0
    input_tokens: int | None = None
    output_tokens: int | None = None
    reason: str | None = None


class RelevanceProvider(Protocol):
    model: str
    capture: dict | None

    async def score_files(self, *, state: dict, candidates: tuple[FileCandidate, ...],
                          timeout: float) -> RelevanceDecision: ...
    async def aclose(self) -> None: ...


class RelevanceUnavailable(Exception):
    """Secret-safe optional-provider failure; never a model rejection."""

    def __init__(self, reason: str, *, permanent: bool = False) -> None:
        super().__init__(reason)
        self.reason, self.permanent = reason, permanent
