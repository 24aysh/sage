"""Provider-neutral contracts for optional, rebuildable code vectors."""

from __future__ import annotations

import hashlib
import math
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Protocol

from pydantic import BaseModel, ConfigDict


class MemoryVectorError(RuntimeError):
    """Safe boundary failure; messages must contain no provider payloads."""


class EmbeddingIdentity(BaseModel):
    model_config = ConfigDict(frozen=True)

    provider: str = "google"
    model: str = "gemini-embedding-2"
    dimensions: int = 3072
    recipe: str = "legion-node-v1-code-query-v1"
    endpoint: str = "https://generativelanguage.googleapis.com"

    @property
    def fingerprint(self) -> str:
        return hashlib.sha256(self.model_dump_json().encode()).hexdigest()[:24]


class VectorUsage(BaseModel):
    """Counts are separate from Solver calls; absent token usage is unknown."""

    document_calls: int = 0
    query_calls: int = 0
    retries: int = 0
    input_tokens: int | None = None
    qdrant_operations: int = 0


class VectorStatus(BaseModel):
    model_config = ConfigDict(frozen=True)

    status: str = "disabled"
    model: str | None = None
    dimensions: int | None = None
    generation: str | None = None
    eligible: int = 0
    embedded: int = 0
    reused: int = 0
    removed: int = 0
    reason: str | None = None
    duration_ms: float = 0
    usage: VectorUsage | None = None


@dataclass(frozen=True)
class VectorPoint:
    point_id: str
    qualified_name: str
    text_hash: str
    vector: list[float]
    generation: str


class EmbeddingProvider(Protocol):
    identity: EmbeddingIdentity
    usage: VectorUsage

    def embed(self, text: str, *, query: bool, timeout: float) -> list[float]: ...


class VectorStore(Protocol):
    def get(self, ids: Sequence[str]) -> dict[str, VectorPoint]: ...
    def upsert(self, points: Sequence[VectorPoint]) -> None: ...
    def search(self, vector: list[float], *, generation: str, limit: int) -> list[tuple[str, float]]: ...
    def close(self) -> None: ...


def validate_vector(vector: Sequence[float], dimensions: int) -> list[float]:
    if len(vector) != dimensions or not all(math.isfinite(x) for x in vector):
        raise MemoryVectorError("Invalid embedding dimension or non-finite values.")
    if not any(vector):
        raise MemoryVectorError("Embedding is a zero vector.")
    return list(vector)
