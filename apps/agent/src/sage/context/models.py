"""Typed context packets and deterministic retrieval evidence."""

from pydantic import BaseModel, ConfigDict, Field

from sage.domain.planning import RetrievalRequest


class RepositoryEvidence(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    request: RetrievalRequest
    content: str = Field(max_length=24_000)
    truncated: bool = False


class ContextPacket(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    version: int = 1
    role: str = Field(min_length=1, max_length=80)
    content: str = Field(min_length=1)
    character_count: int = Field(ge=1)
    digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    omitted_sections: tuple[str, ...] = Field(default=(), max_length=100)
