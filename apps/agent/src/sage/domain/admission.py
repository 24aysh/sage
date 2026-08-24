"""Durable clarification contracts shared with GitHub status rendering."""

from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field


class ReadinessDisposition(StrEnum):
    """Historical clarification categories retained for status compatibility."""

    NEEDS_HUMAN_INFORMATION = "NEEDS_HUMAN_INFORMATION"
    NEEDS_HUMAN_DESIGN_DECISION = "NEEDS_HUMAN_DESIGN_DECISION"


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
